"""OAuth 2.0 (authorization code, web-server flow) for one user, the owner.

Each provider is a `Provider` config; one `OAuth2Client` per connected
account. The consent page redirects to the PWA's /oauth/<provider>/callback,
checked with a single-use `state` rather than a device cookie: the browser
that opens the consent link (Safari) does not share cookies with the
home-screen app.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

import httpx

STATE_TTL = 15 * 60


class OAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class Provider:
    name: str
    auth_url: str
    token_url: str
    scopes: tuple[str, ...]
    auth_params: dict[str, str] = field(default_factory=dict)
    scope_in_token_requests: bool = False  # Microsoft wants it; Google does not


def _write_private(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read_client(path: Path) -> dict:
    """{client_id, client_secret}, from a plain file or a Google-style download."""
    raw = json.loads(Path(path).read_text())
    c = raw.get("web") or raw.get("installed") or raw
    if not c.get("client_id") or not c.get("client_secret"):
        raise OAuthError(f"no client_id/client_secret in {Path(path).name}")
    return c


class OAuth2Client:
    def __init__(self, provider: Provider, client_file: Path, token_file: Path, state_file: Path,
                 redirect_uri: str | None, http: httpx.Client | None = None):
        self.provider = provider
        self.name = provider.name
        self._client_file = Path(client_file)
        self._token_file = Path(token_file)
        self._state_file = Path(state_file)
        self._redirect = redirect_uri
        self._http = http or httpx.Client(timeout=20)
        self._expired_file = self._token_file.with_name(self._token_file.stem + ".expired")
        # once linked, a token that is gone (deleted, lost in a move) is a connection to make again, not one never made
        self._linked_file = self._token_file.with_name(self._token_file.stem + ".linked")

    @property
    def configured(self) -> bool:
        return self._client_file.is_file() and bool(self._redirect)

    @property
    def authorized(self) -> bool:
        return self._token_file.is_file()

    def status(self) -> str:
        """ok | relink (authorization expired, revoked, or its token gone) | off (never set up)."""
        if self.authorized:
            if not self._linked_file.exists():  # linked before this was kept
                self._linked_file.write_text(str(int(time.time())))
            return "ok"
        if self.configured and (self._expired_file.exists() or self._linked_file.exists()):
            return "relink"
        return "off"

    def _client(self) -> dict:
        if not self.configured:
            raise OAuthError(f"{self.name}: client file or public_url missing")
        return read_client(self._client_file)

    def _scope(self) -> dict[str, str]:
        return {"scope": " ".join(self.provider.scopes)} if self.provider.scope_in_token_requests else {}

    def authorization_url(self) -> str:
        state = secrets.token_urlsafe(24)
        _write_private(self._state_file, {"state": state, "exp": time.time() + STATE_TTL})
        q = {
            "client_id": self._client()["client_id"],
            "redirect_uri": self._redirect,
            "response_type": "code",
            "scope": " ".join(self.provider.scopes),
            "state": state,
            **self.provider.auth_params,
        }
        return f"{self.provider.auth_url}?{urlencode(q)}"

    def complete(self, code: str, state: str) -> None:
        try:
            saved = json.loads(self._state_file.read_text())
        except (OSError, ValueError):
            raise OAuthError("no authorization in progress") from None
        if not secrets.compare_digest(saved.get("state", ""), state) or saved.get("exp", 0) < time.time():
            raise OAuthError("state mismatch or expired; start the connection again")
        self._state_file.unlink(missing_ok=True)
        c = self._client()
        r = self._http.post(self.provider.token_url, data={
            "code": code, "client_id": c["client_id"], "client_secret": c["client_secret"],
            "redirect_uri": self._redirect, "grant_type": "authorization_code", **self._scope(),
        })
        if r.status_code != 200:
            raise OAuthError(f"token exchange failed: {r.text[:300]}")
        tok = r.json()
        if "refresh_token" not in tok:
            raise OAuthError("no refresh token returned")
        tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        _write_private(self._token_file, tok)
        self._expired_file.unlink(missing_ok=True)
        self._linked_file.write_text(str(int(time.time())))

    def access_token(self) -> str:
        if not self.authorized:
            raise OAuthError(f"{self.name}: not authorized yet")
        tok = json.loads(self._token_file.read_text())
        if tok.get("expires_at", 0) > time.time():
            return tok["access_token"]
        c = self._client()
        r = self._http.post(self.provider.token_url, data={
            "client_id": c["client_id"], "client_secret": c["client_secret"],
            "refresh_token": tok["refresh_token"], "grant_type": "refresh_token", **self._scope(),
        })
        if r.status_code != 200:
            if r.status_code in (400, 401) and "invalid_grant" in r.text:
                # revoked or expired: the user has to connect again
                self._token_file.unlink(missing_ok=True)
                self._expired_file.write_text(str(int(time.time())))
            raise OAuthError(f"{self.name}: token refresh failed: {r.text[:200]}")
        new = r.json()
        tok.update(access_token=new["access_token"], expires_at=time.time() + int(new.get("expires_in", 3600)) - 60)
        if new.get("refresh_token"):  # some providers rotate it
            tok["refresh_token"] = new["refresh_token"]
        _write_private(self._token_file, tok)
        return tok["access_token"]

    def get(self, url: str, headers: dict | None = None, **params) -> dict:
        r = self._http.get(url, params=params, headers={"Authorization": f"Bearer {self.access_token()}", **(headers or {})})
        r.raise_for_status()
        return r.json()

    def send(self, method: str, url: str, body: dict | None = None) -> dict:
        r = self._http.request(method, url, json=body, headers={"Authorization": f"Bearer {self.access_token()}"})
        r.raise_for_status()
        return r.json() if r.content else {}
