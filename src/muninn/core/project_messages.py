"""What the projects tell the user, and the user's answers back.

Projects reach the user through the mailbox their own records system keeps,
which posts here: a question for the user, progress on something handed
off, or that it is done. A question waits on the home page until the user
answers it, and the answer leaves as a handoff to the project that asked
(`reply_to` names the question). 'done' with a handoff id closes that
handoff; 'done' (or 'drop') with a question id closes that question, settled
where it was asked (the user answered it there). A lent conversation's
undecided questions are its handover's, kept with that conversation and asked
when the user goes into the talk again: the copies posted here wait nowhere.
A question the user has Muninn ask a project leaves as an 'asks'
handoff; what comes back with its id is said in the conversation it was asked from: its replies as the answer,
its closing the question (a 'done') only as having closed it unanswered, when nothing came before.
Messages are kept by the sender's id, so a retried post is a no-op.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Protocol

from .handoffs import Handoffs
from .tasks import TaskRegistry
from .models import InboxItem
from .ports import Clock, InboxStore, ProjectMessageStore
from .language import Language

VERSION = 1  # of the message format; a post may leave it out
TYPES = ("question", "progress", "done", "task", "drop")
MAX_TEXT = 4000


def parse(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A posted message, checked (contracts/messages.v1.schema.json). Raises
    ValueError on anything malformed."""
    if raw.get("v", VERSION) != VERSION:
        raise ValueError(f"unsupported message version {raw.get('v')!r}")
    mid, project, kind = raw.get("id"), raw.get("project"), raw.get("type")
    text = raw.get("text")
    if not isinstance(mid, str) or not mid.strip() or len(mid) > 200:
        raise ValueError("id must be a non-empty string")
    if not isinstance(project, str) or not project.strip() or len(project) > 100 or "/" in project:
        raise ValueError("project must be a project name")
    if kind not in TYPES:
        raise ValueError(f"type must be one of {', '.join(TYPES)}")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    hid = raw.get("handoff_id")
    if hid is not None and (isinstance(hid, bool) or not isinstance(hid, int)):
        raise ValueError("handoff_id must be an integer")
    task = raw.get("task")
    if task is not None and (not isinstance(task, str) or not task.strip() or len(task) > 200):
        raise ValueError("task must be a task key")
    question = raw.get("question")
    if question is not None and (not isinstance(question, str) or not question.strip() or len(question) > 200):
        raise ValueError("question must be a question's message id")
    if kind == "drop" and task is None and question is None:
        raise ValueError("drop needs the task key or the question id")
    inferred = raw.get("inferred", False)
    if not isinstance(inferred, bool):
        raise ValueError("inferred must be true or false")
    extra = {k: str(raw[k])[:MAX_TEXT] for k in ("due", "context", "new_reason") if isinstance(raw.get(k), str) and raw[k].strip()}
    return {"id": mid.strip(), "project": project.strip(), "type": kind, "text": text.strip()[:MAX_TEXT], "handoff_id": hid,
            "task": task.strip() if task else None, "question": question.strip() if question else None, "inferred": inferred, **extra}


class MessageStorage(ProjectMessageStore, InboxStore, Protocol):
    """The messages, and the inbox where progress and done land."""


class ProjectMessages:
    def __init__(self, store: MessageStorage, clock: Clock, handoffs: Handoffs, tasks: TaskRegistry | None = None,
                 projects: Callable[[], set[str]] = set, answered: Callable[[dict[str, Any], dict[str, Any]], None] | None = None, *, words: Language):
        """`projects()`: the projects a question can go to; `answered(handoff, message)`: says a project's answer in the
        conversation the question was asked from."""
        self.words = words
        self._store = store
        self._clock = clock
        self._handoffs = handoffs
        self._tasks = tasks
        self._projects = projects
        self._answered = answered

    def ask(self, project: str, question: str, thread: str | None) -> dict[str, Any]:
        """The user's question for a project, on its way: the session in use there answers it."""
        if project not in self._projects():
            raise ValueError(self.words("project_messages.no_project", project=project))
        return self._handoffs.add(project=project, item=None, fact=question[:MAX_TEXT], effect="asks", thread=thread)

    def tools(self) -> list[Any]:
        from .tools.conversation import AskProject

        return [AskProject(self.ask)] if self._answered is not None else []

    def receive(self, raw: Iterable[Mapping[str, Any]]) -> int:
        """Stores the messages not seen before and acts on them. All are
        checked first, so a bad one rejects the whole post."""
        messages = [parse(m) for m in raw]
        now = self._clock.now()
        new = 0
        for m in messages:
            if not self._store.add_project_message(m, now):
                continue
            new += 1
            asked = self._handoffs.get(m["handoff_id"]) if m["handoff_id"] is not None else None
            if asked is not None and asked.get("effect") == "asks" and self._answered is not None:
                # what the project says back is said where it was asked (follow-ups too), not listed anywhere else;
                # its closing the question is said only when no answer came before it
                if m["type"] != "done" or asked["status"] != "done":
                    self._answered(asked, m)
                self._handoffs.set_status(asked["id"], "done")
                continue
            if m["type"] == "question":
                if str(m.get("context") or "").startswith("lease:"):  # the talk's handover holds it, with the talk
                    self._store.settle_project_message(m["id"], self.words("project_messages.in_lease_handover"), now)
                continue  # waits on the home page for an answer
            if m["type"] in ("done", "drop") and m["question"]:
                self._store.settle_project_message(m["question"], m["text"], now)  # settled where it was asked
                continue
            if self._tasks is not None and (m["type"] in ("task", "drop") or (m["type"] == "done" and m["task"])):
                self._tasks.receive(m)  # tasks show in their own list; no inbox line each
                continue
            if m["type"] == "done" and m["handoff_id"] is not None:
                self._handoffs.set_status(m["handoff_id"], "done")
            if m["type"] == "progress" and str(m.get("context", "")).startswith("lease:"):
                continue  # a lent conversation's handover: it is already in that conversation, kept local there
            title = self.words("project_messages.inbox_title", project=m["project"], kind=m["type"])
            self._store.add_inbox(InboxItem(None, now, "muninn", "info", title, m["text"], {"project_message": m["id"]}))
        return new

    def amend_question(self, qid: str, text: str) -> bool:
        """The project changed what it asks: the open question reads as it now does."""
        return self._store.amend_project_message(qid, text.strip()[:MAX_TEXT], self._clock.now())

    def open_questions(self) -> list[dict[str, Any]]:
        return self._store.project_messages(type="question", status="open")

    def dismiss(self, mid: str) -> dict[str, Any] | None:
        """The user lets a question go (it was settled in the conversation, or no
        longer matters). The project hears it needs no answer, so it closes the
        question on its side instead of asking it again. The handoff, or None if
        there is no open question with that id."""
        q = self._store.dismiss_project_message(mid, self._clock.now())
        if q is None:
            return None
        return self._handoffs.add(project=q["project"], item=q["text"], fact=self.words("project_messages.no_answer_needed"),
                                  effect="answers", reply_to=q["id"], dismissed=True, why=self.words("project_messages.dismissed_why"))

    def answer(self, mid: str, answer: str) -> dict[str, Any] | None:
        """The handoff carrying the answer back, or None if there is no open
        question with that id."""
        answer = answer.strip()
        if not answer:
            raise ValueError("answer must not be empty")
        q = self._store.answer_project_message(mid, answer[:MAX_TEXT], self._clock.now())
        if q is None:
            return None
        return self._handoffs.add(project=q["project"], item=q["text"], fact=answer[:MAX_TEXT],
                                  quote=answer[:MAX_TEXT], effect="answers", reply_to=q["id"],
                                  why=self.words("project_messages.answered_why"))
