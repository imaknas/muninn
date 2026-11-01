"""Cloud backend: Claude through the Anthropic API, with the user's own API key.

The key is read from where config says (keys.py) on every request and given to the client explicitly, so
the SDK never falls back to a key, token or profile it finds on the machine by itself. A rejected key is a
lapsed login (CloudLoginLost), as with the CLI; a reply cut off at max_tokens, or declined, is a failure,
not a reply. Rate limits and overloads are retried by the SDK (max_retries).

How hard a model thinks before it answers is set per tier (`effort`); the models think on their own by default,
and on an API key the thinking is paid for, so the fast tier, which most requests use, thinks least unless config
says otherwise.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

import anthropic

from muninn.adapters.llm.content import content_blocks
from muninn.adapters.llm.keys import KeySource, key_source
from muninn.core.language import Language
from muninn.core.models import Attachment
from muninn.core.ports import Capabilities, CloudLoginLost, Reply
from muninn.llm_config import ProviderContext, ProviderSpec

API_URL = "https://api.anthropic.com"
MAX_TOKENS = 16000
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = {"fast": "low"}  # the other tiers think as their model does by default

ClientFor = Callable[[str], Any]  # key → a client with .messages.create and .models.retrieve


class AnthropicApiBackend:
    is_local = False
    capabilities = Capabilities(images=True, pdf=True)  # photos and PDFs go to the model as they are

    def __init__(self, name: str, label: str, model: str, key: KeySource, client_for: ClientFor,
                 timeout: float = 120.0, max_tokens: int = MAX_TOKENS, effort: str | None = None, *, words: Language):
        self.name = f"{name}:{model}"
        self.label = label
        self.login_hint = words("llm.put_key", label=label, where=key.where)
        self._model = model
        self._key = key
        self._client_for = client_for
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._effort = effort

    def available(self) -> bool:
        return True  # a missing key is not skipped quietly: complete() says the login lapsed

    def _client(self):
        return self._client_for(self._key.read())

    def check(self) -> str:
        """Checks the key and the model with the API, without a model call."""
        try:
            info = self._client().models.retrieve(self._model, timeout=30)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise CloudLoginLost(f"API key rejected ({e.status_code})") from e
        return getattr(info, "display_name", None) or self._model

    def complete(self, system: str, prompt: str, *, json_mode: bool = False, attachments: Sequence[Attachment] = ()) -> str:
        client = self._client()  # before the call: no key is a lapsed login, not a failure to stand in for
        try:
            msg = client.messages.create(
                model=self._model, max_tokens=self._max_tokens, system=system,
                messages=[{"role": "user", "content": content_blocks(prompt, attachments) if attachments else prompt}],
                timeout=self._timeout, **({"output_config": {"effort": self._effort}} if self._effort else {}),
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise CloudLoginLost(f"API key rejected ({e.status_code})") from e
        if msg.stop_reason == "max_tokens":
            raise RuntimeError(f"reply cut off at max_tokens ({self._max_tokens})")
        if msg.stop_reason == "refusal":
            raise RuntimeError("the model declined to answer")
        usage = getattr(msg, "usage", None)
        return Reply("".join(b.text for b in msg.content if b.type == "text").strip(),
                     getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))


class AnthropicApiProvider:
    """The Anthropic API as a cloud provider (llm.providers.<name>.impl: anthropic): its key, the API's URL
    (base_url, for a compatible gateway) and how long a reply may be (max_tokens)."""

    default_models = {"fast": "claude-haiku-5-5", "standard": "claude-sonnet-5-5", "strong": "claude-opus-5-5"}
    location = "cloud"
    counts_tokens = True

    def __init__(self, name: str, key: KeySource, base_url: str = API_URL, max_tokens: int = MAX_TOKENS,
                 client_for: ClientFor | None = None, efforts: Mapping[str, str] | None = None, *, words: Language):
        """`efforts`: model → how hard it thinks (effort_by_model)."""
        self.words = words
        self._name = name
        self._key = key
        self._max_tokens = max_tokens
        self._clients: dict[str, Any] = {}
        self._client_for = client_for or (lambda k: anthropic.Anthropic(api_key=k, base_url=base_url, max_retries=3))
        self._made: dict[tuple[str, float], AnthropicApiBackend] = {}
        self._efforts = dict(efforts or {})

    @staticmethod
    def label(words: Language) -> str:
        return "Anthropic API"

    @classmethod
    def from_spec(cls, spec: ProviderSpec, ctx: ProviderContext) -> "AnthropicApiProvider":
        if not spec.key:
            raise ValueError(f"llm.providers.{spec.name}.key: an API provider needs one — {{secret: <name>}} or {{env: <NAME>}}")
        o: Mapping[str, Any] = spec.options
        return cls(spec.name, key_source(spec.key, ctx.secrets_dir, ctx.words), str(o.get("base_url") or API_URL),
                   int(o.get("max_tokens") or MAX_TOKENS), efforts=effort_by_model(spec.name, spec.models, o.get("effort")),
                   words=ctx.words)

    def _client(self, key: str):
        """One client per key, made again when the key changes."""
        if key not in self._clients:
            self._clients = {key: self._client_for(key)}
        return self._clients[key]

    def models(self) -> list[str]:
        try:
            return [m.id for m in self._client(self._key.read()).models.list(limit=100)]
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise CloudLoginLost(f"API key rejected ({e.status_code})") from e

    def backend(self, model: str, timeout: float = 120.0) -> AnthropicApiBackend:
        if (model, timeout) not in self._made:
            self._made[model, timeout] = AnthropicApiBackend(self._name, self.label(self.words), model, self._key, self._client,
                                                             timeout, self._max_tokens, self._efforts.get(model), words=self.words)
        return self._made[model, timeout]


def effort_by_model(name: str, models: Mapping[str, str], configured: Any) -> dict[str, str]:
    """Each tier's effort (config llm.providers.<name>.effort, tier → low … max, over DEFAULT_EFFORT) as the model it
    goes with. A default is left out for a model another tier also uses: that tier did not ask to think less."""
    where = f"llm.providers.{name}.effort"
    if configured is not None and not isinstance(configured, Mapping):
        raise ValueError(f"{where}: a tier → effort mapping, e.g. {{fast: low, standard: medium}}")
    configured = dict(configured or {})
    for tier, level in configured.items():
        if tier not in models:
            raise ValueError(f"{where}.{tier}: unknown tier; known: {', '.join(models)}")
        if level not in EFFORTS:
            raise ValueError(f"{where}.{tier}: {level!r}; one of {', '.join(EFFORTS)}")
    out: dict[str, str] = {}
    for tier, level in {**{t: e for t, e in DEFAULT_EFFORT.items()
                           if not any(models.get(o) == models.get(t) for o in models if o != t)}, **configured}.items():
        model = models[tier]
        if out.get(model, level) != level:
            raise ValueError(f"{where}: {model} serves two tiers asked to think differently ({out[model]}, {level})")
        out[model] = level
    return out
