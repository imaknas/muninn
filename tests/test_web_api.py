"""The web API keeps to its contract (src/muninn/contracts/web-api.v1.schema.json, generated from the pages'
own declarations in types.ts): every route the app serves is in it, or listed below as one that
machines call, and every route answers the shape it declares, for a newcomer with nothing yet and
for a world with something on every page. The pages can be redesigned against this contract alone."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Callable

import pytest
from starlette.testclient import TestClient
from muninn.package import CONTRACTS
from jsonschema import Draft202012Validator
from starlette.routing import Mount, Route

from world import World

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((CONTRACTS / "web-api.v1.schema.json").read_text(encoding="utf-8"))

# routes that machines call, not the pages, and where their shape is kept instead
MACHINES = {
    "GET /healthz": "the deploy's health check",
    "GET /api/smoke": "bin/deploy.sh (bin/smoke.py)",
    "POST /api/ingest": "the collectors' observations",
    "POST /api/alerts": "src/muninn/contracts/platform-alerts.v1.md",
    "POST /api/ingest/owntracks": "OwnTracks on a phone set up before apps were named in the address",
    "POST /api/ingest/location/{app}": "a tracking app on the phone (core/location.py, one implementation per app)",
    "POST /api/ingest/health": "an iOS Shortcut (steps, sleep)",
    "POST /api/ingest/focus": "an iOS Shortcut (Focus on and off)",
    "POST /api/messages": "src/muninn/contracts/messages.v1.schema.json",
    "POST /api/records": "Principal Handoff records from the projects (src/muninn/contracts/vendor/principal-handoff)",
    "GET /api/records": "Principal Handoff records for a project, read from a cursor",
    "POST /api/records/ack": "a project acknowledging records up to a cursor",
    "POST /api/permission-requests": "src/muninn/contracts/permission-requests.v2.schema.json",
    "GET /api/permission-requests/{rid}": "src/muninn/contracts/permission-requests.v2.schema.json",
    "GET /api/leases/{lease_id}/events": "server-sent events, read by talk.ts as they come",
    "GET /api/changes/events": "server-sent events, read by app.ts while the app is in view",
    "GET /api/documents/{name}": "a file, opened as a link",
    "GET /api/uploads/{uid}": "a file the user sent, shown again as an image or a link",
    "GET /api/leases/{lease_id}/files/{file_id}": "a file a deep talk's session showed, shown as an image or a link",
}


def served(app) -> set[str]:
    out = set()
    for r in app.routes:
        if isinstance(r, Route) and (r.path.startswith("/api/") or r.path == "/healthz"):
            path = re.sub(r"\{(\w+)(:\w+)?\}", r"{\1}", r.path)
            out |= {f"{m} {path}" for m in r.methods or () if m != "HEAD"}
    return out


def pattern(key: str) -> re.Pattern[str]:
    method, path = key.split(" ", 1)
    return re.compile(re.escape(method) + " " + re.sub(r"\\\{\w+\\\}", r"[^/]+", re.escape(path)) + "$")


def test_the_contract_is_what_types_ts_declares():
    spec = importlib.util.spec_from_file_location("web_api_schema", ROOT / "bin" / "web_api_schema.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert (CONTRACTS / "web-api.v1.schema.json").read_text(encoding="utf-8") == gen.render(), \
        "types.ts changed: run uv run python bin/web_api_schema.py"


def test_every_route_is_in_the_contract_or_called_by_machines(tmp_path):
    app = World(tmp_path).client.app
    routes = served(app)
    declared = set(CONTRACT["routes"]) | set(MACHINES)
    missing = {r for r in routes if not any(pattern(d).match(r) or pattern(r).match(d) for d in declared)}
    assert missing == set(), "served but in neither the contract nor MACHINES"
    stale = {d for d in declared if not any(pattern(r).match(d) for r in routes)}
    assert stale == set(), "declared but not served"


def validator(key: str) -> Draft202012Validator:
    return Draft202012Validator({**CONTRACT["routes"][key], "$defs": CONTRACT["$defs"]})


def conforms(key: str, answer: Any) -> None:
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '(top)'}: {e.message}" for e in validator(key).iter_errors(answer)]
    assert errors == [], f"{key} answered off contract:\n" + "\n".join(errors[:12])


# ── each route, called the way the pages call it ──────────────────────────────

def first(w: World, path: str, key: str = "items") -> dict[str, Any]:
    items = w.client.get(path).json()[key]
    assert items, f"the world has nothing at {path}"
    return items[0]


def ready(w: World, kind: str) -> dict[str, Any]:
    return next(r for r in w.client.get("/api/home").json()["ready"] if r["kind"] == kind)


def filled(w: World) -> int:
    """A packet whose application form is filled, on a site that takes it."""
    from muninn.core.apply import Field, Form, SiteResult, Sites

    class Site:
        name = "Greenhouse"

        def handles(self, url):
            return True

        def submit(self, form, values, files):
            return SiteResult(True, said="Thank you for applying")

    pid = ready(w, "application_packet")["id"]
    w.e.applications.sites = Sites([Site()])
    w.e.store.set_state(f"apply:{pid}", {"state": "filled", "site": "Greenhouse", "values": {"first_name": "Ada"}, "files": {},
                                         "form": Form("Greenhouse", "https://jobs.example/1", (Field("first_name", "First Name", "text", True),)).to_dict()})
    return pid


def leased(w: World) -> str:
    return w.client.get("/api/leases?active=1").json()["active"][0]["thread"]


Call = Callable[[World], tuple[str, Any]]
GETS: dict[str, Call] = {
    "GET /api/me": lambda w: ("/api/me", None),
    "GET /api/settings": lambda w: ("/api/settings", None),
    "GET /api/devices/https": lambda w: ("/api/devices/https", None),
    "GET /api/settings/sources": lambda w: ("/api/settings/sources", None),
    "GET /api/settings/google": lambda w: ("/api/settings/google", None),
    "GET /api/settings/form-filling": lambda w: ("/api/settings/form-filling", None),
    "GET /api/today": lambda w: ("/api/today", None),
    "GET /api/day": lambda w: ("/api/day", None),
    "GET /api/goal-days": lambda w: ("/api/goal-days", None),
    "GET /api/home": lambda w: ("/api/home", None),
    "GET /api/inbox": lambda w: ("/api/inbox", None),
    "GET /api/confirmations": lambda w: ("/api/confirmations", None),
    "GET /api/connections": lambda w: ("/api/connections", None),
    "GET /api/requests": lambda w: ("/api/requests", None),
    "GET /api/review": lambda w: ("/api/review", None),
    "GET /api/attribution": lambda w: ("/api/attribution", None),
    "GET /api/push/key": lambda w: ("/api/push/key", None),
    "GET /api/setup/health": lambda w: ("/api/setup/health", None),
    "GET /api/setup/location": lambda w: ("/api/setup/location", None),
    "GET /api/setup/activity": lambda w: ("/api/setup/activity", None),
    "GET /api/project-questions": lambda w: ("/api/project-questions", None),
    "GET /api/permission-requests/pending": lambda w: ("/api/permission-requests/pending", None),
    "GET /api/permissions/auto-commands": lambda w: ("/api/permissions/auto-commands", None),
    "GET /api/leases": lambda w: ("/api/leases", None),
    "GET /api/conversation": lambda w: ("/api/conversation", None),
    "GET /api/topics": lambda w: ("/api/topics", None),
    "GET /api/waiting": lambda w: ("/api/waiting", None),
    "GET /api/setup": lambda w: ("/api/setup", None),
}
# the ones that read one thing, only where there is one
FULL_GETS: dict[str, Call] = {
    "GET /api/items/{iid}": lambda w: (f"/api/items/{first(w, '/api/inbox')['id']}", None),
    "GET /api/prepared/{pid}": lambda w: (f"/api/prepared/{ready(w, 'application_packet')['id']}", None),
    "GET /api/mail/{account}/{mid}": lambda w: ("/api/mail/personal/m1", None),
}
LEASE_VIEWS = {  # the same route, asked three ways
    "active": lambda w: "/api/leases?active=1",
    "thread": lambda w: f"/api/leases?thread={leased(w)}",
}
POSTS: dict[str, Call] = {
    "POST /api/uploads": lambda w: ("/api/uploads?name=room.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16),  # the file itself
    "POST /api/pair": lambda w: ("/api/pair", {"code": w.devices.new_pairing_code(), "name": "another phone"}),
    "POST /api/setup/models": lambda w: ("/api/setup/models", {"impl": "ollama"}),
    "POST /api/setup/save": lambda w: ("/api/setup/save", {"impl": "ollama", "fast": "model-small"}),
    "POST /api/setup/pair": lambda w: ("/api/setup/pair", {}),
    "POST /api/setup/pair-here": lambda w: ("/api/setup/pair-here", {}),  # asked from this computer: LOCAL
    "POST /api/devices/pair": lambda w: ("/api/devices/pair", {}),
    "POST /api/devices/https": lambda w: ("/api/devices/https", {}),
    "POST /api/settings/language": lambda w: ("/api/settings/language", {"language": "en"}),
    "POST /api/settings/sources": lambda w: ("/api/settings/sources", {"workspace_roots": [], "transcript_roots": []}),
    "POST /api/settings/google": lambda w: ("/api/settings/google", {"gmail": True, "client": json.dumps({"web": {
        "client_id": "x.apps.googleusercontent.com", "client_secret": "s", "redirect_uris": [w.client.get("/api/settings/google").json()["callback"]]}})}),
    "POST /api/settings/form-filling": lambda w: ("/api/settings/form-filling", {}),
    "POST /api/inbox/read": lambda w: ("/api/inbox/read", {"ids": [first(w, "/api/inbox")["id"]]}),
    "POST /api/ideas/{n}/settle": lambda w: (f"/api/ideas/{w.client.get('/api/home').json()['ideas'][0]['n']}/settle", {"decision": "shelved"}),
    "POST /api/confirmations/{cid}": lambda w: ((c := first(w, "/api/confirmations")) and f"/api/confirmations/{c['id']}",
                                                {"answer": c["options"][0]}),
    "POST /api/connections/{provider}": lambda w: ("/api/connections/microsoft", {}),
    "POST /api/command": lambda w: ("/api/command", {"text": "今天先做投遞"}),
    "POST /api/command/act": lambda w: ("/api/command/act", {"thread": w.talk(), "index": 0}),
    "POST /api/tasks/{key}/close": lambda w: (f"/api/tasks/{w.client.get('/api/home').json()['tasks'][0]['key']}/close", {}),
    "POST /api/handoffs/{hid}/cancel": lambda w: (
        f"/api/handoffs/{w.client.post(f'/api/project-questions/{first(w, '/api/project-questions')['id']}/answer', json={'answer': '紅的'}).json()['handoff_id']}/cancel", {}),
    "POST /api/review": lambda w: ((it := first(w, "/api/review")) and "/api/review", {"id": it["id"], "decision": "confirm"}),
    "POST /api/review/domain": lambda w: ("/api/review/domain", {"domain": first(w, "/api/review")["domain"]}),
    "POST /api/review/why": lambda w: ("/api/review/why", {"id": first(w, "/api/review")["id"]}),
    "POST /api/prepared/{pid}/draft": lambda w: (f"/api/prepared/{ready(w, 'mail_reply')['id']}/draft", {"body": "Thanks! Tuesday at 10."}),
    "POST /api/prepared/{pid}/done": lambda w: (f"/api/prepared/{ready(w, 'application_packet')['id']}/done", {}),
    "POST /api/prepared/{pid}/send": lambda w: (f"/api/prepared/{filled(w)}/send", {}),
    "POST /api/prepared/{pid}/dismiss": lambda w: (f"/api/prepared/{ready(w, 'application_packet')['id']}/dismiss", {}),
    "POST /api/actions/{aid}/undo": lambda w: (f"/api/actions/{next(d for d in w.client.get('/api/home').json()['done'] if d['undoable'])['id']}/undo", {}),
    "POST /api/attribution": lambda w: ("/api/attribution", {"repo": "side-thing", "domain": "jobs"}),
    "POST /api/input/note": lambda w: ("/api/input/note", {"text": "履歷改好了", "domain": "jobs"}),
    "POST /api/input/hours": lambda w: ("/api/input/hours", {"goal": "jobs/golang", "hours": 1.5, "date": "2026-10-22", "note": ""}),
    "POST /api/tracker/status": lambda w: ("/api/tracker/status", {"domain": "jobs", "target": "acme", "status": "applied"}),
    "POST /api/tracker/undo/{change}": lambda w: (
        f"/api/tracker/undo/{w.client.post('/api/tracker/status', json={'domain': 'jobs', 'target': 'acme', 'status': 'applied'}).json()['change']}", {}),
    "POST /api/deadlines/done": lambda w: ("/api/deadlines/done", {"id": f"jobs/{w.e.plans.load()[0].deadlines[0].id}", "done": True}),
    "POST /api/schedule": lambda w: ("/api/schedule", {}),
    "POST /api/places": lambda w: ("/api/places", {"name": "公司", "lat": 25.04, "lon": 121.55}),
    "POST /api/push/subscribe": lambda w: ("/api/push/subscribe", {"subscription": {"endpoint": "https://push.example/1",
                                                                                  "keys": {"p256dh": "k", "auth": "a"}}}),
    "POST /api/push/test": lambda w: ("/api/push/test", {}),
    "POST /api/push/ack": lambda w: ("/api/push/ack", {"stage": "shown"}),
    "POST /api/opened": lambda w: ("/api/opened", {}),
    "POST /api/project-questions/{mid}/answer": lambda w: (f"/api/project-questions/{first(w, '/api/project-questions')['id']}/answer", {"answer": "紅的"}),
    "POST /api/project-questions/{mid}/dismiss": lambda w: (f"/api/project-questions/{first(w, '/api/project-questions')['id']}/dismiss", {}),
    "POST /api/leases/attach": lambda w: ("/api/leases/attach", {"project": "blog"}),
    "POST /api/leases/keepalive": lambda w: ("/api/leases/keepalive", {"thread": leased(w)}),
    "POST /api/leases/terminal": lambda w: ("/api/leases/terminal", {"thread": leased(w)}),
    "POST /api/leases/detach": lambda w: ("/api/leases/detach", {"thread": leased(w)}),
    "POST /api/leases/work": lambda w: (setattr(w.runner, "sandboxes", ["idea-1"]), "/api/leases/work",
                                        {"thread": w.client.post("/api/leases/attach", json={"project": "idea-1"}).json()["thread"]})[1:],
    "POST /api/leases/discuss": lambda w: (setattr(w.runner, "sandboxes", ["idea-1"]), "/api/leases/discuss",
                                           {"thread": w.client.post("/api/leases/attach", json={"project": "idea-1"}).json()["thread"]})[1:],
    "POST /api/permissions/auto-commands": lambda w: ("/api/permissions/auto-commands", {"on": False}),
    "POST /api/client-error": lambda w: ("/api/client-error", {"message": "x is undefined", "where": "#things"}),
    "POST /api/permission-requests/{rid}/decision": lambda w: (f"/api/permission-requests/{first(w, '/api/permission-requests/pending')['id']}/decision",
                                                               {"decision": "deny"}),
}


LOCAL = {"POST /api/setup/pair-here"}


def test_every_contract_route_has_a_call_here():
    assert set(CONTRACT["routes"]) == set(GETS) | set(FULL_GETS) | set(POSTS)


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    return World(tmp_path_factory.mktemp("full")).full()


@pytest.fixture(scope="module")
def newcomer(tmp_path_factory):
    return World(tmp_path_factory.mktemp("new"), newcomer=True)


@pytest.mark.parametrize("key", sorted(GETS))
def test_a_page_read_keeps_to_the_contract_for_a_newcomer(newcomer, key):
    path, _ = GETS[key](newcomer)
    r = newcomer.client.get(path)
    assert r.status_code == 200, r.text
    conforms(key, r.json())


@pytest.mark.parametrize("key", sorted(GETS | FULL_GETS))
def test_a_page_read_keeps_to_the_contract_with_something_everywhere(full, key):
    path, _ = (GETS | FULL_GETS)[key](full)
    r = full.client.get(path)
    assert r.status_code == 200, r.text
    conforms(key, r.json())


@pytest.mark.parametrize("how", sorted(LEASE_VIEWS))
def test_the_lease_view_keeps_to_the_contract_however_it_is_asked(full, how):
    r = full.client.get(LEASE_VIEWS[how](full))
    assert r.status_code == 200, r.text
    conforms("GET /api/leases", r.json())


@pytest.mark.parametrize("key", sorted(POSTS))
def test_a_change_keeps_to_the_contract(tmp_path, key):
    w = World(tmp_path).full()  # a world of its own: changes do not leak into the next call
    path, body = POSTS[key](w)
    client = w.client
    if key in LOCAL:  # a browser on the computer Muninn runs on
        client = TestClient(w.client.app, base_url="http://127.0.0.1:18795", client=("127.0.0.1", 50000),
                            headers={"Cookie": "; ".join(f"{k}={v}" for k, v in w.client.cookies.items())})
    r = (client.post(path, content=body, headers={"Content-Type": "image/png", "X-Muninn-Upload": "1"}) if isinstance(body, bytes)
         else client.post(path, json=body))
    assert r.status_code == 200, f"{key}: {r.status_code} {r.text}"
    conforms(key, r.json())


def test_a_refusal_answers_an_error(full):
    for path in ("/api/prepared/999", "/api/items/999"):
        r = full.client.get(path)
        assert r.status_code == 404
        Draft202012Validator({"$ref": "#/$defs/ErrorReply", "$defs": CONTRACT["$defs"]}).validate(r.json())


# lists the full world may leave empty, and why
MAY_BE_EMPTY = {"/domains/0/week": "not in the contract: the pages do not read a domain's planned week",
                "/items/0/lines": "what waits first is one thing; only a deep talk's undecided questions come as several lines"}


def lists(x: Any, path: str = "") -> list[tuple[str, int]]:
    out = []
    if isinstance(x, list):
        out.append((path, len(x)))
        for i, v in enumerate(x[:1]):
            out += lists(v, f"{path}/0")
    elif isinstance(x, dict):
        for k, v in x.items():
            out += lists(v, f"{path}/{k}")
    return out


@pytest.mark.parametrize("key", ["GET /api/conversation", "GET /api/topics", "GET /api/waiting", "GET /api/home", "GET /api/day", "GET /api/today", "GET /api/inbox", "GET /api/confirmations",
                                 "GET /api/review", "GET /api/attribution", "GET /api/requests", "GET /api/project-questions",
                                 "GET /api/permission-requests/pending"])
def test_the_full_world_fills_every_list_so_item_shapes_are_checked(full, key):
    path, _ = GETS[key](full)
    empty = [p for p, n in lists(full.client.get(path).json()) if n == 0 and p not in MAY_BE_EMPTY]
    assert empty == [], f"{key}: empty in the full world, so the shape of its items goes unchecked"


def test_the_contract_notices_a_field_that_changes_type_or_goes_missing(full):
    home = full.client.get("/api/home").json()
    with pytest.raises(AssertionError, match="domains"):
        conforms("GET /api/home", {**home, "domains": "4"})
    with pytest.raises(AssertionError, match="ideas"):
        conforms("GET /api/home", {k: v for k, v in home.items() if k != "ideas"})
    conforms("GET /api/home", {**home, "something_new": 1})  # an added field is fine


def test_in_a_container_the_gateway_a_published_port_arrives_through_is_found(tmp_path):
    from muninn.main import container_gateway

    route = tmp_path / "route"
    route.write_text("Iface\tDestination\tGateway\tFlags\n"
                     "eth0\t00000000\t010011AC\t0003\n"  # default via 172.17.0.1
                     "eth0\t000011AC\t00000000\t0001\n")
    assert container_gateway(route) == ["172.17.0.1"]
    assert container_gateway(tmp_path / "none") == []
