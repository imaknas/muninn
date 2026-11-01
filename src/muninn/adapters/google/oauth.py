"""Google as an OAuth provider: Gmail (read + drafts) and Calendar."""

from __future__ import annotations

from pathlib import Path

import httpx

from muninn.adapters.oauth import OAuth2Client, OAuthError, Provider

GOOGLE = Provider(
    name="google",
    auth_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    scopes=(
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",  # drafts only; Muninn never sends
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    ),
    auth_params={"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"},
)

GoogleAuthError = OAuthError


def GoogleOAuth(client_file: Path, token_file: Path, state_file: Path, redirect_uri: str | None,
                http: httpx.Client | None = None) -> OAuth2Client:
    return OAuth2Client(GOOGLE, client_file, token_file, state_file, redirect_uri, http)
