"""Tools for the conversation itself: what to do today, a question about the user's own plans,
an answer to the question being discussed, a mail to find, news to analyse, a wish for Muninn,
and taking back something Muninn did."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, ClassVar, Protocol, Sequence

from ..models import ActionRecord, Confirmation
from ..ports import Clock, ObservationStore, PreparedStore
from ..project_messages import ProjectMessages
from . import FACT, Outcome, Turn


class Today:
    name: ClassVar[str] = "today"
    description: ClassVar[str] = "What to do first today. args: {}"
    lookup: ClassVar[bool] = True

    def __init__(self, next_items: Callable[[], Sequence[dict[str, Any]]]):
        """`next_items`: what is due or behind, most urgent first (the home page's next steps)."""
        self._next = next_items

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        nxt = self._next()
        if not nxt:
            return Outcome(turn.words("tools_conversation.nothing_today"))
        return Outcome("\n".join(f"{i}. {q['text']}" for i, q in enumerate(nxt[:5], 1)), link="#home")


class Ask:
    name: ClassVar[str] = "ask"
    description: ClassVar[str] = "The user is asking about the user's own plans, goals, applications, progress or decisions (e.g. \"Why do I need to practise Go?\", \"How far behind is the PhD?\"). args: {question: <the user's question>}"
    lookup: ClassVar[bool] = True

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        if not turn.answer:  # looked up before the round was carried out
            raise ValueError(turn.words("tools_conversation.no_answer"))
        return Outcome(turn.answer)


class MailNotes(ObservationStore, PreparedStore, Protocol):
    """The mail notes kept as observations, and the reply drafts prepared."""


class FindMail:
    name: ClassVar[str] = "find_mail"
    description: ClassVar[str] = "Find a recent email (to reply to it or read it). args: {query: <sender or subject keywords>}"
    lookup: ClassVar[bool] = True

    def __init__(self, store: MailNotes, clock: Clock):
        self._store = store
        self._clock = clock

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        q = str(a["query"]).lower()
        now = self._clock.now()
        for o in reversed(self._store.observations(now - timedelta(days=21), now + timedelta(seconds=1), kinds=("mail",))):
            p = o.payload
            if q in str(p.get("from", "")).lower() or q in str(p.get("subject", "")).lower():
                ready = next((r for r in self._store.prepared(status="ready")
                              if r.kind == "mail_reply" and r.target == p.get("thread")), None)
                if ready:
                    return Outcome(turn.words("tools_conversation.mail_with_draft", subject=p.get("subject")), link=f"#packet/{ready.id}")
                return Outcome(turn.words("tools_conversation.mail_found", subject=p.get("subject")), link=f"#mail/{p.get('account')}:{p.get('id')}")
        raise ValueError(turn.words("tools_conversation.no_mail", query=a["query"]))


class RecordFact:
    """Never run: the sentence goes to the fact analysis (the command runner does that)."""

    name: ClassVar[str] = FACT
    description: ClassVar[str] = "The user tells you a fact, progress or news about something that has already happened (e.g. \"Just found out…\", \"X replied…\", \"Decided…\"). args: {fact: <the fact>}"
    lookup: ClassVar[bool] = False

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        raise ValueError("record_fact is analysed, not run")


class AnswerQuestion:
    name: ClassVar[str] = "answer_question"
    description: ClassVar[str] = ("The user plainly answered the question under discussion. When about.kind is question, answer is one of about.options (word for word); "
                                  "when it is project_question, answer is the reply to pass on to that project: written as one complete passage in the user's words. args: {answer: <the answer>}")
    lookup: ClassVar[bool] = False

    def __init__(self, messages: ProjectMessages, answer: Callable[[int, str], Confirmation | None],
                 actions: Callable[[datetime], Sequence[ActionRecord]], clock: Clock):
        """`answer(confirmation id, answer)` answers one of Muninn's questions (it may act on it);
        `actions(since)`: what Muninn did since then, to offer the undo right here."""
        self._messages = messages
        self._answer = answer
        self._actions = actions
        self._clock = clock

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        about = turn.about or {}
        if about.get("kind") == "project_question":
            answer = str(a.get("answer") or "").strip()
            if not answer:
                raise ValueError(turn.words("tools_conversation.what_answer"))
            h = self._messages.answer(str(about["ref"]), answer)
            if h is None:
                raise ValueError(turn.words("tools_conversation.already_answered"))
            return Outcome(turn.words("tools_conversation.passing_on", project=about["project"], answer=answer), handoff_id=h["id"])
        if about.get("kind") != "question":
            raise ValueError(turn.words("tools_conversation.no_question"))
        before = self._clock.now()
        c = self._answer(int(about["ref"]), str(a.get("answer")))
        if c is not None:  # what the answer did, so it can be undone right here
            done = [x for x in self._actions(before - timedelta(seconds=1)) if not x.undone]
            if done:
                return Outcome(turn.words("tools_conversation.answered", answer=c.answer), action_id=done[-1].id)
        if c is None:
            raise ValueError(turn.words("tools_conversation.options", options=turn.words.join(about.get("options") or [])))
        return Outcome(turn.words("tools_conversation.answered", answer=c.answer))


class RequestFeature:
    name: ClassVar[str] = "request_feature"
    description: ClassVar[str] = "The user wants Muninn itself to do something it does not do yet (e.g. book tickets for the user, a new kind of reminder), or has a comment on Muninn itself. args: {text: <the request>}"
    lookup: ClassVar[bool] = False

    def __init__(self, add: Callable[[str, str | None, Sequence[str]], int]):
        """`add(text, thread, via)` keeps the wish with the conversation it was asked in and the deep talk's content the
        round carried, and returns how many are waiting."""
        self._add = add

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        n = self._add(str(a["text"]), turn.thread, turn.via if turn.only_to is not None else ())
        return Outcome(turn.words("tools_conversation.feature_kept", recorded=turn.words("tools_conversation.recorded"), n=n))


def _pairs(text: str) -> set[str]:
    """The text's two-character runs: how much two pieces of Chinese (or any) text share."""
    t = "".join(text.split())
    return {t[i:i + 2] for i in range(len(t) - 1)} or ({t} if t else set())


class UndoAction:
    name: ClassVar[str] = "undo_action"
    description: ClassVar[str] = ("The user wants to take back something Muninn just did (e.g. \"undo\", \"not that one just now\", \"cancel the dentist reminder\", \"no need to note the idea\"). "
                                  "args: {what: <which one the user means, in the user's words; empty if the user did not name one>}")
    lookup: ClassVar[bool] = False

    def __init__(self, recent: Callable[[], Sequence[ActionRecord]], undo: Callable[[int], bool]):
        """`recent()`: what Muninn did lately that can be taken back, newest first; `undo(action id)`."""
        self._recent = recent
        self._undo = undo

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        recent = list(self._recent())
        if not recent:
            raise ValueError(turn.words("tools_conversation.nothing_to_undo"))
        what = str(a.get("what") or "").strip()
        if not what:
            picked = recent[0]  # nothing named: the latest
        else:
            want = _pairs(what)
            scored = [(len(want & _pairs(x.text)), x) for x in recent]
            best = max(s for s, _ in scored)
            if best == 0:
                raise ValueError(turn.words("tools_conversation.undo_not_found", what=what, recent=turn.words.join(x.text for x in recent[:3])))
            top = [x for s, x in scored if s == best]
            if len(top) > 1:
                raise ValueError(turn.words("tools_conversation.undo_which", among=turn.words.join(x.text for x in top[:3])))
            picked = top[0]
        if picked.id is None or not self._undo(picked.id):
            raise ValueError(turn.words("tools_conversation.cannot_undo", what=picked.text))
        return Outcome(turn.words("tools_conversation.undone", what=picked.text))


class AskProject:
    name: ClassVar[str] = "ask_project"
    description: ClassVar[str] = ("The user wants to know how things stand on a project's side, or an answer from it (e.g. what a session the user has open elsewhere is working on): Muninn sends the question to that project, "
                                  "where that project's session answers with the context it has at hand, and the reply comes back to this conversation. "
                                  "args: {project: <project name>, question: <what to ask, as one complete sentence>}")
    lookup: ClassVar[bool] = False

    def __init__(self, ask: Callable[[str, str, str | None], dict[str, Any]]):
        """`ask(project, question, thread)`: the question on its way (its handoff); ValueError when it cannot go."""
        self._ask = ask

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        project, question = str(a.get("project") or "").strip(), str(a.get("question") or "").strip()
        if not project or not question:
            raise ValueError(turn.words("tools_conversation.ask_what"))
        if turn.only_to is not None and project not in turn.only_to:
            source = turn.words.join(sorted(turn.only_to)) or turn.words("tools_conversation.a_deep_talk")
            raise ValueError(turn.words("tools_conversation.not_passed_on", source=source, project=project))
        h = self._ask(project, question, turn.thread)
        return Outcome(turn.words("tools_conversation.asked", project=project), handoff_id=h["id"])


class TalkToProject:
    name: ClassVar[str] = "talk_to_project"
    description: ClassVar[str] = ("The user wants to talk with a project: ask about its details or progress, or discuss something in that project (Muninn opens a session of its own in that project to take this conversation over; it can read the project's files and records, not the sessions the user has open elsewhere; "
                                  "once done it hands the conversation back by itself). args: {project: <project name>}")
    lookup: ClassVar[bool] = False
    acts_on: ClassVar[tuple[str, ...]] = ("conversation",)

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        project = str(a.get("project") or "").strip()
        if not project:
            raise ValueError(turn.words("tools_conversation.which_project"))
        return Outcome(turn.words("tools_conversation.lent", project=project), lend=project)


class ConversationTools:
    """The conversation's own tools: what to do today, a question about the user's own plans,
    news to analyse, a wish for Muninn, and taking back what Muninn did."""

    def __init__(self, next_items: Callable[[], Sequence[dict[str, Any]]], add_feature: Callable[[str, str | None, Sequence[str]], int],
                 undoable: Callable[[], Sequence[ActionRecord]], undo: Callable[[int], bool], can_lend: bool = True):
        """`undoable()`: what Muninn did lately that can be taken back, newest first. `can_lend`: a project's session
        can take a conversation over (a session runner is set up); without one, talking to a project is not offered."""
        self._next = next_items
        self._add_feature = add_feature
        self._undoable = undoable
        self._undo = undo
        self._can_lend = can_lend

    def tools(self) -> list[Any]:
        return [Today(self._next), Ask(), RecordFact(), RequestFeature(self._add_feature), UndoAction(self._undoable, self._undo),
                *([TalkToProject()] if self._can_lend else [])]
