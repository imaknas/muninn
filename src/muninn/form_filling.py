"""Adding form filling from the settings page: the optional part that fills in application forms in a browser (the
`autofill` extra, and the Chromium it drives). It takes minutes and downloads a browser, so it runs on a thread of
its own and the page asks how it goes; when it is done Muninn starts again to use it."""

from __future__ import annotations

import threading
from typing import Any, Callable

from muninn.upgrade import UpgradeError

EXTRA = "autofill"
BROWSER = ["-m", "playwright", "install", "chromium"]


class FormFilling:
    def __init__(self, installation: Any, version: str, ready: Callable[[], bool], restart: Callable[[], bool],
                 thread: Callable[[Callable[[], None]], Any] = lambda work: threading.Thread(target=work, daemon=True).start()):
        """`installation`: how this Muninn was installed (installs.installation()); `version`: the running one,
        installed again with the extra; `ready()`: whether forms can be filled now (the extra and its browser);
        `restart()`: starts Muninn again, False when the user has to; `thread(work)`: runs the install apart."""
        self._installation, self._version, self._ready, self._restart, self._thread = installation, version, ready, restart, thread
        self._lock = threading.Lock()
        self._state, self._said = "idle", ""

    def state(self) -> dict[str, Any]:
        """{ready, state: idle | installing | failed | restarting | restart_yourself, said, refused}."""
        with self._lock:
            state, said = self._state, self._said
        return {"ready": state == "idle" and self._ready(), "state": state, "said": said,
                "refused": self._installation.extra_refusal()}

    def start(self) -> dict[str, Any]:
        """Starts installing it, unless that is already under way."""
        with self._lock:
            if self._state == "installing":
                return {"state": "installing"}
            self._state, self._said = "installing", ""
        self._thread(self._work)
        return {"state": "installing"}

    def _work(self) -> None:
        try:
            self._installation.add_extra(EXTRA, self._version, BROWSER)
        except Exception as e:  # noqa: BLE001 — whatever stopped it is said, never left "installing"
            with self._lock:
                self._state, self._said = "failed", str(e) if isinstance(e, UpgradeError) else f"{type(e).__name__}: {e}"
            return
        restarting = self._restart()
        with self._lock:
            self._state = "restarting" if restarting else "restart_yourself"
