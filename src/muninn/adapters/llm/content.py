"""What a user message carries to a Claude model when the user sent files: shared by the CLI and the API."""

from __future__ import annotations

import base64
import re
from typing import Any, Sequence

from muninn.core.language import Language
from muninn.core.models import Attachment


def content_blocks(prompt: str, attachments: Sequence[Attachment]) -> list[dict[str, Any]]:
    """Each photo as an image block, each PDF as a document block, each text file inlined, then the prompt."""
    blocks: list[dict[str, Any]] = []
    for a in attachments:
        data = base64.b64encode(a.data).decode()
        if a.is_image:
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": a.media_type, "data": data}})
        elif a.media_type == "application/pdf":
            blocks.append({"type": "document", "source": {"type": "base64", "media_type": a.media_type, "data": data}, "title": a.name})
        else:
            blocks.append({"type": "text", "text": f"--- {a.name} ---\n{a.data.decode('utf-8', 'replace')}"})
    blocks.append({"type": "text", "text": prompt})
    return blocks


THINK = re.compile(r"<think>.*?</think>\s*", re.S)  # what a reasoning model writes before its answer


def without_thinking(reply: str) -> str:
    return THINK.sub("", reply).strip()


def estimated_tokens(text: str) -> int:
    """A generous guess at how many tokens text takes: about one per CJK character, one per four others."""
    wide = sum(1 for ch in text if ord(ch) >= 0x2E80)
    return wide + (len(text) - wide) // 4 + 1


def check_length(system: str, prompt: str, context: int | None, model: str, words: Language) -> None:
    """Raises before sending when the request would not fit the model's declared context (a model given more is
    cut from the front without a word); nothing to check when the context is not declared."""
    if context and (need := estimated_tokens(system) + estimated_tokens(prompt)) > context * 0.9:
        raise ValueError(words("llm.too_long", need=need, model=model, context=context))
