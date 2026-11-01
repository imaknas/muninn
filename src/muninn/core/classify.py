"""Domain suggestions for unattributed activity (sensitive: the local model, unless routing sends everything to the cloud)."""

from __future__ import annotations

import json
from typing import Any, Sequence

from .llm_router import LLMRequest, LLMRouter
from .models import Sensitivity
from .reply_json import json_object

SYSTEM = (
    "You sort a person's computer activity into life domains. "
    "Reply with JSON only: {\"domain\": \"<one of the ids>\"} or {\"domain\": null} if unsure."
)


class LocalDomainClassifier:
    def __init__(self, router: LLMRouter):
        self._router = router

    def classify_domain(self, description: dict[str, Any], domains: Sequence[tuple[str, str]]) -> str | None:
        prompt = json.dumps(
            {"domains": [{"id": d, "title": t} for d, t in domains], "activity": description},
            ensure_ascii=False,
        )
        # Unattributed content: declared sensitive with no domain, so local only.
        reply = self._router.complete(
            LLMRequest(system=SYSTEM, content=prompt, sensitivity=Sensitivity.SENSITIVE, domain=None, json_mode=True,
                       task="domain_classify")
        )
        domain = (json_object(reply) or {}).get("domain")
        return domain if domain in {d for d, _ in domains} else None
