"""Whether a model's JSON reply keeps to the shape its decision point declares (contracts/llm-points.yaml →
contracts/llm/<point>.schema.json): what is wrong with it, said so the model can be asked once more."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from ruamel.yaml import YAML

from muninn.core.reply_json import read_object


def schemas(contracts: Path) -> dict[str, Any]:
    """Each decision point's output schema, by point."""
    points = (YAML(typ="safe").load((contracts / "llm-points.yaml").read_text(encoding="utf-8")) or {}).get("points") or {}
    return {name: json.loads((contracts / p["output"]["schema"]).read_text(encoding="utf-8"))
            for name, p in points.items() if (p.get("output") or {}).get("schema")}


class ReplyCheck:
    def __init__(self, by_point: Mapping[str, Any]):
        self._validators = {p: Draft202012Validator(s) for p, s in by_point.items()}

    def __call__(self, task: str, reply: str) -> list[str]:
        """What is wrong with the reply for this task ([] when nothing is, or the task declares no shape)."""
        v = self._validators.get(task)
        if v is None:
            return []
        try:
            data = read_object(reply)
        except ValueError as e:
            return [str(e)]
        return [f"{'/'.join(map(str, e.absolute_path)) or '(top)'}: {e.message}" for e in
                sorted(v.iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))][:3]
