"""Whether pushes reach a phone. The push service taking a notification says nothing of a phone showing it (a
subscription of an app since removed is still taken for a while), so a phone says when it showed one, and pushes
taken with none shown for a while mean they no longer arrive: said in the settings, to the platform asking, and once a
day among the reminders, where the user sees it next time they open the app (a push about pushes would not arrive)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from .language import Language
from .models import InboxItem
from .ports import Clock, InboxStore, StateStore
from .timewin import local_date

UNSHOWN = "push:unshown_since"  # when the first push the service took, of those no phone has shown yet, was sent
GRACE = timedelta(hours=6)  # a phone off or out of reach shows what was pushed when it is back; quiet hours don't count
STEP = timedelta(minutes=5)  # how finely the time gone by is told apart from quiet hours


def last_shown_key(device: int) -> str:
    return f"push:last_ack:{device}"


class PushHealthStorage(StateStore, InboxStore, Protocol):
    def claim(self, marker: str, at: datetime) -> bool: ...


class PushHealth:
    def __init__(self, store: PushHealthStorage, clock: Clock, tz: str, *, words: Language, quiet: Callable[[datetime], bool]):
        """`quiet(at)`: whether `at` is in the user's quiet hours, when a phone may well be off with nothing wrong."""
        self._store, self._clock, self._tz = store, clock, tz
        self.words = words
        self._quiet = quiet

    def taken(self) -> None:
        """The push service took a notification for at least one subscription."""
        now = self._clock.now().isoformat()
        self._store.update_state(UNSHOWN, lambda since: since or now)

    def shown(self, device: int) -> None:
        """A phone showed one."""
        now = self._clock.now().isoformat()
        self._store.set_state(last_shown_key(device), now)
        self._store.set_state(UNSHOWN, None)

    def status(self) -> dict[str, Any]:
        """`reaching`: False once a push has gone unshown longer than a phone can be away; `unshown_since`."""
        since = self._store.get_state(UNSHOWN)
        return {"reaching": since is None or not self._overdue(datetime.fromisoformat(since)), "unshown_since": since}

    def _overdue(self, since: datetime) -> bool:
        """Whether more than GRACE went by since then, the quiet hours left out."""
        now = self._clock.now()
        gone = timedelta()
        at = since
        while at < now:
            if not self._quiet(at):
                gone += min(STEP, now - at)
                if gone > GRACE:
                    return True
            at += STEP
        return False

    def jobs(self) -> list[Any]:
        from .actors import Job

        return [Job("telling when pushes no longer reach a phone", self.check, phase="after")]

    def check(self) -> InboxItem | None:
        """Once a day while pushes do not arrive: a reminder that says so."""
        st = self.status()
        now = self._clock.now()
        if st["reaching"] or not self._store.claim(f"push-unshown:{local_date(now, self._tz).isoformat()}", now):
            return None
        hours = int((now - datetime.fromisoformat(st["unshown_since"])).total_seconds() // 3600)
        return self._store.add_inbox(InboxItem(None, now, "muninn", "alert", self.words("push.not_reaching", hours=hours),
                                               self.words("push.not_reaching_detail"), signal_key="push:not-reaching"))
