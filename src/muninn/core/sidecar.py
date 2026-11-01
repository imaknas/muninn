"""Muninn beside a deep talk. While a conversation is lent to a project's session, what the user says there
still goes to that session. The same judgment that keeps the sentence in the talk (Topics.choose, lent_turn)
also says what in it is for Muninn rather than the project: a reminder, something to keep, a task or a fact
about another part of their life. Muninn does that part in the background, so the user never waits on it,
and says so in one line beside the talk; the session is told it need not. What of Muninn's answer is the
project's (one sentence the model writes, for_project) the session is told as a message of its own.

It is read with the conversation's own history (what was said before it was lent), what it is about, and the
talk's latest lines (his, the session's and Muninn's: 「你去跟 Muninn 說這個問題」 is what the session just said), so it
finds what it refers to; the session's lines are read, never kept in Muninn's conversation (what Muninn said beside
the talk is, once the talk is over), and only that project may hear of what comes of them. It is carried out straight away, not as a round of the conversation: the user has not moved
on, the talk is not given back for it, and nothing done there sends the conversation anywhere else."""

from __future__ import annotations

from typing import Any, Callable, ContextManager

from .commands import CommandRunner
from .lease import talk_lines
from .ports import Clock, LeaseStore
from .language import Language


class Sidecar:
    def __init__(self, understand: Callable[[str, str, dict[str, Any]], dict[str, Any]], commands: Callable[[], CommandRunner], store: LeaseStore,
                 clock: Clock, serialize: Callable[[Callable[[], Any]], Any], tell: Callable[[str, dict[str, Any]], None], *,
                 relay: Callable[[str, str], Any], working: Callable[[str], ContextManager[Any]],
                 label: Callable[[str], str] = lambda project: project, words: Language):
        """`understand(thread, said, beside)`: a round of that conversation understood beside the talk `beside`
        ({lease, project, called, talk}; Conversation.understand_turn, which changes nothing); `serialize`: runs a
        change under the engine lock; `tell(lease_id, event)`: to the views open on that talk; `relay(lease_id,
        text)`: tells the talk's session a sentence (ChannelLease.relay; calls the runner); `working(lease_id)`:
        held while Muninn does its part, so the talk is not given back meanwhile; `label(project)`: what the user
        calls the project."""
        self.words = words
        self._understand = understand
        self._relay = relay
        self._working = working
        self._commands = commands
        self._store = store
        self._clock = clock
        self._serialize = serialize
        self._tell = tell
        self._label = label

    def act(self, lease_id: str, thread: str, part: str) -> str | None:
        """Does `part` (what in the sentence was for Muninn), said in one line and kept beside the talk; what of it
        is the project's, its session is told."""
        with self._working(lease_id):
            return self._act(lease_id, thread, part)

    def _act(self, lease_id: str, thread: str, part: str) -> str | None:
        lease = self._store.lease(lease_id) or {}
        project = str(lease.get("project") or "")
        called = self._label(project)
        beside = {"lease": lease_id, "project": project, "called": called, "talk": talk_lines(self._store.lease_turns(lease_id), called)}
        plan = self._understand(thread, part, beside)["plan"]
        if plan is None:
            return None
        r = self._serialize(lambda: self._commands().execute({**plan, "thread": thread}))  # done for that conversation
        # beside the talk Muninn only does what it was handed: when it did something, what happened is all it says
        # (its sentence tends to take up the talk itself); when it only answers, a sentence or two
        said = "" if plan["actions"] else first_sentences(r.reply, SAID_MAX, self.words)
        line = "\n".join(x for x in [said, *(o.text for o in r.outcomes)] if x)
        if line:
            self._serialize(lambda: self._store.add_lease_turn(lease_id, "muninn", line, self._clock.now()))
            self._tell(lease_id, {"type": "sidecar", "text": line})
        if plan.get("for_project"):  # after its answer, as the user sees it; outside the lock: it calls the runner
            self._relay(lease_id, plan["for_project"])
        return line or None


SAID_MAX = 2  # sentences Muninn says beside a talk when it only answers


def first_sentences(text: str, n: int, words: Language) -> str:
    """The first `n` sentences of `text`, as `words` cuts and runs them together."""
    return words("sidecar.first_sentences", parts=[p for p in words.sentences(text or "") if p.strip()][:n]).strip()


def aside(part: str, words: Language) -> str:
    """What the session is told with the sentence, so it does not do what Muninn took."""
    return words("sidecar.aside", part=part)
