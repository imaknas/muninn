"""Requests that kept someone waiting, said in the log: the log has no line per request, and "it is slow" can otherwise
only be guessed at afterwards. How long until the answer starts (a stream stays open for as long as it is watched)."""

from __future__ import annotations

import time
from typing import Any, Callable

OVER_S = 1.0


class SlowRequests:
    def __init__(self, app: Any, log: Callable[..., None], over: float = OVER_S, timer: Callable[[], float] = time.monotonic):
        self._app, self._log, self._over, self._timer = app, log, over, timer

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = self._timer()

        async def timed(message: Any) -> None:
            if message["type"] == "http.response.start" and (took := self._timer() - started) >= self._over:
                self._log("slow request: %s %s took %.1fs", scope["method"], scope["path"], took)
            await send(message)

        await self._app(scope, receive, timed)
