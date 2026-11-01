"""Filling in a job's application form from its packet, so the user only looks it over and presses send once.

For a packet whose posting is on a site Muninn can fill, it reads the form's fields, has a model answer each from the
packet, the résumé and what the user answered before, and asks the user (in the conversation) only what they alone can
say. Once every required field has an answer it fills the form in a browser, keeps a picture of it, and puts one
question to the user: send it? Their yes sends it, and only a page that says it was received marks the target applied.

Sites differ, so each is an ApplicationSite (Greenhouse first). A posting on a site with none is said so on the packet,
and applied to by hand as before. Nothing is sent without the user's yes, and nothing on a page meant for a person
(a CAPTCHA, a code sent to their mail) is answered for them: it is handed back with what is left to do."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .actions import ActionGate, DecidedBy, PacketApply
from .language import Language, written_in
from .llm_router import LLMRequest, LLMRouter
from .models import InboxItem, Prepared
from .ports import Clock, PreparedStore, StateStore
from .prepare import PACKET
from .timewin import local_date
from .tools import Tool
from .tools.apply import AnswerForm
from .reply_json import json_object

log = logging.getLogger(__name__)

KINDS = ("text", "textarea", "select", "multiselect", "file")


@dataclass(frozen=True)
class Field:
    """One field of a form: `id` is what the site finds it by; `options` the choices of a select."""

    id: str
    label: str
    kind: str  # one of KINDS
    required: bool = False
    options: tuple[str, ...] = ()
    note: str = ""  # what the form says under it


@dataclass(frozen=True)
class Form:
    site: str  # the ApplicationSite's name
    url: str  # where the form is filled in
    fields: tuple[Field, ...]

    def field(self, fid: str) -> Field | None:
        return next((f for f in self.fields if f.id == fid), None)

    def to_dict(self) -> dict[str, Any]:
        return {"site": self.site, "url": self.url, "fields": [asdict(f) for f in self.fields]}

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> Form:
        return Form(d["site"], d["url"], tuple(Field(**{**f, "options": tuple(f.get("options") or ())}) for f in d["fields"]))


class PostingGone(Exception):
    """The site says the posting is no longer there (taken down, or filled): there is no form to fill any more."""


# why a site could not go on by itself: a person has to do what is left
CAPTCHA, CODE, INVALID, UNREACHABLE = "captcha", "code", "invalid", "unreachable"


@dataclass(frozen=True)
class SiteResult:
    """What a site did: `shot` a picture of the page (JPEG), `said` what the page said after sending, `blocked` why it
    stopped (CAPTCHA, CODE, INVALID, UNREACHABLE; "" when it did not), `refused` the fields the page would not take,
    `pressed` whether its send button was pressed (from then on the application may have gone, whatever followed)."""

    ok: bool
    shot: bytes | None = None
    said: str = ""
    blocked: str = ""
    refused: tuple[str, ...] = ()
    pressed: bool = False


class ApplicationSite(Protocol):
    name: str

    def handles(self, url: str) -> bool:
        """Whether a posting at this address is one this site's forms are filled for."""
        ...

    def form(self, url: str) -> Form | None:
        """The posting's application form; None when it cannot be read now. PostingGone when the site says the
        posting is no longer there."""
        ...

    def fill(self, form: Form, values: Mapping[str, Any], files: Mapping[str, Path]) -> SiteResult:
        """Fills the form in a browser without sending it: ok with a picture of it filled."""
        ...

    def submit(self, form: Form, values: Mapping[str, Any], files: Mapping[str, Path]) -> SiteResult:
        """Fills the form again and sends it: ok only when the page says it was received."""
        ...


class Sites:
    """The sites Muninn can fill forms on, the first that handles an address answering for it."""

    def __init__(self, sites: Sequence[ApplicationSite] = ()):
        self.sites = list(sites)

    def __bool__(self) -> bool:
        return bool(self.sites)

    def for_url(self, url: str) -> ApplicationSite | None:
        return next((s for s in self.sites if s.handles(url)), None)


FILL_SYSTEM = """You fill in a job application form for the user, who looks it over and sends it themselves.
Input is JSON: the form's fields (id, label, kind, required, options, note), the application packet prepared for this job
(summary, draft answers, the résumé chosen), the résumé's text, and what the user answered on earlier forms (earlier: label → answer).
Reply with one JSON object and nothing else:
{"values": {"<field id>": "<text or option>" or ["<option>", ...]}, "ask": [{"field": "<field id>", "question": "..."}]}
Rules:
- A select's value is one of its options, written exactly as given; a multiselect's is a list of them.
- Name, email, phone, location, links, employers, titles, dates and numbers come from the résumé.
- Text answers use the packet's draft answers where one fits, in the language the form is written in.
- What only the user can say goes in ask, never guessed: work authorization, visa sponsorship, notice period, start date,
  expected salary, restrictive agreements, how they heard of the job, and any placeholder in brackets in a draft answer.
  An earlier answer to the same question is used instead of asking again.
- question: one short question in {language} for one field.
- Voluntary self-identification (gender, race, ethnicity, veteran, disability): the option that declines to answer, unless an earlier answer says otherwise.
- A required acknowledgement of a policy the form shows is checked.
- File fields are left out: the résumé is attached by Muninn.
- A field that is not required and has nothing that fits is left out.
"""


def fill_system(words: Language) -> str:
    return written_in(FILL_SYSTEM, words)


def checked(form: Form, values: Mapping[str, Any], ask: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """A model's answers kept to the form: a select's value one of its options (matched without regard to case), no
    file field, every required field either answered or asked (by its label when the model left it out)."""
    out: dict[str, Any] = {}
    for f in form.fields:
        if f.kind == "file" or f.id not in values:
            continue
        v = values[f.id]
        if f.kind == "select":
            v = _option(f, v[0] if isinstance(v, list) and v else v)
        elif f.kind == "multiselect":
            v = [o for o in (_option(f, x) for x in (v if isinstance(v, list) else [v])) if o] or None
        else:
            v = str(v).strip() if v is not None and not isinstance(v, (dict, list)) else None
        if v:
            out[f.id] = v
    asked = {}
    for a in ask:
        f = form.field(str(a.get("field")))
        if f is not None and f.kind != "file" and f.id not in out and str(a.get("question") or "").strip():
            asked[f.id] = str(a["question"]).strip()
    for f in form.fields:
        if f.required and f.kind != "file" and f.id not in out and f.id not in asked:
            asked[f.id] = f.label
    return out, [{"field": k, "question": q} for k, q in asked.items()]


def _option(f: Field, v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip().casefold()
    return next((o for o in f.options if o.casefold() == s), None)


def parse_fill(reply: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    data = json_object(reply)
    if data is None or not isinstance(data.get("values"), dict):
        return None
    return data["values"], [a for a in data.get("ask") or [] if isinstance(a, dict)]


class FormFiller:
    """The model that answers a form's fields (decision point form_fill)."""

    def __init__(self, router: LLMRouter):
        self._router = router

    def answer(self, form: Form, packet: Mapping[str, Any], resume: str, earlier: Mapping[str, str], *, sensitivity: Any,
               domain: str, words: Language) -> tuple[dict[str, Any], list[dict[str, str]]] | None:
        """The fields' values and what to ask the user; None when the model gave nothing usable."""
        reply = self._router.complete(LLMRequest(
            system=fill_system(words),
            content=json.dumps({"fields": [asdict(f) for f in form.fields if f.kind != "file"],
                                "packet": {k: packet.get(k) for k in ("summary", "answers", "resume") if k in packet},
                                "resume": resume, "earlier": dict(earlier)}, ensure_ascii=False),
            sensitivity=sensitivity, domain=domain, json_mode=True, task="form_fill"))
        got = parse_fill(reply)
        return None if got is None else checked(form, *got)


class ApplyStorage(PreparedStore, StateStore, Protocol):
    """The packets, and each one's form as it is being filled (state "apply:<packet id>"), and what the user answered
    on forms before (state "apply:earlier": label → answer)."""


class Applications:
    """Application forms filled from packets: reading and answering a form, asking the user what only they can say,
    filling it, and sending it on their yes. Each packet's form goes through states: unsupported (no site fills it),
    unread (could not be read today; tried again tomorrow), gone (the site says the posting is no longer there), asking
    (waits on the user's answers), filled (waits on their yes), sent, or held (the site stopped; what is left is the
    user's)."""

    def __init__(self, store: ApplyStorage, clock: Clock, tz: str, sites: Sites, filler: FormFiller | None,
                 packets: Any, documents: Callable[[], Mapping[str, Path]], texts: Callable[[], Sequence[tuple[str, str]]],
                 keep: Callable[[str, bytes], str], gate: ActionGate, report: Callable[[InboxItem, datetime], None],
                 speak: Callable[[dict[str, Any], str, Sequence[dict[str, Any]]], Any], *, words: Language):
        """`packets` (Packets): the packets and marking one sent; `documents()`: the résumé files by name, `texts()`
        their text; `keep(name, jpeg)`: keeps a picture of a form, returning the id it is shown by; `report` tells the
        user (inbox and push); `speak(about, text, buttons)` says something in the packet's conversation."""
        self.words = words
        self._store = store
        self._clock = clock
        self._tz = tz
        self.sites = sites
        self._filler = filler
        self._packets = packets
        self._documents = documents
        self._texts = texts
        self._keep = keep
        self._gate = gate
        self._report = report
        self._speak = speak

    # ── what a packet's form is at ──

    def state(self, pid: int) -> dict[str, Any] | None:
        return self._store.get_state(f"apply:{pid}")

    def _save(self, pid: int, st: dict[str, Any]) -> dict[str, Any]:
        self._store.set_state(f"apply:{pid}", st)
        return st

    def view(self, pid: int) -> dict[str, Any] | None:
        """The form as the packet's page shows it: its state, each field with its value (or the question that waits),
        the picture of it filled, and what the page said when it was sent."""
        st = self.state(pid)
        if not st:
            return None
        out = {k: st[k] for k in ("state", "site", "shot", "said", "blocked") if st.get(k)}
        if st.get("form"):
            form = Form.from_dict(st["form"])
            asked = {a["field"]: a["question"] for a in st.get("ask") or []}
            out["url"] = form.url
            out["fields"] = [{"id": f.id, "label": f.label, "required": f.required,
                              **({"value": st["values"][f.id]} if f.id in st.get("values", {}) else {}),
                              **({"ask": asked[f.id]} if f.id in asked else {}),
                              **({"file": st["files"][f.id]} if f.id in st.get("files", {}) else {})}
                             for f in form.fields]
        return out

    # ── reading and answering a form ──

    def run(self, limit: int = 1) -> list[int]:
        """Reads and answers the forms of packets ready to send that have none yet (one a round by default: a browser
        is slow); fills those whose questions were all answered. The packets moved on."""
        if not self.sites or self._filler is None:
            return []
        today = local_date(self._clock.now(), self._tz).isoformat()
        moved = []
        for p in self._store.prepared(status="ready"):
            if len(moved) >= limit:
                break
            if p.kind != PACKET:
                continue
            st = self.state(p.id) or {}
            if st.get("state") in ("unsupported", "gone", "asking", "filled", "sent", "held", "unsure") or (st.get("state") == "unread" and st.get("on") == today):
                continue
            moved.append(p.id)
            self.read(p)
        return moved

    def read(self, p: Prepared) -> dict[str, Any]:
        """Reads a packet's form and answers it: asking the user what is left, or filling it when nothing is."""
        url = (p.data.get("posting") or {}).get("url") or ""
        site = self.sites.for_url(url)
        if site is None:
            return self._save(p.id, {"state": "unsupported"})
        try:
            form = site.form(url)
        except PostingGone:
            self._speak({"kind": "packet", "id": str(p.id)}, self.words("apply.gone", title=p.title, site=site.name),
                        [{"label": self.words("apply.gone_set_aside"), "tool": "dismiss_packet",
                          "args": {"packet": p.id, "why": self.words("apply.gone_why")}}])
            return self._save(p.id, {"state": "gone", "site": site.name})
        if form is None:
            return self._save(p.id, {"state": "unread", "site": site.name, "on": local_date(self._clock.now(), self._tz).isoformat()})
        plan = self._packets.plan_for(p)
        packet = p.data.get("packet") or {}
        texts = dict(self._texts())
        resume = packet.get("resume") or next(iter(texts), "")
        got = self._filler.answer(form, packet, texts.get(resume, ""), self._store.get_state("apply:earlier") or {},
                                  sensitivity=plan.sensitivity_default, domain=p.domain, words=self.words) if plan else None
        if got is None:
            return self._save(p.id, {"state": "unread", "site": site.name, "on": local_date(self._clock.now(), self._tz).isoformat()})
        values, ask = got
        files = self._files(form, resume, texts, values)
        st = self._save(p.id, {"state": "asking", "site": site.name, "form": form.to_dict(), "values": values, "files": files, "ask": ask})
        if ask:
            self._speak({"kind": "packet", "id": str(p.id)}, self.words("apply.asking", title=p.title,
                                                                        questions=self.words.join([a["question"] for a in ask])), [])
            return st
        return self._fill(p, st)

    def _files(self, form: Form, resume: str, texts: Mapping[str, str], values: dict[str, Any]) -> dict[str, str]:
        """The résumé attached to the form's résumé field when the site takes its kind of file; else its text goes in
        the field the form has for a résumé typed in, when it has one."""
        field = next((f for f in form.fields if f.kind == "file" and "resume" in f.id.lower()), None)
        path = self._documents().get(resume)
        if field is None or path is None:
            return {}
        typed = form.field(f"{field.id}_text")
        if path.suffix.lower() in (".pdf", ".doc", ".docx", ".txt", ".rtf"):
            if typed is not None:
                values.pop(typed.id, None)  # attached: not typed in as well
            return {field.id: resume}
        if typed is not None and texts.get(resume):
            values[typed.id] = texts[resume]
        return {}

    def answer(self, pid: int, fid: str, value: Any) -> str:
        """The user's answer for one field of a packet's form (said in the conversation): kept, and remembered for
        the same question on later forms. When nothing is left to ask, the form is filled. What to tell the user."""
        p = self._store.get_prepared(pid)
        st = self.state(pid)
        if p is None or p.status != "ready" or not st or not st.get("form") or st.get("state") == "sent":
            raise ValueError(self.words("apply.no_form"))
        form = Form.from_dict(st["form"])
        f = form.field(fid)
        if f is None or f.kind == "file":
            raise ValueError(self.words("apply.no_field", fields=self.words.join([x.label for x in form.fields if x.kind != "file"])))
        values, _ = checked(Form(form.site, form.url, (f,)), {fid: value}, ())
        if fid not in values:
            raise ValueError(self.words("apply.not_an_option", label=f.label, options=self.words.join(list(f.options))))
        earlier = dict(self._store.get_state("apply:earlier") or {})
        earlier[f.label] = values[fid]
        self._store.set_state("apply:earlier", earlier)
        st["values"] = {**st.get("values", {}), fid: values[fid]}
        st["ask"] = [a for a in st.get("ask") or [] if a["field"] != fid]
        if st.get("state") in ("filled", "held"):  # a change to a filled form: filled again, to be looked over again
            st["state"] = "asking"
        self._save(pid, st)
        if st["ask"]:
            return self.words("apply.still_asking", label=f.label, questions=self.words.join([a["question"] for a in st["ask"]]))
        st = self._fill(p, st)
        return self.words("apply.filled_now", title=p.title) if st["state"] == "filled" else self.words("apply.held", title=p.title, why=self._why(st))

    # ── filling and sending ──

    def _fill(self, p: Prepared, st: dict[str, Any]) -> dict[str, Any]:
        texts = dict(self._texts())
        resume = (p.data.get("packet") or {}).get("resume") or next(iter(texts), "")
        st["files"] = self._files(Form.from_dict(st["form"]), resume, texts, st["values"])  # the résumé as it is now
        site = self.sites.for_url(Form.from_dict(st["form"]).url) or self.sites.for_url((p.data.get("posting") or {}).get("url") or "")
        if site is None:
            return self._save(p.id, {**st, "state": "unsupported"})
        r = site.fill(Form.from_dict(st["form"]), st["values"], self._paths(st))
        if not r.ok:
            st = self._save(p.id, {**st, "state": "held", "blocked": r.blocked or UNREACHABLE, "refused": list(r.refused)})
            self._report(InboxItem(None, self._clock.now(), p.domain, "ready", self.words("apply.held", title=p.title, why=self._why(st)),
                                   "", {"prepared": p.id}), self._clock.now())
            return st
        st = self._save(p.id, {**st, "state": "filled", "blocked": "", "shot": self._keep(f"form-{p.id}.jpg", r.shot) if r.shot else ""})
        self._gate.run(PacketApply(p.id, p.title), DecidedBy.INFERRED_SURE)  # sending cannot be taken back: asked first
        return st

    def _paths(self, st: Mapping[str, Any]) -> dict[str, Path]:
        docs = self._documents()
        return {fid: docs[name] for fid, name in (st.get("files") or {}).items() if name in docs}

    def submit(self, pid: int) -> tuple[Prepared, str] | None:
        """Sends a filled form (the user said yes): the packet sent and its target applied when the page says the
        application was received; else held, with what is left to the user. (packet, what the page said), or None."""
        p = self._store.get_prepared(pid)
        st = self.state(pid)
        if p is None or p.status != "ready" or not st or st.get("state") not in ("filled", "held") or not st.get("form"):
            return None
        form = Form.from_dict(st["form"])
        site = self.sites.for_url(form.url)
        if site is None:
            return None
        r = site.submit(form, st["values"], self._paths(st))
        if not r.ok and r.pressed and (r.blocked or UNREACHABLE) == UNREACHABLE:
            # sent, and then nothing that says whether the site took it: never sent again from here (an application
            # cannot be taken back), the user looks for the site's confirmation and says whether it went
            st = self._save(pid, {**st, "state": "unsure", "blocked": UNREACHABLE, "refused": []})
            self._speak({"kind": "packet", "id": str(pid)}, self.words("apply.unsure", title=p.title),
                        [{"label": self.words("apply.unsure_sent"), "tool": "packet_sent", "args": {"packet": pid}}])
            return None
        if not r.ok:
            st = self._save(pid, {**st, "state": "held", "blocked": r.blocked or UNREACHABLE, "refused": list(r.refused)})
            self._speak({"kind": "packet", "id": str(pid)}, self.words("apply.held", title=p.title, why=self._why(st)), [])
            return None
        shot = self._keep(f"sent-{pid}.jpg", r.shot) if r.shot else st.get("shot", "")
        self._save(pid, {**st, "state": "sent", "said": r.said, "shot": shot})
        self._packets.mark_sent(pid)
        return p, r.said

    def why(self, pid: int) -> str:
        """Why a packet's form was held, as the user is told."""
        return self._why(self.state(pid) or {})

    def _why(self, st: Mapping[str, Any]) -> str:
        blocked = st.get("blocked") or UNREACHABLE
        if blocked == INVALID and st.get("refused") and st.get("form"):
            form = Form.from_dict(st["form"])
            labels = [f.label for fid in st["refused"] if (f := form.field(fid))]
            return self.words("apply.why_invalid", fields=self.words.join(labels))
        return self.words(f"apply.why_{blocked}")

    # ── as an actor: the conversation answers a form's questions ──

    kinds = ()

    def topic_extra(self, pid: int) -> dict[str, Any]:
        """What the packet's topic adds about its form: the fields still asked (id, label, question, options), and the
        values it was filled with, so a sentence can answer one or change one."""
        st = self.state(pid)
        if not st or not st.get("form") or st.get("state") == "sent":
            return {}
        form = Form.from_dict(st["form"])
        return {"form": {"state": st["state"],
                         "asking": [{"field": a["field"], "label": f.label, "question": a["question"], **({"options": list(f.options)} if f.options else {})}
                                    for a in st.get("ask") or [] if (f := form.field(a["field"]))],
                         "filled": [{"field": f.id, "label": f.label, "value": st["values"][f.id]} for f in form.fields if f.id in st.get("values", {})]}}

    def tools(self) -> list[Tool]:
        return [AnswerForm(self)] if self.sites else []  # no site to fill a form on: nothing to answer

    def jobs(self) -> list[Any]:
        from .actors import Job

        return [Job("filling application forms", lambda: self.run(1), phase="after")] if self.sites else []
