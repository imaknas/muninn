"""What Muninn and the projects are asking the user, as topics of a discussion: Muninn's own
questions (confirmation cards) and the projects' questions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .models import ActionRecord, Confirmation
from .ports import Clock, ConfirmationStore
from .privacy import UNDERSTANDING_DOMAIN, Cleared
from .project_messages import ProjectMessages


class Questions:
    """The questions as an actor: one as the topic of a discussion, and the tool to answer it."""

    kinds = ("question", "project_question")

    def __init__(self, store: ConfirmationStore, pending: Callable[[], Sequence[Confirmation]], messages: ProjectMessages,
                 project_domain: Callable[[str], str | None], clear: Callable[[str], Cleared | None],
                 window_topic: Callable[[Confirmation], dict[str, Any]],
                 handlers: Mapping[str, Callable[[Confirmation, str], Any]], feedback: Callable[[str, dict[str, Any]], None],
                 actions: Callable[[datetime], Sequence[ActionRecord]], clock: Clock):
        """`pending`: Muninn's open questions; `messages`: the projects' questions and the answers
        going back; `project_domain`: a project's domain; `clear`: the work check (may the cloud
        read this text); `window_topic`: a window question with what is known about that window;
        `handlers`: per kind of question, what an answer does (each actor answers its own);
        `feedback(ref, data)` keeps the answer on the case that asked; `actions(since)`: what Muninn did since."""
        self._store = store
        self._pending = pending
        self._messages = messages
        self._project_domain = project_domain
        self._clear = clear
        self._window_topic = window_topic
        self._handlers = handlers
        self._feedback = feedback
        self._actions = actions
        self._clock = clock

    def answer(self, cid: int, answer: str) -> Confirmation | None:
        """The user's answer to one of Muninn's questions: recorded, and carried out by the actor
        the question belongs to. None when it is no longer open or not one of its options."""
        pending = next((c for c in self._pending() if c.id == cid), None)
        if pending is None or answer not in pending.options:
            return None
        c = self._store.answer_confirmation(cid, answer, self._clock.now())
        if c is None:
            return None
        self._feedback(f"confirm:{cid}", {"kind": "answer", "question": c.question, "answer": answer,
                                          "accepted": answer == c.options[0]})
        if (handle := self._handlers.get(c.kind)) is not None:
            handle(c, answer)
        return c

    def tools(self) -> list[Any]:
        from .tools.conversation import AnswerQuestion

        return [AnswerQuestion(self._messages, self.answer, self._actions, self._clock)]

    def topic(self, kind: str, ref: str) -> dict[str, Any] | None:
        if kind == "project_question":
            q = next((x for x in self._messages.open_questions() if x["id"] == ref), None)
            if q is None:
                return None
            return {"kind": "project_question", "ref": q["id"], "domain": self._question_domain(q["project"], q["text"]),
                    "project": q["project"], "question": q["text"], **({"project_says": q["note"]} if q.get("note") else {})}
        c = next((x for x in self._pending() if str(x.id) == str(ref)), None)
        if c is None:
            return None
        if c.kind == "window_class":
            return self._window_topic(c)
        # attribution questions are about unknown activity: possibly work, so never the cloud's
        domain = None if c.kind == "attribution" else (c.data.get("domain") or (c.data.get("action") or {}).get("domain")
                                                        or str(c.data.get("deadline", "")).partition("/")[0] or None)
        return {"kind": "question", "ref": c.id, "domain": domain, "question": c.question, "options": list(c.options)}

    def _question_domain(self, project: str, text: str) -> str | None:
        """The domain a project's question is about, for the cloud gate: the
        project's own domain when the rules know it, else the work check's
        verdict on the question (None, so local only, when it cannot clear it)."""
        domain = self._project_domain(project)
        if domain is not None:
            return domain
        return UNDERSTANDING_DOMAIN if self._clear(f"{project}: {text}") else None
