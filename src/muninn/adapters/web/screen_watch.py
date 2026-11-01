"""Telling every open screen when what it shows changed, so what was said or done on one device (or by Muninn on
its own, in the scheduled tick) shows on the others at once. The screens never ask: each keeps one stream open
while it is in view, and is told "changed". Here, a thread looks at two numbers the store keeps (its own writes,
and the database's version, which moves when another process writes); only when one moved is the fingerprint of
what the screens show worked out again, and only when that changed is anyone told."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable

log = logging.getLogger(__name__)
LOOK_S = 1.0  # how often the store's two numbers are looked at (no query is made unless they moved)


class ScreenWatch:
    def __init__(self, version: Callable[[], Any], stamp: Callable[[], str]):
        """`version()`: what the store says about having been written to (cheap); `stamp()`: the fingerprint of
        what the screens show (a few queries), worked out only when the version moved."""
        self._version = version
        self._stamp = stamp
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._guard = threading.Lock()
        self._stop = threading.Event()
        self._seen: Any = None
        self._last: str | None = None

    def start(self) -> None:
        self.check()  # what the screens show now is the baseline, taken before anything else happens
        threading.Thread(target=self._watch, name="screen-watch", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=10)
        with self._guard:
            self._subs.append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._guard:
            self._subs = [s for s in self._subs if s[1] is not q]

    def check(self) -> bool:
        """Looks once; True when the screens were told."""
        version = self._version()
        if version == self._seen:
            return False
        self._seen = version
        stamp = self._stamp()
        if stamp == self._last:
            return False
        first, self._last = self._last is None, stamp
        if first:
            return False  # what they show now is what they were drawn from
        with self._guard:
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, stamp)
            except (RuntimeError, asyncio.QueueFull):  # that screen went away or has a "changed" waiting already
                pass
        return True

    def _watch(self) -> None:
        while not self._stop.wait(LOOK_S):
            try:
                self.check()
            except Exception:
                log.exception("screen watch failed")
