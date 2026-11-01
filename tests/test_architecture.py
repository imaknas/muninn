"""The layering the README promises, checked on the source itself:

- core depends only on core (its ports), never on adapters or the
  composition root;
- the model backends are reached only through the router: nothing but the
  LLM adapters themselves and main.py (where they are chosen) imports them;
- core starts no processes and opens no connections; that is adapter work;
- every model request names its judgment point (`task`), so each call can
  be found, measured and given a safe default.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "muninn"
PROCESS_AND_NETWORK = ("subprocess", "urllib.request", "http.client", "http.server", "httpx", "requests", "socket")
# urllib.parse only reads strings, so core may use it


def modules():
    for path in sorted(SRC.rglob("*.py")):
        yield path.relative_to(SRC).as_posix(), ast.parse(path.read_text())


def imports(tree: ast.AST, here: str) -> set[str]:
    """Absolute module names a file imports (relative imports resolved)."""
    package = ["muninn", *here.split("/")[:-1]]
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            mod = ".".join([*base, node.module] if node.module else base)
            out.add(mod)
            out |= {f"{mod}.{a.name}" for a in node.names}
    return out


def test_core_depends_only_on_core():
    bad = [(name, m) for name, tree in modules() if name.startswith("core/")
           for m in imports(tree, name) if m.startswith(("muninn.adapters", "muninn.main", "muninn.config"))]
    assert bad == []


def test_model_backends_are_reached_only_through_the_router():
    bad = [(name, m) for name, tree in modules()
           if not (name.startswith("adapters/llm/") or name == "main.py")
           for m in imports(tree, name) if m.startswith("muninn.adapters.llm")]
    assert bad == []


def test_core_starts_no_processes_and_opens_no_connections():
    bad = [(name, m) for name, tree in modules() if name.startswith("core/")
           for m in imports(tree, name) if any(m == p or m.startswith(p + ".") for p in PROCESS_AND_NETWORK)]
    assert bad == []


def test_every_model_request_names_its_judgment_point():
    untagged = []
    for name, tree in modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "LLMRequest":
                if not (any(k.arg == "task" for k in node.keywords) or len(node.args) >= 6):
                    untagged.append(f"{name}:{node.lineno}")
    assert untagged == [], "give each LLMRequest a task= naming what it is for"


# who may take the whole store: everything else names the small ports it uses (core/ports.py)
WHOLE_STORE = {"main.py", "core/engine.py", "core/assembly.py", "core/ports.py", "adapters/store_sqlite.py"}


def test_only_the_composition_root_and_the_engine_take_the_whole_store():
    named = set()
    for here, tree in modules():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Name) and node.id == "Store") or (
                    isinstance(node, ast.ImportFrom) and any(a.name == "Store" for a in node.names)):
                named.add(here)
    assert named <= WHOLE_STORE, f"name the small ports instead of Store in: {sorted(named - WHOLE_STORE)}"


# who may hold the engine: the front object that builds it (core/assembly.py), and the web adapter
# (which only names its type). No other module does; new code takes the services and ports it needs
# (core/places.py's Whereabouts, a tool in core/tools/, …), and this list may only shrink.
LEANS_ON_ENGINE = {"core/assembly.py"}


def test_no_new_module_leans_on_the_engine():
    leaning = {here for here, tree in modules()
               if here != "core/engine.py" and any(m == "muninn.core.engine" or m.startswith("muninn.core.engine.")
                                                   for m in imports(tree, here))}
    assert leaning <= LEANS_ON_ENGINE, f"take services and ports, not the engine, in: {sorted(leaning - LEANS_ON_ENGINE)}"


# the actors (core/actors.py), each the modules it is made of; handoffs (the outbox to the projects),
# the action gate, ports, models and tools are shared and not actors
ACTORS = {
    "tasks": {"core/tasks.py"}, "plan review": {"core/review.py"},
    "questions": {"core/questions.py", "core/project_messages.py"}, "mail": {"core/mail.py"},
    "places": {"core/places.py"}, "packets": {"core/prepare.py"}, "windows": {"core/window_classify.py"},
    "reflection": {"core/reflect.py"}, "leases": {"core/lease.py"}, "offline time": {"core/offline_time.py"},
    "conversation": {"core/conversation.py", "core/commands.py"},
    "records": {"core/records.py"},
    "plans": {"core/plans.py"},
    "alerts": {"core/alerts.py"}, "onboarding": {"core/onboarding.py"},
    "ideas": {"core/ideas.py"},
}
# imports from one actor into another that remain; this list may only shrink
CROSSING = {("questions", "tasks")}  # the projects' inbox hands task messages to the task registry


def test_actors_do_not_reach_into_each_other():
    owner = {path: actor for actor, paths in ACTORS.items() for path in paths}
    crossing = set()
    for here, tree in modules():
        if here not in owner:
            continue
        for m in imports(tree, here):
            there = owner.get(m.removeprefix("muninn.").replace(".", "/") + ".py")
            if there and there != owner[here]:
                crossing.add((owner[here], there))
    assert crossing <= CROSSING, f"pass what an actor needs from another in; don't import it: {sorted(crossing - CROSSING)}"


def test_the_engine_knows_no_actor():
    """The engine observes, notifies and runs the jobs it is given; the front object wires the actors to it."""
    owner = {path for paths in ACTORS.values() for path in paths}
    tree = dict(modules())["core/engine.py"]
    reached = {m.removeprefix("muninn.").replace(".", "/") + ".py" for m in imports(tree, "core/engine.py")} & owner
    assert not reached, f"the engine imports actors: {sorted(reached)}"
