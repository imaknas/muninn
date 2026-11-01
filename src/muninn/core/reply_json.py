"""The JSON object a model's reply holds: read the same way wherever a reply is read, by the caller that acts on it
and by the check that holds it to its decision point's shape."""

from __future__ import annotations

import json
import re
from typing import Any

THINK = re.compile(r"<think>.*?</think>", re.S)  # what a reasoning model writes before its answer


def read_object(reply: str) -> dict[str, Any]:
    """The object, from the first "{" to the last "}" (prose around it and a <think> block left out); ValueError
    saying what is wrong when there is none or it is not valid JSON."""
    text = THINK.sub("", reply)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the reply")
    try:
        return json.loads(text[start:end + 1])  # what runs from "{" to "}" and reads at all is an object
    except ValueError as e:
        raise ValueError(f"not valid JSON: {e}") from None


def json_object(reply: str | None) -> dict[str, Any] | None:
    """The object, or None (no reply, or none to read in it): each caller then does what its point does on failure."""
    if not reply:
        return None
    try:
        return read_object(reply)
    except ValueError:
        return None
