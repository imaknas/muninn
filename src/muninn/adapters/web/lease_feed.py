"""Following the leases while the server runs: one thread per active lease
reads the runner's events, applies them under the same lock as every other
engine call, and hands them to the conversation views that are open (SSE).
A ticker gives back the leases that are done with, expires unanswered permission requests, and runs
what else follows a lease in the background (reporting an experiment that came back).
After a restart the feeds pick up from the last event each lease applied."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable, Sequence

from muninn.core.lease import ChannelLease, PermissionDesk
from muninn.core.ports import LeaseStore, RunnerError, SessionRunner

log = logging.getLogger(__name__)
TICK_S = 30
RETRY_S = (2, 5, 15, 30, 60)


class LeaseFeed:
    def __init__(self, store: LeaseStore, runner: SessionRunner, leases: ChannelLease, desk: PermissionDesk | None,
                 locked: Callable[[Callable[[], Any]], Any], background: Sequence[Callable[[], Any]] = ()):
        """`background`: run each tick, outside the engine lock (each takes the lock itself where it writes)."""
        self._store = store
        self._runner = runner
        self._leases = leases
        self._desk = desk
        self._locked = locked  # runs a function under the engine lock
        self._background = background
        self._threads: dict[str, threading.Thread] = {}
        self._subs: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
        self._guard = threading.Lock()
        self._stop = threading.Event()

    # ── conversation views listening ────────────────────────────────────────

    def subscribe(self, lease_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._guard:
            self._subs.setdefault(lease_id, []).append((asyncio.get_running_loop(), q))
        self.ensure()
        return q

    def unsubscribe(self, lease_id: str, q: asyncio.Queue) -> None:
        with self._guard:
            self._subs[lease_id] = [s for s in self._subs.get(lease_id, []) if s[1] is not q]

    def watching(self, lease_id: str) -> int:
        """How many conversation views are open on this lease now (what is published reaches only those)."""
        with self._guard:
            return len(self._subs.get(lease_id, []))

    def publish(self, lease_id: str, ev: dict[str, Any]) -> None:
        """Hands an event to the conversation views open on this lease (the runner's, or the app's own)."""
        with self._guard:
            subs = list(self._subs.get(lease_id, []))
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, ev)
            except (RuntimeError, asyncio.QueueFull):  # that view went away or fell behind
                pass

    # ── following the runner ────────────────────────────────────────────────

    def start(self) -> None:
        threading.Thread(target=self._ticker, name="lease-ticker", daemon=True).start()
        self.ensure()

    def stop(self) -> None:
        self._stop.set()

    def ensure(self) -> None:
        """A feed for every active lease (new ones included)."""
        for lease in self._store.leases():
            lid = lease["lease_id"]
            with self._guard:
                running = lid in self._threads and self._threads[lid].is_alive()
                if not running:
                    t = threading.Thread(target=self._follow, args=(lid,), name=f"lease-{lid[:8]}", daemon=True)
                    self._threads[lid] = t
                    t.start()

    def _follow(self, lease_id: str) -> None:
        tries = 0
        while not self._stop.is_set():
            lease = self._store.lease(lease_id)
            if lease is None or lease.get("ended"):
                return
            try:
                for ev in self._runner.events(lease_id, int(lease["last_seq"] or 0)):
                    tries = 0
                    applied = self._locked(lambda ev=ev: self._leases.on_event(ev))
                    if applied or ev.get("type") in ("text_delta", "tool_use", "tool_result"):
                        self.publish(lease_id, ev)
                    if applied and ev.get("type") == "turn_end":
                        self._leases.flush_relays(lease_id)  # what Muninn waited to tell it, now it has answered
                    if applied and ev.get("type") == "turn_end" and (self._store.lease(lease_id) or {}).get("end_asked"):
                        self._leases.return_idle()  # the user was done with it: given back now its last answer is written
                    if applied and ev.get("type") == "state" and ev.get("state") == "detached":
                        # a talk gone into again while it wrote its handover goes on with the same session, at once
                        if (why := self._leases.resume_waiting(lease_id)) is not None:
                            self.publish(lease_id, {"type": "error", "error": "resume_failed", "detail": why, "lease_id": lease_id})
                    if self._stop.is_set():
                        return
            except RunnerError as e:
                if e.category == "not_found":
                    log.warning("lease %s is gone from the runner; giving the thread back", lease_id)
                    self._locked(lambda: self._leases.forget(lease_id))
                    self.publish(lease_id, {"type": "state", "state": "detached", "lease_id": lease_id})
                    return
                log.info("lease %s feed interrupted: %s", lease_id, e)
            except Exception:  # keep following whatever one event did
                log.exception("lease %s feed failed", lease_id)
            time.sleep(RETRY_S[min(tries, len(RETRY_S) - 1)])
            tries += 1

    def _ticker(self) -> None:
        while not self._stop.wait(TICK_S):
            try:
                self._leases.return_idle()  # calls the runner: not under the engine's lock
                for lease_id, why in self._leases.sweep_waiting():  # what waits on a handover is never dropped
                    self.publish(lease_id, {"type": "error", "error": "resume_failed", "detail": why, "lease_id": lease_id})
                if self._desk is not None:
                    self._locked(self._desk.expire)
                for run in self._background:
                    run()
                self.ensure()
            except Exception:
                log.exception("lease ticker failed")
