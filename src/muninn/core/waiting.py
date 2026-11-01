"""What waits on the user, as one list: every place that shows or says it (the panel, Muninn speaking up,
the morning and the evening) reads this one, so they cannot disagree.

Each kind of thing that can wait on the user is one source: a function giving its entries. The sources are
listed in one place (where Muninn is put together), in the order they are shown and brought up; a new kind is a
new source there. An entry holds facts (what it is, which object, the conversation or deep talk it belongs to,
the sentence that says it, whether Muninn may bring it up unasked, who may hear of it); how it is drawn is the
screen's business, by its kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterable, Sequence
from .language import Language


@dataclass(frozen=True)
class Wait:
    id: str  # "<prefix>:<ref>": the same string everywhere (the panel's item page, taking it off the list at once)
    kind: str  # what it is: the card that shows it
    says: str  # the one sentence that says it (the morning, the evening, Muninn bringing it up)
    group: str = "wait"  # "wait": on the user; "doing": being worked on
    about: dict[str, Any] | None = None  # the item a discussion of it is about
    thread: str | None = None  # the conversation it belongs to
    lease: str | None = None  # the deep talk it came from
    speak: bool = False  # Muninn may bring it up unasked in a pause (one sentence the user can answer by talking)
    suggest: tuple[dict[str, Any], ...] = ()  # the buttons that go with that sentence
    via: tuple[str, ...] = ()  # where its words came from ("lease:<id>", "project:<name>"): only they may hear of it again
    yields: bool = False  # a conversation's own line: left out when the item it is about has its own entry
    by: str | None = None  # who asks it, as the user calls them (a project, a deep talk's)
    lines: tuple[str, ...] = ()  # what it is made of, when it is several (a deep talk's undecided questions)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "group": self.group, "about": self.about, "thread": self.thread, "lease": self.lease,
                "by": self.by, "lines": list(self.lines)}


Source = Callable[[], Iterable[Wait]]


def _key(w: Wait) -> tuple[str, str] | None:
    return (str(w.about["kind"]), str(w.about["id"])) if w.about else None


class Waiting:
    def __init__(self, sources: Sequence[Source], *, words: Language):
        """`sources`: one per kind, in the order they are shown and brought up."""
        self.words = words
        self._sources = list(sources)

    def all(self) -> list[Wait]:
        """Every entry, in order; an item is listed once, by its own entry (a conversation about it yields to it)."""
        got = [w for source in self._sources for w in source()]
        own = {_key(w) for w in got if not w.yields and _key(w)}
        out, seen = [], set()
        for w in got:
            k = _key(w)
            if w.yields and k and (k in own or k in seen):
                continue
            out.append(w)
            if k:
                seen.add(k)
        return out


# ── the sources, each from what it already holds ───────────────────────────


def relinks(names: Callable[[], Sequence[str]], *, words: Language) -> Source:
    return lambda: [Wait(f"relink:{n}", "relink", words("waiting.relink", name=n)) for n in names()]


def permission_requests(pending: Callable[[], Sequence[dict[str, Any]]], *, words: Language) -> Source:
    def says(p: dict[str, Any]) -> str:
        who = p.get("label") or p.get("project") or words("waiting.a_project")
        return words("waiting.permission", who=who, what=p.get("summary") or p.get("domain") or p.get("kind"))
    return lambda: [Wait(f"perm:{p['id']}", "perm", says(p), lease=p.get("lease")) for p in pending()]


def confirmations(pending: Callable[[], Sequence[Any]]) -> Source:
    """Muninn's own questions; which repo a piece of work belongs to is answered on its card, never brought up."""
    return lambda: [Wait(f"c:{c.id}", c.kind, c.question, about={"kind": "question", "id": c.id}, speak=c.kind != "attribution",
                         suggest=tuple({"label": o, "tool": "answer_question", "args": {"answer": o},
                                        **({"plain": True} if (c.data or {}).get("neutral") else {})} for o in c.options))
                    for c in pending()]


def topics(today: Callable[[], Sequence[dict[str, Any]]], *, words: Language) -> Source:
    """The conversation's open topics: one waiting on the user, or one being worked on."""
    def says(tp: dict[str, Any]) -> str:
        return tp.get("ask") or (words("waiting.topic_now", title=tp["title"], now=tp["now"]) if tp.get("now") else tp["title"])
    return lambda: [Wait(f"topic:{tp['thread']}", "topic", says(tp), group="wait" if tp["state"] == "waiting" else "doing",
                         about=tp.get("about"), thread=tp["thread"], speak=tp["state"] == "waiting", via=tuple(tp.get("via") or ()),
                         yields=True)
                    for tp in today() if tp["state"] in ("waiting", "doing")]


def project_questions(open_questions: Callable[[], Sequence[dict[str, Any]]], *, words: Language) -> Source:
    """A project's question, waiting for the user's answer (a deep talk's own are that talk's: `talk_questions`)."""
    return lambda: [Wait(f"pq:{q['id']}", "project", words("waiting.project_asks", project=q["project"], text=q["text"]), about={"kind": "project_question", "id": q["id"]},
                         speak=True, via=(f"project:{q['project']}",), by=q["project"])
                    for q in open_questions() if not str(q.get("context") or "").startswith("lease:")]


def talk_questions(threads: Callable[[], Iterable[tuple[str, dict[str, Any]]]], *, words: Language) -> Source:
    """What a deep talk left undecided waits together, with its conversation: asked in the talk when the user goes
    back into it (never brought up here); a talk going on has them in hand."""
    return lambda: [Wait(f"talkq:{tid}", "talkq", words("waiting.talk_left", called=left["called"], n=len(left["questions"])), thread=tid,
                         lease=left["lease"], via=(f"lease:{left['lease']}",), by=left["called"], lines=tuple(left["questions"]))
                    for tid, t in threads() if (left := t.get("talk_left")) and left.get("questions") and not t.get("lease")]


def plan_review(queue: Callable[[], Sequence[dict[str, Any]]], *, words: Language) -> Source:
    return lambda: [Wait(f"r:{r['id']}", "review", words("waiting.review", title=r["title"], detail=r["detail"]),
                         about={"kind": "review", "id": r["id"]}, speak=True,
                         suggest=({"label": words("waiting.yes"), "tool": "decide_item", "args": {"decision": "confirm"}},
                                  {"label": words("waiting.no"), "tool": "decide_item", "args": {"decision": "reject"}}))
                    for r in queue()]


def ready_packets(ready: Callable[[], Sequence[Any]], *, words: Language) -> Source:
    def one(p: Any) -> Wait:
        reply = p.kind == "mail_reply"
        says = words("waiting.reply_ready", title=p.title) if reply else words("waiting.packet_ready", title=p.title)
        return Wait(f"p:{p.id}", "reply" if reply else "packet", says,
                    about=None if reply else {"kind": "packet", "id": p.id})
    return lambda: [one(p) for p in ready()]


def due_tasks(open_tasks: Callable[[], Sequence[dict[str, Any]]], today: Callable[[], date], *, words: Language) -> Source:
    """A task due by tomorrow (or overdue) waits on the user like a question does; the day is the user's."""
    def waits() -> list[Wait]:
        d = today()
        out = []
        for t in open_tasks():
            if not t.get("due"):
                continue
            days = (date.fromisoformat(str(t["due"])[:10]) - d).days
            if days <= 1:
                when = words("waiting.overdue") if days < 0 else words("waiting.due_today") if days == 0 else words("waiting.due_tomorrow")
                out.append(Wait(f"t:{t['key']}", "due", words("waiting.due", title=t["title"], when=when), about={"kind": "task", "id": t["key"]}))
        return out
    return waits


def lent_talks(active: Callable[[], Sequence[dict[str, Any]]], topic_threads: Callable[[], set[str]], *,
               words: Language) -> Source:
    """Deep talks going on, unless their conversation is a topic already (open or settled today)."""
    def waits() -> list[Wait]:
        known = topic_threads()
        return [Wait(f"lease:{x['thread']}", "lease", words("waiting.talking_with", who=x.get("label") or x["project"]), group="doing", thread=x["thread"],
                     lease=x["lease_id"])
                for x in active() if x["thread"] not in known]
    return waits
