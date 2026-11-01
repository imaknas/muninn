"""A new user's first conversation as a topic: what they care about becomes their first domains.

The start card puts this topic on the main input; the model then drafts a domain per thing the
user names (propose_domain, several in one round), asks about what was not said instead of
making it up, and each drafted goal and deadline waits for the user in the plan review."""

from __future__ import annotations

from typing import Any, Callable, Sequence
from .language import Language


class Onboarding:
    kinds = ("onboarding",)

    def __init__(self, domains: Callable[[], Sequence[tuple[str, str]]], *, words: Language):
        """`domains`: (id, title) of the domains there are now."""
        self.words = words
        self._domains = domains

    def topic(self, kind: str, ref: str) -> dict[str, Any] | None:
        return {
            "kind": "onboarding", "ref": "first", "domain": None, "title": self.words("onboarding.title"),  # its name on the screen, and in the prompt
            "domains_now": [title for _, title in self._domains()],
            "note": "The user has just started using Muninn and has no domains yet (or only a few). A domain is something the user cares about and "
                    "wants to see progress on, e.g. a job search, a thesis, health. From what the user says, draft a domain for each thing the user "
                    "mentions with propose_domain (one round may hold several): id in lowercase English with hyphens, title in the user's own "
                    "words; fill in hours_per_week only if the user said how much time a week the user wants to spend, and deadlines only if the user gave "
                    "a deadline; do not make up what the user did not say: ask about it in one sentence in reply (e.g. \"Roughly how many hours "
                    "a week do you want to spend on it?\"). Do not open a domain that already exists (domains_now) again. "
                    f"Once they are made, tell the user: each item waits in \"{self.words('onboarding.plan_review')}\" for the user's yes; if "
                    f"calendar, mail and location are not connected yet, connect them in \"{self.words('onboarding.settings')}\", "
                    "so Muninn can measure where the time goes.",
        }
