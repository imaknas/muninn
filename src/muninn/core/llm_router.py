"""The only path from the core to any LLM.

Which requests may reach a cloud backend is a RoutingPolicy, chosen once at
the composition root:

- PrivacyFirst (fails closed): content that is sensitive, unattributed, or
  from an always-sensitive domain goes to a local backend only, and a cloud
  backend only ever receives a CloudSafe payload built from whitelisted
  fields (privacy.py). If no local backend is up, the request is skipped.
- CloudFirst: every request goes to the cloud backend, and a local one
  answers only when the cloud fails.

A local model standing in for the cloud is marked on the completion
(`stood_in`), so what it wrote can say so. A lapsed cloud login is not a
failure a local model covers: the request fails (`login_lost`) and the
router says so once per request (`on_login_lost`), so the user hears of it.

Every request and its reply can be kept as an evaluation case (a recorder
is injected), so a model can later be measured on the same requests.

A JSON reply that does not keep to its decision point's declared shape (an
injected check) is asked for once more, with what was wrong; the better of the
two goes back, as text, to the caller's own reading of it.

Once the day's spending limit is reached (an injected check, core/budget.py),
no cloud model is asked: a local one still answers what it may, and otherwise
the request is refused ("budget"), so each caller does what it does when no
model answers.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol, Sequence

from .models import Attachment, Sensitivity
from .ports import CloudLoginLost, LLMBackend, Reply
from .redact import redact

log = logging.getLogger(__name__)

_CLOUD_SAFE_TOKEN = object()


class CloudSafe:
    """Text assembled exclusively from whitelisted fields. Construct via privacy.py."""

    __slots__ = ("text",)

    def __init__(self, text: str, *, _token: object = None):
        if _token is not _CLOUD_SAFE_TOKEN:
            raise TypeError("CloudSafe can only be built by muninn.core.privacy")
        self.text = text


def _make_cloud_safe(text: str) -> CloudSafe:
    return CloudSafe(text, _token=_CLOUD_SAFE_TOKEN)


@dataclass(frozen=True)
class LLMRequest:
    system: str
    content: str | CloudSafe
    sensitivity: Sensitivity
    domain: str | None
    json_mode: bool = False
    task: str = "other"  # what the request is for: the evaluation groups and scores cases by it
    attachments: tuple[Attachment, ...] = ()  # files the user sent: only a backend that reads files gets the request


class RoutingPolicy(Protocol):
    stand_in: bool  # a local model may answer when the cloud fails

    def cloud_allowed(self, req: LLMRequest, sensitive_domains: set[str]) -> bool: ...


class PrivacyFirst:
    stand_in = True

    def cloud_allowed(self, req: LLMRequest, sensitive_domains: set[str]) -> bool:
        return (
            isinstance(req.content, CloudSafe)
            and req.sensitivity < Sensitivity.SENSITIVE
            and req.domain is not None
            and req.domain not in sensitive_domains
        )


class CloudFirst:
    """The user chose the cloud model for everything, work included. No local
    model answers, not even when the cloud fails (nothing answers then, and the
    caller says so): a local model takes over a task only once the evaluation
    shows it can."""

    stand_in = False

    def cloud_allowed(self, req: LLMRequest, sensitive_domains: set[str]) -> bool:
        return True


class Recorder(Protocol):
    def __call__(self, req: LLMRequest, reply: str | None, backend: str | None, *, by_local: bool, cloud_ok: bool,
                 ms: int | None = None) -> int | None: ...


@dataclass(frozen=True)
class Completion:
    reply: str | None
    backend: str | None
    case: int | None  # the evaluation case it was kept as
    stood_in: bool = False  # the cloud was meant to answer and failed; a local model did
    login_lost: str | None = None  # the cloud's login has lapsed (what the user calls that backend): nothing answered
    # why nothing was asked: "no_model" (no model is set up at all); "capability" (no model set up reads the files
    # sent); "routing" (one that reads them is not allowed this content: only a local model may, and it cannot);
    # "budget" (today's spending limit is reached, and no local model may answer it)
    refused: str | None = None

    @property
    def unanswered(self) -> bool:
        """No model answered (none available, or every one failed)."""
        return self.backend is None


def with_text_files(text: str, attachments: Sequence[Attachment]) -> tuple[str, tuple[Attachment, ...]]:
    """Text files the user sent, written into the request as text (any model reads them), and the files left: the
    photos and PDFs, which only a model that reads them gets."""
    texts = [a for a in attachments if not a.is_image and a.media_type != "application/pdf"]
    if not texts:
        return text, tuple(attachments)
    inlined = "\n\n".join(f"--- {a.name} ---\n{a.data.decode('utf-8', 'replace')}" for a in texts)
    return f"{inlined}\n\n{text}", tuple(a for a in attachments if a not in texts)


def _took(kept: str, *asked: str) -> str:
    """The reply kept, as having taken what all the replies asked for took (where the provider said), so a reply
    asked for twice costs both."""
    tokens = [(getattr(r, "input_tokens", None), getattr(r, "output_tokens", None)) for r in asked]
    if all(i is None and o is None for i, o in tokens):
        return kept
    return Reply(str(kept), sum(i or 0 for i, _ in tokens), sum(o or 0 for _, o in tokens))


RETRY = "\n\n(Your last reply did not keep to the required format: %s. Reply with only one JSON object in that format.)"


def masked(text: str, task: str) -> str:
    out, n = redact(text)
    if n:
        log.warning("llm request %s: %d credential(s) masked before it went to the cloud", task, n)
    return out


class LLMRouter:
    def __init__(self, backends: Sequence[LLMBackend], sensitive_domains: Iterable[str],
                 policy: RoutingPolicy | None = None, recorder: Recorder | None = None,
                 cloud_for: Callable[[str], Sequence[LLMBackend]] | None = None,
                 on_login_lost: Callable[[LLMBackend, str], None] | None = None,
                 timer: Callable[[], float] = time.monotonic,
                 check_reply: Callable[[str, str], list[str]] | None = None,
                 over_budget: Callable[[], bool] | None = None):
        """`cloud_for(task)`: the cloud backends for a request's task (task_models.py); by default
        the cloud backends among `backends`, whatever the task. `on_login_lost(backend, why)`: a
        cloud backend's login lapsed (a request failed for it). `timer`: seconds, for how long the reply took.
        `check_reply(task, reply)`: what is wrong with a JSON reply for that task ([] when nothing is).
        `over_budget()`: whether today's spending limit is reached (no limit: never)."""
        self._local = [b for b in backends if b.is_local]
        self._cloud = [b for b in backends if not b.is_local]
        self._cloud_for = cloud_for or (lambda task: self._cloud)
        self._sensitive_domains = set(sensitive_domains)
        self._policy = policy or PrivacyFirst()
        self._recorder = recorder
        self._on_login_lost = on_login_lost or (lambda backend, why: None)
        self._timer = timer
        self._check_reply = check_reply
        self._over_budget = over_budget or (lambda: False)

    def _kept_to_shape(self, req: LLMRequest, backend: LLMBackend, sent: str, files: dict, reply: str) -> str:
        """The reply, or, when it does not keep to the task's shape, the one the model gives when told what was
        wrong, if that one does. A second miss, or a failure asking again, keeps the first: the caller reads it."""
        assert self._check_reply is not None
        wrong = self._check_reply(req.task, reply)
        if not wrong:
            return reply
        log.warning("llm reply for %s from %s does not keep to its shape (%s); asked once more", req.task, backend.name, "; ".join(wrong))
        try:
            again = backend.complete(req.system, sent + RETRY % "; ".join(wrong), json_mode=True, **files)
        except Exception as e:  # noqa: BLE001 — the first reply still stands
            log.warning("asking %s again failed: %s", backend.name, e)
            return reply
        if self._check_reply(req.task, again):
            log.warning("llm reply for %s from %s still does not keep to its shape", req.task, backend.name)
            return _took(reply, reply, again)
        return _took(again, reply, again)

    def label_of(self, backend_name: str | None) -> str:
        """What the user calls the backend that wrote something (kept by its name): one of these backends, else one
        of the same provider (the part of the name before ":"), else the name itself."""
        name = backend_name or ""
        known = [*self._local, *self._cloud]
        same = next((b for b in known if b.name == name), None) or next(
            (b for b in known if b.name.split(":")[0] == name.split(":")[0]), None)
        return same.label if same else name

    def backends(self) -> list[LLMBackend]:
        """The models this router may ask (its own; the per-task cloud ones besides)."""
        return [*self._local, *self._cloud]

    def is_local(self, backend_name: str | None) -> bool:
        return any(b.name == backend_name for b in self._local)

    def cloud_allowed(self, req: LLMRequest) -> bool:
        return self._policy.cloud_allowed(req, self._sensitive_domains)

    def complete(self, req: LLMRequest) -> str | None:
        return self.complete_case(req).reply

    def complete_with_backend(self, req: LLMRequest) -> tuple[str | None, str | None]:
        """The reply and the name of the backend that wrote it."""
        c = self.complete_case(req)
        return c.reply, c.backend

    def complete_case(self, req: LLMRequest) -> Completion:
        if not self._local and not self._cloud_for(req.task):  # nothing set up to ask: said so, nothing recorded
            return Completion(None, None, None, refused="no_model")
        text = req.content.text if isinstance(req.content, CloudSafe) else req.content
        text, binary = with_text_files(text, req.attachments)
        cloud_ok = self.cloud_allowed(req)
        reply, name, login_lost, ms = None, None, None, None
        order = [*self._cloud_for(req.task), *(self._local if self._policy.stand_in else [])] if cloud_ok else self._local
        if any(not b.is_local for b in order) and self._over_budget():  # nothing that costs is asked until tomorrow
            order = [b for b in order if b.is_local]
            if not order:
                log.info("llm request %s refused: today's spending limit is reached", req.task)
                return Completion(None, None, None, refused="budget")
        if binary:  # what cannot see the files the user sent does not answer as if it had
            readers = [b for b in order if all(b.capabilities.reads(a) for a in binary)]
            if not readers:
                elsewhere = not cloud_ok and any(all(b.capabilities.reads(a) for a in binary) for b in self._cloud_for(req.task))
                log.info("llm request %s refused: no permitted model reads %s", req.task, ", ".join(a.media_type for a in binary))
                return Completion(None, None, None, refused="routing" if elsewhere else "capability")
            order = readers
        for backend in order:
            try:
                if not backend.available():
                    continue
                started = self._timer()
                files = {"attachments": binary} if binary else {}
                sent = text if backend.is_local else masked(text, req.task)  # a credential pasted in stays here
                reply, name = backend.complete(req.system, sent, json_mode=req.json_mode, **files), backend.name
                if req.json_mode and self._check_reply is not None:
                    reply = self._kept_to_shape(req, backend, sent, files, reply)
                ms = round((self._timer() - started) * 1000)
                break
            except CloudLoginLost as e:  # not something a local model answers in its place
                log.error("llm backend %s: login lapsed (%s); request %s failed", backend.name, e, req.task)
                self._on_login_lost(backend, str(e))
                login_lost = backend.label
                break
            except Exception as e:  # a failing backend must not break the caller
                log.warning("llm backend %s failed: %s", backend.name, e)
        if name is None and not login_lost:
            log.info("llm request skipped: no permitted backend available (local-only=%s)", not cloud_ok)
        stood_in = cloud_ok and self.is_local(name)
        if stood_in:
            log.warning("llm request %s answered by %s in place of the cloud", req.task, name)
        case = None
        if self._recorder is not None and req.task != "judge":
            case = self._recorder(req, reply, name, by_local=self.is_local(name), cloud_ok=cloud_ok, ms=ms)
        return Completion(reply, name, case, stood_in=stood_in, login_lost=login_lost)
