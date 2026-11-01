"""The registry of the model's decision points (src/muninn/contracts/llm-points.yaml) and the code agree:
every point the code asks a model about is declared, every declared point is used, and each one
says what it answers with, what happens when the model fails, and how it is scored."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from muninn.core.evals import SCORERS
from muninn.core.task_models import GROUPS
from muninn.package import CONTRACTS

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "muninn"
REGISTRY = YAML(typ="safe").load((CONTRACTS / "llm-points.yaml").read_text(encoding="utf-8"))
POINTS: dict = REGISTRY["points"]
ON_FAILURE = {"skip", "retry_later", "ask_user", "say_so", "default"}
MATERIAL = {"cloud_safe", "personal", "sensitive", "local_only"}


def _const(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def tasks_in_code() -> set[str]:
    """The task names the code gives its model requests: `task=` on a request (`point=` on a decision; or the sixth
    positional), the name passed to a helper that builds one (`complete("route", …)`, `_ask(…, "reflect_week")`),
    and a helper's default (`task: str = "answer"`)."""
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                for kw in node.keywords:
                    if kw.arg in ("task", "point") and (v := _const(kw.value)):
                        found.add(v)
                if name == "LLMRequest" and len(node.args) >= 6 and (v := _const(node.args[5])):
                    found.add(v)
                if name == "complete" and node.args and (v := _const(node.args[0])):
                    found.add(v)
                if name == "_ask" and len(node.args) == 4 and (v := _const(node.args[3])):
                    found.add(v)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args.args + node.args.kwonlyargs
                defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + node.args.defaults + node.args.kw_defaults
                for a, d in zip(args, defaults):
                    if a.arg == "task" and (v := _const(d)):
                        found.add(v)
    return found


def test_the_registry_keeps_to_the_shared_format():
    import jsonschema

    shared = json.loads((CONTRACTS / "vendor" / "records" / "llm-points.v1.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(REGISTRY, shared)


def test_every_point_the_code_asks_about_is_declared_and_every_declared_one_is_used():
    code = tasks_in_code()
    assert code - set(POINTS) == set(), "asked of a model but not in src/muninn/contracts/llm-points.yaml"
    assert set(POINTS) - code == set(), "declared but no longer asked"


@pytest.mark.parametrize("name", sorted(POINTS))
def test_each_point_says_what_it_needs_to(name):
    p = POINTS[name]
    for key in ("caller", "purpose", "kind", "shape", "model_group", "material", "output", "on_failure", "eval"):
        assert p.get(key), f"{name}: {key} missing"
    assert (SRC / p["caller"]).is_file(), f"{name}: no such caller {p['caller']}"
    assert p["kind"] in ("decide", "generate")
    assert p["shape"] == "completion", f"{name}: every point is one model call with no tools of its own"
    assert p["kind"] != "decide" or p.get("labels"), f"{name}: a decide point lists its answers"
    assert p["material"] in MATERIAL
    assert p["on_failure"] in ON_FAILURE, f"{name}: on_failure is one of {sorted(ON_FAILURE)}, never a silent stand-in"
    assert (p["on_failure"] == "default") == bool(p.get("default")), f"{name}: a default says what it is, and only then"
    assert p["eval"].get("scorer") and p["eval"].get("truth"), f"{name}: how it is scored and against what"
    out = p["output"]
    assert out["format"] in ("json", "text")
    if out["format"] == "json":
        schema = CONTRACTS / out["schema"]  # relative to the registry's own directory
        assert schema.is_file(), f"{name}: no schema {out['schema']}"
        assert json.loads(schema.read_text(encoding="utf-8"))["type"] == "object"


def test_model_groups_and_scorers_name_declared_points():
    groups = {g.key for g in GROUPS}
    for name, p in POINTS.items():
        assert p["model_group"] in groups | {"default"}, f"{name}: unknown model group {p['model_group']}"
    in_groups = {t for g in GROUPS for t in g.tasks}
    assert in_groups <= set(POINTS), "a model group names a point the registry does not have"
    for name, p in POINTS.items():
        grouped = next((g.key for g in GROUPS if name in g.tasks), "default")
        assert p["model_group"] == grouped, f"{name}: the registry says {p['model_group']}, task_models says {grouped}"
    assert set(SCORERS) <= set(POINTS)
    for name, p in POINTS.items():
        want = "none" if name == "judge" else name if name in SCORERS else "judge"  # the judge itself is not scored
        assert p["eval"]["scorer"] == want, f"{name}: scorer {p['eval']['scorer']} disagrees with evals.SCORERS ({want})"


def test_coverage_counts_each_declared_point_even_one_never_asked():
    from muninn.core.evals import coverage

    cases = [{"task": "packet_fit", "feedback": [{"kind": "answer"}], "by_local": False},
             {"task": "packet_fit", "feedback": [], "by_local": True}]
    rows = {r["point"]: r for r in coverage(POINTS, cases)}
    assert set(rows) == set(POINTS)
    assert (rows["packet_fit"]["cases"], rows["packet_fit"]["with_feedback"], rows["packet_fit"]["by_local"]) == (2, 1, 1)
    assert rows["evening"]["cases"] == 0


def test_the_fit_checks_labels_are_the_registrys():
    from muninn.core.prepare import VERDICTS

    assert list(VERDICTS) == POINTS["packet_fit"]["labels"]


def _enums(schema, path: str = "") -> dict[str, list]:
    out = {path: schema["enum"]} if isinstance(schema, dict) and "enum" in schema else {}
    if isinstance(schema, dict):
        for k, v in schema.items():
            out |= _enums(v, f"{path}/{k}")
    return out


def _schema(name: str) -> dict:
    return json.loads((CONTRACTS / POINTS[name]["output"]["schema"]).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(n for n, p in POINTS.items()
                                        if p.get("labels") and (p.get("output") or {}).get("schema") and _enums(_schema(n))))
def test_a_decide_points_schema_allows_exactly_its_labels(name):
    """Where the schema lists the answers, they are the registry's labels: a reply the code would take is never held to
    be out of shape (and asked for again, which steers the model to another answer), nor one it would drop held to be
    in shape. (Labels like "<a known domain id>" are open: their schema says only the type.)"""
    assert POINTS[name]["labels"] in [[x for x in e if x is not None] for e in _enums(_schema(name)).values()], name


def test_the_mail_categories_and_idea_steps_are_the_registrys():
    from muninn.core.idea_next import LABELS
    from muninn.core.mail import CATEGORIES

    assert list(CATEGORIES) == POINTS["mail_triage"]["labels"]
    assert list(LABELS) == POINTS["idea_next"]["labels"]


def test_the_mail_schema_names_every_field_the_prompt_asks_for():
    from muninn.core.mail import MAIL_SYSTEM

    template = MAIL_SYSTEM[MAIL_SYSTEM.index("{\"category\""):MAIL_SYSTEM.index("}\nCategories")]
    asked = set(re.findall(r'"(\w+)":', template))
    schema = _schema("mail_triage")
    assert asked <= set(schema["properties"]), asked - set(schema["properties"])
