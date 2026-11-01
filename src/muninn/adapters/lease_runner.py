"""The session runner over its local HTTP API (contracts/vendor/records/lease-runner.v2):
a Bearer token, JSON bodies, and the lease's events as server-sent events. The same runner
opens the sandboxes ideas are explored in."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Iterator, Sequence

import httpx

from muninn.core.models import Attachment
from muninn.core.ports import RunnerError, SandboxRefused

V = 2  # the runner's contract (contracts/vendor/records/lease-runner.v2.schema.json): steps by kind, quota windows


def runner_error(data: dict[str, Any], status: int) -> RunnerError:
    return RunnerError(str(data.get("error") or "internal"), str(data.get("detail") or f"HTTP {status}"))


class HttpSessionRunner:
    def __init__(self, url: str, token_file: str | Path, timeout: float = 30.0):
        self._url = url.rstrip("/")
        self._token_file = Path(token_file).expanduser()
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        try:
            token = self._token_file.read_text().strip()
        except OSError as e:
            raise RunnerError("unauthorized", f"runner token unreadable: {e}") from e
        return {"Authorization": f"Bearer {token}"}

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            r = httpx.request(method, f"{self._url}{path}", headers=self._headers(),
                              json={"v": V, **body} if body is not None else None, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise RunnerError("unreachable", str(e)) from e
        data: dict[str, Any] = {}
        if r.content:
            try:
                data = r.json()
            except ValueError:
                data = {}
        if r.status_code >= 400:
            raise runner_error(data, r.status_code)
        return data

    def start(self, project: str, session_id: str, thread: str, *, resume: bool = False,
              mode: str = "discuss") -> dict[str, Any]:
        return self._call("POST", "/v1/leases", {"project": project, "mode": mode, "session_id": session_id,
                                                 "thread": thread, "resume": resume})

    def send(self, lease_id: str, text: str, client_msg_id: str, attachments: Sequence[Attachment] = ()) -> None:
        files = [{"name": a.name, "media_type": a.media_type, "data": base64.b64encode(a.data).decode()} for a in attachments]
        self._call("POST", f"/v1/leases/{lease_id}/messages",
                   {"text": text, "client_msg_id": client_msg_id, **({"attachments": files} if files else {})})

    def events(self, lease_id: str, after_seq: int) -> Iterator[dict[str, Any]]:
        headers = {**self._headers(), "Accept": "text/event-stream"}
        if after_seq:
            headers["Last-Event-ID"] = str(after_seq)
        try:
            with httpx.stream("GET", f"{self._url}/v1/leases/{lease_id}/events?v={V}", headers=headers,
                              timeout=httpx.Timeout(self._timeout, read=None)) as r:
                if r.status_code >= 400:
                    r.read()
                    try:
                        data = r.json()
                    except ValueError:
                        data = {}
                    raise runner_error(data, r.status_code)
                yield from parse_sse(r.iter_lines())
        except httpx.HTTPError as e:
            raise RunnerError("unreachable", str(e)) from e

    def file(self, lease_id: str, file_id: str) -> tuple[bytes, str]:
        try:
            r = httpx.get(f"{self._url}/v1/leases/{lease_id}/files/{file_id}", headers=self._headers(), timeout=self._timeout)
        except httpx.HTTPError as e:
            raise RunnerError("unreachable", str(e)) from e
        if r.status_code >= 400:
            try:
                data = r.json()
            except ValueError:
                data = {}
            raise runner_error(data, r.status_code)
        return r.content, r.headers.get("content-type", "application/octet-stream")

    def keepalive(self, lease_id: str) -> None:
        self._call("POST", f"/v1/leases/{lease_id}/keepalive", {})

    def terminal(self, lease_id: str) -> dict[str, str]:
        got = self._call("POST", f"/v1/leases/{lease_id}/terminal", {})
        return {"command": str(got["command"]), "hint": str(got.get("hint") or "")}

    def detach(self, lease_id: str, reason: str) -> None:
        self._call("POST", f"/v1/leases/{lease_id}/detach", {"reason": reason})

    def set_mode(self, lease_id: str, mode: str) -> dict[str, Any]:
        return self._call("POST", f"/v1/leases/{lease_id}/mode", {"mode": mode})

    def policy(self) -> dict[str, Any]:
        return self._call("GET", f"/v1/policy?v={V}")


PURPOSE_MAX = 500  # the contract's limit; cut the same way every time, so a retry matches
UPGRADE_TIMEOUT_S = 180.0  # renaming, creating the repository and pushing take a while


class HttpSandboxes:
    """Sandboxes opened by the session runner (POST /v1/sandboxes); once open, a sandbox is in the
    runner's policy like any project and is lent the same way."""

    def __init__(self, url: str, token_file: str | Path, http: httpx.Client):
        self._url = url.rstrip("/")
        self._token_file = Path(token_file).expanduser()
        self._http = http

    def create(self, name: str, purpose: str) -> str | None:
        try:
            token = self._token_file.read_text().strip()
        except OSError as e:
            raise SandboxRefused("failed", detail=f"runner token unreadable: {e}") from e
        try:
            r = self._http.post(f"{self._url}/v1/sandboxes", headers={"Authorization": f"Bearer {token}"},
                                json={"v": V, "name": name, "purpose": purpose.strip()[:PURPOSE_MAX]})
        except httpx.HTTPError as e:
            raise SandboxRefused("unreachable", detail=str(e)) from e
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {}
        if r.status_code in (200, 201) and data.get("name"):
            return str(data["name"])
        error = str(data.get("error") or f"HTTP {r.status_code}")
        detail = str(data.get("detail") or "")
        if error == "exists":
            raise SandboxRefused("taken", detail=detail)
        if error == "bad_name":
            raise SandboxRefused("bad_name", detail=detail)
        if error == "too_many":
            raise SandboxRefused("too_many", limit=data.get("limit"), current=data.get("current"), detail=detail)
        raise SandboxRefused("failed", detail=f"{error}: {detail}" if detail else error)

    def upgrade(self, name: str, rename: str | None) -> str | None:
        """POST /v1/sandboxes/<name>/upgrade: makes a GitHub repository and pushes, so it may take a minute."""
        try:
            token = self._token_file.read_text().strip()
        except OSError as e:
            raise SandboxRefused("failed", detail=f"runner token unreadable: {e}") from e
        try:
            r = self._http.post(f"{self._url}/v1/sandboxes/{name}/upgrade", headers={"Authorization": f"Bearer {token}"},
                                json={"v": V, **({"rename": rename} if rename else {})}, timeout=UPGRADE_TIMEOUT_S)
        except httpx.HTTPError as e:
            raise SandboxRefused("unreachable", detail=str(e)) from e
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {}
        if r.status_code == 200 and data.get("name"):
            return str(data["name"])
        error = str(data.get("error") or f"HTTP {r.status_code}")
        detail = str(data.get("detail") or "")
        kind = {"name_taken": "taken", "dirty": "dirty", "busy": "busy"}.get(error, "failed")
        raise SandboxRefused(kind, detail=f"{error}: {detail}" if kind == "failed" and detail else detail or error)

    def rename(self, name: str, to: str) -> str | None:
        """POST /v1/projects/<name>/rename: moves its folder, memory and repository (GitHub too), so it may take a
        minute. None from a runner that does not rename yet (no such endpoint)."""
        try:
            token = self._token_file.read_text().strip()
        except OSError as e:
            raise SandboxRefused("failed", detail=f"runner token unreadable: {e}") from e
        try:
            r = self._http.post(f"{self._url}/v1/projects/{name}/rename", headers={"Authorization": f"Bearer {token}"},
                                json={"v": V, "to": to}, timeout=UPGRADE_TIMEOUT_S)
        except httpx.HTTPError as e:
            raise SandboxRefused("unreachable", detail=str(e)) from e
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {}
        if r.status_code == 200 and data.get("name"):
            return str(data["name"])
        error = str(data.get("error") or "")
        if r.status_code == 404 and error != "not_found":
            return None
        detail = str(data.get("detail") or "")
        kind = {"name_taken": "taken", "bad_name": "bad_name", "busy": "busy"}.get(error, "failed")
        error = error or f"HTTP {r.status_code}"
        raise SandboxRefused(kind, detail=f"{error}: {detail}" if kind == "failed" and detail else detail or error)


def parse_sse(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Server-sent events → the JSON in each event's data (comments and keep-alives skipped)."""
    data: list[str] = []
    for line in lines:
        if line == "":
            if data:
                try:
                    yield json.loads("\n".join(data))
                except ValueError:
                    pass
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        try:
            yield json.loads("\n".join(data))
        except ValueError:
            pass
