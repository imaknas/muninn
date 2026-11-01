"""Credentials pasted into what a cloud model would read are masked before it goes: an API key, a token, a private
key header. Only shapes a credential has and ordinary text does not (a known prefix and a long random tail), so mail,
order numbers and one-time codes reach the model as they are."""

from __future__ import annotations

import re

PATTERNS = {
    "Anthropic API key": r"\bsk-ant-[A-Za-z0-9_-]{20,}",
    "OpenAI API key": r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}",
    "GitHub token": r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}",
    "Slack token": r"\bxox[abposr]-[A-Za-z0-9-]{10,}",
    "AWS access key": r"\bAKIA[0-9A-Z]{16}\b",
    "Google API key": r"\bAIza[0-9A-Za-z_-]{35}\b",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
    "Stripe live key": r"\b[rs]k_live_[A-Za-z0-9]{20,}",
}
_ANY = re.compile("|".join(f"(?P<p{i}>{p})" for i, p in enumerate(PATTERNS.values())))
_NAMES = list(PATTERNS)


def redact(text: str) -> tuple[str, int]:
    """The text with each credential replaced by what it was ("[masked GitHub token]"), and how many."""
    count = 0

    def mask(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"[masked {_NAMES[int(m.lastgroup[1:])]}]"

    return _ANY.sub(mask, text), count
