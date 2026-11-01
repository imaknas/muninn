"""Giving Muninn an HTTPS address with Tailscale from the settings page: whether this computer's Tailscale can, and
`tailscale serve` run for it. The address is for the tailnet only (serve, never funnel): the user's own devices."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

TIMEOUT_S = 20
PORTS = range(8444, 8454)  # the HTTPS ports tried, first free one taken (8444 is what the docs name)
MAC_APP = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")  # the app's CLI, not on PATH


class Tailscale:
    def __init__(self, run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 which: Callable[[str], str | None] = shutil.which, mac_app: Path = MAC_APP):
        self._run, self._which, self._mac_app = run, which, mac_app

    def _binary(self) -> str | None:
        return self._which("tailscale") or (str(self._mac_app) if self._mac_app.is_file() else None)

    def _json(self, *args: str) -> Any:
        binary = self._binary()
        assert binary is not None
        try:
            done = self._run([binary, *args, "--json"], capture_output=True, text=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return None
        if done.returncode != 0:
            return None
        try:
            return json.loads(done.stdout or "null")
        except ValueError:
            return None

    def state(self, port: int) -> dict[str, Any]:
        """Whether an HTTPS address can be made here: {"ready": True, "url"} with the address it would be (or already
        is), else {"ready": False, "why"}: not_installed, not_running, no_https or no_port."""
        if self._binary() is None:
            return {"ready": False, "why": "not_installed"}
        status = self._json("status") or {}
        if status.get("BackendState") != "Running":
            return {"ready": False, "why": "not_running"}
        name = str((status.get("Self") or {}).get("DNSName") or "").rstrip(".")
        if not name or not status.get("CertDomains"):
            return {"ready": False, "why": "no_https"}
        served = ((self._json("serve", "status") or {}).get("Web") or {})
        for https in PORTS:
            proxy = (((served.get(f"{name}:{https}") or {}).get("Handlers") or {}).get("/") or {}).get("Proxy")
            if proxy in (None, f"http://127.0.0.1:{port}"):
                return {"ready": True, "url": f"https://{name}:{https}", "https_port": https, "served": proxy is not None}
        return {"ready": False, "why": "no_port"}

    def unserve(self, port: int) -> list[str]:
        """Each HTTPS address that serves Muninn's port, taken off the tailnet: the addresses."""
        if self._binary() is None:
            return []
        served = ((self._json("serve", "status") or {}).get("Web") or {})
        gone = []
        for where, conf in served.items():
            proxy = (((conf or {}).get("Handlers") or {}).get("/") or {}).get("Proxy")
            https = where.rsplit(":", 1)[-1]
            if proxy == f"http://127.0.0.1:{port}" and https.isdigit():
                try:
                    done = self._run([self._binary(), "serve", f"--https={https}", "off"], capture_output=True, text=True, timeout=TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    continue
                if done.returncode == 0:
                    gone.append(f"https://{where}")
        return gone

    def serve(self, port: int) -> str:
        """Serves Muninn's port at its tailnet HTTPS address (kept across restarts); the address. RuntimeError, said."""
        st = self.state(port)
        if not st["ready"]:
            raise RuntimeError(st["why"])
        if not st["served"]:
            try:
                done = self._run([self._binary(), "serve", "--bg", f"--https={st['https_port']}", f"http://127.0.0.1:{port}"],
                                 capture_output=True, text=True, timeout=TIMEOUT_S)
            except subprocess.TimeoutExpired as e:
                # Serve not yet on for this tailnet: the CLI says where to turn it on, and waits
                said = " ".join(x.decode(errors="replace") if isinstance(x, bytes) else x or "" for x in (e.stdout, e.stderr))
                link = re.search(r"https://login\.tailscale\.com/\S+", said)
                raise RuntimeError(f"serve_off {link.group(0) if link else 'https://login.tailscale.com/admin'}") from None
            if done.returncode != 0:
                raise RuntimeError((done.stderr or done.stdout or "tailscale serve failed").strip().splitlines()[-1])
        return str(st["url"])
