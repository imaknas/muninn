"""A provider with an OpenAI-style Chat Completions API (llm.providers.<name>.impl: openai): OpenAI itself, or any
service that speaks the same at its own base_url (OpenRouter, Gemini's compatible endpoint, DeepSeek, LM Studio,
vLLM). It has no models of its own to assume: config names each tier's.

A key is read on every request, as for the Anthropic API; a provider on this machine or network (location: local)
may have none. JSON is asked for with `response_format`; a service that refuses that is asked again with the
request written into the prompt, and remembered (said once per model). A reply cut off at the length limit, or
withheld by a content filter, is a failure, not a reply; rate limits and overloads are tried again a few times.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Callable, Mapping, Sequence

import httpx

from muninn.adapters.llm.content import check_length, without_thinking
from muninn.adapters.llm.keys import KeySource, key_source
from muninn.adapters.llm.ollama import capabilities_of
from muninn.core.language import Language
from muninn.core.models import Attachment
from muninn.core.ports import TEXT_ONLY, Capabilities, CloudLoginLost, Reply
from muninn.llm_config import ProviderContext, ProviderSpec

log = logging.getLogger(__name__)

API_URL = "https://api.openai.com/v1"
MAX_TOKENS = 16000
RETRIES = 3  # for a rate limit or an overload, waiting as the service says (Retry-After) or 2, 4, 8 seconds
PASSING = {429, 500, 502, 503, 504, 529}  # what may pass by itself; 501 (not implemented) and the like do not
JSON_NOTE = "\nRespond with a single JSON object and nothing else."

Post = Callable[..., httpx.Response]  # httpx.post's shape, for tests


class OpenAICompatBackend:
    def __init__(self, provider: str, label: str, model: str, base_url: str, key: KeySource | None, *, timeout: float,
                 max_tokens: int, is_local: bool, capabilities: Capabilities, context: int | None,
                 post: Post, get: Callable[..., httpx.Response], sleep: Callable[[float], None], no_format: set[str],
                 words: Language):
        self.words = words
        self.name = f"{provider}:{model}"
        self.label = label
        self.is_local = is_local
        self.capabilities = capabilities
        self.login_hint = words("llm.put_key", label=label, where=key.where) if key else ""
        self._model, self._url, self._key = model, base_url.rstrip("/"), key
        self._timeout, self._max_tokens, self._context = timeout, max_tokens, context
        self._post, self._get, self._sleep = post, get, sleep
        self._no_format = no_format  # models whose service refused response_format, shared by the provider

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key.read()}"} if self._key else {}

    def available(self) -> bool:
        return True  # a missing key or an unreachable service is said when asked, not skipped quietly

    def check(self) -> str:
        """The key works and the model is one the service lists (no model is asked anything). A service without a
        model list is reached, its model unconfirmed."""
        try:
            r = self._get(f"{self._url}/models", headers=self._headers(), timeout=30)
        except httpx.HTTPError as e:
            raise RuntimeError(f"{self._url} not reachable: {e}") from e
        if r.status_code in (401, 403) and self._key:
            raise CloudLoginLost(f"API key rejected ({r.status_code})")
        if r.status_code == 404:
            return "reachable (no model list to check against)"
        r.raise_for_status()
        ids = [m.get("id") for m in r.json().get("data", [])]
        if self._model not in ids:
            raise RuntimeError(f"{self._model} is not among {self._url}'s models")
        return "listed"

    def complete(self, system: str, prompt: str, *, json_mode: bool = False, attachments: Sequence[Attachment] = ()) -> str:
        headers = self._headers()  # before the call: no key is a lapsed login, not a failure to stand in for
        check_length(system, prompt, self._context, self.name, self.words)
        native = json_mode and self.name not in self._no_format
        # response_format wants the word "json" in the messages; without response_format, the prompt is what asks
        said = system + JSON_NOTE if json_mode and ("json" not in (system + prompt).lower() or not native) else system
        content: Any = prompt
        if attachments:
            content = [*({"type": "image_url", "image_url": {"url": f"data:{a.media_type};base64,{base64.b64encode(a.data).decode()}"}}
                         for a in attachments if a.is_image),
                       *({"type": "file", "file": {"filename": a.name,
                                                   "file_data": f"data:{a.media_type};base64,{base64.b64encode(a.data).decode()}"}}
                         for a in attachments if a.media_type == "application/pdf"),
                       {"type": "text", "text": prompt}]
        body: dict[str, Any] = {"model": self._model, "max_tokens": self._max_tokens,
                                "messages": [{"role": "system", "content": said}, {"role": "user", "content": content}],
                                **({"response_format": {"type": "json_object"}} if native else {})}
        r = self._send(body, headers)
        if native and r.status_code == 400 and "response_format" in r.text:
            log.warning("%s does not take response_format: JSON is asked for in the prompt from now on", self.name)
            self._no_format.add(self.name)
            return self.complete(system, prompt, json_mode=True, attachments=attachments)
        if r.status_code in (401, 403) and self._key:
            raise CloudLoginLost(f"API key rejected ({r.status_code})")
        r.raise_for_status()
        d = r.json()
        choice = (d.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise RuntimeError(f"reply cut off at max_tokens ({self._max_tokens})")
        if choice.get("finish_reason") == "content_filter":
            raise RuntimeError("the reply was withheld by the service's content filter")
        usage = d.get("usage") or {}
        return Reply(without_thinking(str((choice.get("message") or {}).get("content") or "")),
                     usage.get("prompt_tokens"), usage.get("completion_tokens"))

    def _send(self, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        for attempt in range(RETRIES + 1):
            r = self._post(f"{self._url}/chat/completions", json=body, headers=headers, timeout=self._timeout)
            if r.status_code not in PASSING or attempt == RETRIES:
                return r
            wait = r.headers.get("retry-after")
            self._sleep(min(float(wait), 60) if wait and wait.replace(".", "", 1).isdigit() else 2 ** (attempt + 1))
        return r


class OpenAICompatProvider:
    default_models: Mapping[str, str] = {}  # every service names its own: config says each tier's
    location = "cloud"
    counts_tokens = True

    def __init__(self, name: str, base_url: str = API_URL, key: KeySource | None = None, *, label: str | None = None,
                 local: bool = False, max_tokens: int = MAX_TOKENS, contexts: Mapping[str, int] | None = None,
                 reads: Mapping[str, Capabilities] | None = None, post: Post = httpx.post,
                 get: Callable[..., httpx.Response] = httpx.get, sleep: Callable[[float], None] = time.sleep,
                 words: Language):
        self.words = words
        self._name, self._url, self._key, self._local = name, base_url, key, local
        self._label = label or name
        self._max_tokens, self._contexts, self._reads = max_tokens, dict(contexts or {}), dict(reads or {})
        self._post, self._get, self._sleep = post, get, sleep
        self._no_format: set[str] = set()
        self._made: dict[tuple[str, float], OpenAICompatBackend] = {}

    @staticmethod
    def label(words: Language) -> str:
        return words("llm.openai_compatible")

    @classmethod
    def from_spec(cls, spec: ProviderSpec, ctx: ProviderContext) -> "OpenAICompatProvider":
        o = spec.options
        if not spec.key and spec.location != "local":
            raise ValueError(f"llm.providers.{spec.name}.key: a service off this network needs one — {{secret: <name>}} or {{env: <NAME>}}")
        return cls(spec.name, str(o.get("base_url") or API_URL), key_source(spec.key, ctx.secrets_dir, ctx.words) if spec.key else None,
                   label=o.get("label"), local=spec.location == "local", max_tokens=int(o.get("max_tokens") or MAX_TOKENS),
                   contexts=spec.contexts, reads=capabilities_of(spec.name, o.get("reads"), ("images", "pdf")), words=ctx.words)

    def models(self) -> list[str]:
        try:
            r = self._get(f"{self._url.rstrip('/')}/models", headers={"Authorization": f"Bearer {self._key.read()}"} if self._key else {},
                          timeout=30)
        except httpx.HTTPError as e:
            raise RuntimeError(f"{self._url} not reachable: {e}") from e
        if r.status_code in (401, 403) and self._key:
            raise CloudLoginLost(f"API key rejected ({r.status_code})")
        if r.status_code == 404:
            return []  # no list kept: the model is named by hand
        r.raise_for_status()
        return sorted(str(m.get("id")) for m in r.json().get("data", []) if m.get("id"))

    def backend(self, model: str, timeout: float = 120.0) -> OpenAICompatBackend:
        if (model, timeout) not in self._made:
            self._made[model, timeout] = OpenAICompatBackend(
                self._name, self._label, model, self._url, self._key, timeout=timeout, max_tokens=self._max_tokens,
                is_local=self._local, capabilities=self._reads.get(model, TEXT_ONLY), context=self._contexts.get(model),
                post=self._post, get=self._get, sleep=self._sleep, no_format=self._no_format, words=self.words)
        return self._made[model, timeout]
