"""What the projects tell the user, and the user's answers back.

Projects reach the user through the mailbox their own records system keeps,
which posts here: a question for the user, progress on something handed
off, or that it is done. A question waits on the home page until the user
answers it, and the answer leaves as a handoff to the project that asked
(`reply_to` names the question). 'done' with a handoff id closes that
handoff. A project saying he decided a question in its own session is its
word, not his answer: the question still waits, with what the project says
beside it; once the project no longer asks it, it leaves his list and he is
asked once whether that is what he decided ('done' with a question id is
both; 'drop' only no longer asking). His yes adopts the project's claim; his
no puts the question back for his own answer, and the project is told to look
again at what it did on the wrong one. A lent conversation's
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
from .models import Confirmation, InboxItem
from .ports import Clock, ConfirmationStore, InboxStore, ProjectMessageStore
from .language import Language

VERSION = 1  # of the message format; a post may leave it out
TYPES = ("question", "progress", "done", "task", "drop")
MAX_TEXT = 4000
DECISION_CLAIM = "decision_claim"  # the question whether a project's word that he decided is right


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


class MessageStorage(ProjectMessageStore, InboxStore, ConfirmationStore, Protocol):
    """The messages, the inbox where progress and done land, and the question whether a project's claim is right."""


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
                if m["type"] == "done":  # the project's word that he answered it there
                    self.claimed(m["question"], m["project"], {"id": None, "text": m["text"]})
                self.withdrawn(m["question"], m["project"], "settled" if m["type"] == "done" else "not_needed")
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
        """Each with `note` when a project said he decided it: what it says, or that he said it was not so."""
        return [{**q, "note": self._claim_note(q)} if q.get("claim") else q
                for q in self._store.project_messages(type="question", status="open")]

    def _claim_note(self, q: dict[str, Any]) -> str:
        c, w = q["claim"], self.words
        if c.get("wrong"):
            return w("project_messages.claim_wrong", project=q["project"], answer=c.get("text") or "")
        return w("project_messages.claim_said", project=q["project"], answer=c.get("text") or "")

    def claimed(self, qid: str, project: str, claim: dict[str, Any]) -> None:
        """The project says he decided its question (`claim`: {id: the claim's own record id, None for a v1 message;
        text; option}): kept beside the question, which still waits for him; asked about once the project no longer
        asks it."""
        if self._question(qid, project) is None:
            return
        q = self._store.claim_project_message(qid, {**claim, "text": str(claim.get("text") or "")[:MAX_TEXT]}, self._clock.now())
        if q is not None and q["status"] == "withdrawn":  # the project stopped asking before its claim came
            self._ask_about_claim(q)

    def withdrawn(self, qid: str, project: str, reason: str | None) -> None:
        """The project no longer asks it: it leaves his list, never as his answer. When the project says it was decided
        in its own session (`reason` settled, or a claim came), he is asked once whether it was."""
        if self._question(qid, project) is None:
            return
        q = self._store.move_project_message(qid, "withdrawn", self._clock.now(), was="open")
        if q is not None and (q.get("claim") or reason == "settled"):
            self._ask_about_claim(q)

    def _question(self, qid: str, project: str) -> dict[str, Any] | None:
        return next((q for q in self._store.project_messages(type="question") if q["id"] == qid and q["project"] == project), None)

    def _ask_about_claim(self, q: dict[str, Any]) -> None:
        """Once per question: a claim that comes later takes the place of the question still waiting, never of one he
        answered."""
        key, w, claim = f"{DECISION_CLAIM}:{q['id']}", self.words, q.get("claim") or {}
        asked = [c for c in self._store.confirmations(pending_only=False) if c.key == key]
        if any(c.answer for c in asked):
            return
        for c in asked:  # asked without the claim that has come since
            self._store.answer_confirmation(c.id, "", self._clock.now())
        question = w("project_messages.claim_check", project=q["project"], text=q["text"], answer=claim["text"]) if claim.get("text") \
            else w("project_messages.claim_check_no_answer", project=q["project"], text=q["text"])
        self._store.add_confirmation(Confirmation(None, self._clock.now(), DECISION_CLAIM, question,
                                                  (w("project_messages.claim_right"), w("project_messages.claim_not_right")),
                                                  {"question": q["id"], "project": q["project"], "claim": claim.get("id")}, key=key))

    def claim_answered(self, c: Confirmation, answer: str) -> None:
        """His yes adopts the project's claim (a v1 message's, or a withdrawal with no answer, leaves nothing to tell);
        his no puts the question back on his list for his own answer."""
        q = self._question(str(c.data["question"]), str(c.data["project"]))
        if q is None:
            return
        claim = q.get("claim") or {}
        if answer == c.options[0]:
            if claim.get("id") and claim.get("text"):
                self._handoffs.add(project=q["project"], item=q["text"], fact=claim["text"], effect="answers", reply_to=q["id"],
                                   option=claim.get("option"), why=self.words("project_messages.confirmed_why"),
                                   decided={"by": "user", "confirmed": c.id, "claim": claim["id"]})
            return
        now = self._clock.now()
        self._store.claim_project_message(q["id"], {**claim, "text": claim.get("text") or "", "wrong": True}, now)
        self._store.move_project_message(q["id"], "open", now, was="withdrawn")

    def _said(self, q: dict[str, Any], why: str) -> dict[str, Any]:
        """Why his answer goes to the project; to a question it got wrong, also that it is to look again at what it did
        (`recheck`: told as a note of its own where the project keeps records)."""
        claim = q.get("claim") or {}
        if not claim.get("wrong"):
            return {"why": why}
        recheck = self.words("project_messages.recheck", answer=claim["text"]) if claim.get("text") \
            else self.words("project_messages.recheck_no_answer")
        return {"why": f"{why} {recheck}", "recheck": recheck}

    def dismiss(self, mid: str) -> dict[str, Any] | None:
        """The user lets a question go (it was settled in the conversation, or no
        longer matters). The project hears it needs no answer, so it closes the
        question on its side instead of asking it again. The handoff, or None if
        there is no open question with that id."""
        q = self._store.dismiss_project_message(mid, self._clock.now())
        if q is None:
            return None
        return self._handoffs.add(project=q["project"], item=q["text"], fact=self.words("project_messages.no_answer_needed"),
                                  effect="answers", reply_to=q["id"], dismissed=True,
                                  **self._said(q, self.words("project_messages.dismissed_why")))

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
                                  **self._said(q, self.words("project_messages.answered_why")))
