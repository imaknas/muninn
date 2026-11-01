"""Local backend: Ollama, on this machine or this network (llm.providers.<name>.impl: ollama; the older
llm.local_model names it too).

Its own API rather than the OpenAI-compatible one: only here can a request say how much the model reads at once
(`num_ctx`); without it Ollama reads 4096 tokens whatever the model holds, and cuts a longer prompt from the front.
Photos go only to a model config says reads them; no Ollama model reads a PDF as it is.
"""

from __future__ import annotations

import base64
from typing import Any, Mapping, Sequence

import httpx

from muninn.adapters.llm.content import check_length, without_thinking
from muninn.core.language import Language
from muninn.core.models import Attachment
from muninn.core.ports import TEXT_ONLY, Capabilities, Reply
from muninn.llm_config import ProviderContext, ProviderSpec

DEFAULT_URL = "http://127.0.0.1:11434"


class OllamaBackend:
    login_hint = ""

    def __init__(self, model: str, base_url: str = DEFAULT_URL, timeout: float = 180.0, *, name: str = "ollama",
                 label: str | None = None, is_local: bool = True, context: int | None = None,
                 capabilities: Capabilities = TEXT_ONLY, words: Language):
        """`label`: what the user calls it (the local model, unless given)."""
        self.words = words
        self.name = f"{name}:{model}"
        self.label = label or words("llm.local_model")
        self.is_local = is_local
        self.capabilities = capabilities
        self._model = model
        self._url = base_url.rstrip("/")
        self._timeout = timeout
        self._context = context

    def _models(self) -> list[str] | None:
        r = httpx.get(f"{self._url}/api/tags", timeout=3)
        return [m.get("name") for m in r.json().get("models", [])] if r.status_code == 200 else None

    def available(self) -> bool:
        try:
            return self._model in (self._models() or [])
        except (httpx.HTTPError, ValueError):
            return False

    def check(self) -> str:
        try:
            names = self._models()
        except (httpx.HTTPError, ValueError) as e:
            raise RuntimeError(f"Ollama not reachable at {self._url}: {e}") from e
        if names is None:
            raise RuntimeError(f"Ollama at {self._url} did not list its models")
        if self._model not in names:
            raise RuntimeError(f"{self._model} is not pulled (ollama pull {self._model})")
        return "reachable"

    def complete(self, system: str, prompt: str, *, json_mode: bool = False, attachments: Sequence[Attachment] = ()) -> str:
        check_length(system, prompt, self._context, self.name, self.words)
        if json_mode:
            # MLX models in Ollama reject `format`; ask for JSON in the prompt instead.
            system += "\nRespond with a single JSON object and nothing else."
        user: dict[str, Any] = {"role": "user", "content": prompt}
        if attachments:
            user["images"] = [base64.b64encode(a.data).decode() for a in attachments if a.is_image]
        body: dict[str, Any] = {"model": self._model, "messages": [{"role": "system", "content": system}, user],
                                "stream": False, "think": False,
                                "options": {"temperature": 0.2, **({"num_ctx": self._context} if self._context else {})}}
        r = httpx.post(f"{self._url}/api/chat", json=body, timeout=self._timeout)
        r.raise_for_status()
        d = r.json()
        if d.get("done_reason") == "length":
            raise RuntimeError(f"reply cut off at the model's length limit ({self.name})")
        return Reply(without_thinking(d["message"]["content"]), d.get("prompt_eval_count"), d.get("eval_count"))


def capabilities_of(name: str, declared: Any, allowed: tuple[str, ...]) -> dict[str, Capabilities]:
    """config llm.providers.<name>.reads: model → what it reads besides text ([images], [images, pdf]); a model not
    named reads text only."""
    where = f"llm.providers.{name}.reads"
    if declared is None:
        return {}
    if not isinstance(declared, Mapping):
        raise ValueError(f"{where}: model → what it reads, e.g. {{qwen2.5vl:7b: [images]}}")
    out = {}
    for model, kinds in declared.items():
        kinds = [kinds] if isinstance(kinds, str) else list(kinds or [])
        if bad := set(kinds) - set(allowed):
            raise ValueError(f"{where}.{model}: {sorted(bad)}; this provider's models can read {', '.join(allowed)}")
        out[str(model)] = Capabilities(images="images" in kinds, pdf="pdf" in kinds)
    return out


class OllamaProvider:
    """Ollama as a provider: its models' backends, each reading as much as config declares (`context`)."""

    default_models: Mapping[str, str] = {}
    location = "local"
    counts_tokens = True

    def __init__(self, name: str, base_url: str = DEFAULT_URL, *, local: bool = True, label: str | None = None,
                 contexts: Mapping[str, int] | None = None, reads: Mapping[str, Capabilities] | None = None, words: Language):
        self.words = words
        self._name, self._url, self._local = name, base_url, local
        self._label = label or (self.label(words) if local else name)
        self._contexts, self._reads = dict(contexts or {}), dict(reads or {})
        self._made: dict[tuple[str, float], OllamaBackend] = {}

    @staticmethod
    def label(words: Language) -> str:
        return words("llm.local_model")

    @classmethod
    def from_spec(cls, spec: ProviderSpec, ctx: ProviderContext) -> "OllamaProvider":
        o = spec.options
        return cls(spec.name, str(o.get("base_url") or DEFAULT_URL), local=spec.location == "local", label=o.get("label"),
                   contexts=spec.contexts, reads=capabilities_of(spec.name, o.get("reads"), ("images",)), words=ctx.words)

    def models(self) -> list[str]:
        try:
            r = httpx.get(f"{self._url.rstrip('/')}/api/tags", timeout=5)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(f"Ollama not reachable at {self._url}: {e}") from e
        names = sorted(str(m.get("name")) for m in r.json().get("models", []) if m.get("name"))
        return [n for n in names if self._chats(n)]

    def _chats(self, name: str) -> bool:
        """Whether the model holds a conversation, as Ollama says of it (an embedding model does not); when Ollama
        says nothing (an older version), it is listed."""
        try:
            r = httpx.post(f"{self._url.rstrip('/')}/api/show", json={"model": name}, timeout=5)
            caps = r.json().get("capabilities") if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return True
        return not caps or "completion" in caps

    def backend(self, model: str, timeout: float = 180.0) -> OllamaBackend:
        if (model, timeout) not in self._made:
            self._made[model, timeout] = OllamaBackend(model, self._url, timeout, name=self._name, label=self._label,
                                                       is_local=self._local, context=self._contexts.get(model),
                                                       capabilities=self._reads.get(model, TEXT_ONLY), words=self.words)
        return self._made[model, timeout]
