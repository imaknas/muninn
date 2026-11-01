from __future__ import annotations

import base64
from datetime import timedelta

from conftest import TZ, at, make_harness
from muninn.adapters.sources.location.owntracks import OwnTracks
from muninn.core.location import observation
from muninn.core.models import Observation, Sensitivity
from muninn.core.places import Place, distance_m, place_at, stays

HOME = (25.0330, 121.5654)
OFFICE = (25.0478, 121.5170)


def loc(t, lat, lon, i=0):
    return Observation("location", t, "location", f"location:p:{int(t.timestamp())}:{i}", {"lat": lat, "lon": lon}, sensitivity=Sensitivity.SENSITIVE)


def track(day=21):
    pts = []
    t = at(2026, 10, day, 8)
    for _ in range(8):  # home 08:00–09:45
        pts.append(loc(t, *HOME)); t += timedelta(minutes=15)
    t = at(2026, 10, day, 10, 15)
    for _ in range(25):  # office 10:15–16:15
        pts.append(loc(t, OFFICE[0] + 0.0002, OFFICE[1])); t += timedelta(minutes=15)
    return pts


def test_an_owntracks_report_is_a_location_point_kept_as_the_contract_says():
    [p] = OwnTracks().points({"_type": "location", "lat": 25.03, "lon": 121.56, "tst": 1792540800, "acc": 12, "batt": 80, "tid": "kn", "secret": "x"})
    o = observation(p, "owntracks")
    assert o.kind == "location" and o.payload == {"lat": 25.03, "lon": 121.56, "device": "kn", "app": "owntracks", "accuracy_m": 12.0}
    assert o.sensitivity == Sensitivity.SENSITIVE and o.key == "location:kn:1792540800"  # the same key as before: a resent point is one
    assert OwnTracks().points({"_type": "transition"}) == [] and OwnTracks().reply() == []


def test_stays_and_places():
    assert 4000 < distance_m(*HOME, *OFFICE) < 6000
    office = Place("公司", *OFFICE, 200, "work")
    got = stays(track(), [office])
    # home 08:00 until the first office point (10:15); office until its last point
    assert [(s.place.name if s.place else None, round(s.hours, 2)) for s in got] == [(None, 2.25), ("公司", 6.0)]
    sparse = [loc(at(2026, 10, 21, 1), *HOME), loc(at(2026, 10, 21, 9), *HOME, i=1)]
    [home] = stays(sparse, [], until=at(2026, 10, 21, 12))
    assert home.hours == 11  # silence means still there
    assert place_at(*HOME, [office]) is None


def test_name_place_attributes_location_and_never_cloud():
    h = make_harness(at(2026, 10, 21, 22))
    h.engine.ingest(track(), evaluate=False)
    home = h.engine.home_view()["places"]
    assert [p["place"] for p in home] == [None, None]
    h.engine.whereabouts.name("公司", home[1]["lat"], home[1]["lon"])
    h.engine.whereabouts.name("家", home[0]["lat"], home[0]["lon"])
    assert [p["place"] for p in h.engine.home_view()["places"]] == ["家", "公司"]
    # a place is context: location points get no domain from a place without one
    office_obs = h.store.observations(at(2026, 10, 21, 11), at(2026, 10, 21, 12), kinds=("location",))
    assert {o.domain for o in office_obs} == {None}
    assert h.engine.places_line() == "今天的位置:公司 11.8h;家 2.2h"  # office lasts until now (22:00)
    [summary] = h.engine.evening()
    assert summary.body.startswith("今天的位置:公司")
    assert all("121.5" not in p for _, p in h.cloud.calls)  # coordinates never sent to the cloud


def test_owntracks_endpoint_auth(web):
    h, client, devices = web
    body = {"_type": "location", "lat": 25.03, "lon": 121.56, "tst": 1792540800, "tid": "kn"}
    assert client.post("/api/ingest/owntracks", json=body).status_code == 401
    bad = base64.b64encode(b"kn:wrong").decode()
    assert client.post("/api/ingest/owntracks", json=body, headers={"authorization": f"Basic {bad}"}).status_code == 401
    good = base64.b64encode(b"kn:tok").decode()
    r = client.post("/api/ingest/owntracks", json=body, headers={"authorization": f"Basic {good}"})
    assert r.status_code == 200 and r.json() == []
    assert h.store.observations(at(2026, 10, 1, 0), kinds=("location",))
    client.post("/api/pair", json={"code": devices.new_pairing_code()})
    named = client.post("/api/places", json={"name": "家", "lat": 25.03, "lon": 121.56}).json()
    assert named["ok"] and named["text"] == "記住了:家" and named["action_id"]
    [ot] = client.get("/api/setup/location").json()["apps"]
    import json as _json
    from urllib.parse import unquote

    cfg = _json.loads(base64.b64decode(unquote(ot["link"].split("inline=", 1)[1])))
    assert "+" not in ot["link"].split("inline=", 1)[1] and {f["label"]: f["value"] for f in ot["fields"]}["Password"] == "tok"
    assert cfg["password"] == "tok" and cfg["url"].endswith("/api/ingest/location/owntracks") and cfg["mode"] == 3
    setup = client.get("/api/setup/location").json()
    assert setup["last_received"] and TZ


def test_day_starts_where_the_night_was():
    h = make_harness(at(2026, 10, 21, 10))
    h.engine.ingest([loc(at(2026, 10, 20, 23), *HOME)], evaluate=False)  # last point last night, then silence
    [st] = h.engine.day_stays()
    assert st.start == at(2026, 10, 21, 0) and st.end == at(2026, 10, 21, 10)


def test_current_place_shows_right_away():
    h = make_harness(at(2026, 10, 21, 2, 47))
    h.engine.ingest([loc(at(2026, 10, 21, 2, 42), *HOME)], evaluate=False)
    [st] = h.engine.day_stays()
    assert st.start == at(2026, 10, 21, 2, 42) and st.end == at(2026, 10, 21, 2, 47)


def test_what_was_done_where():
    h = make_harness(at(2026, 10, 21, 22))
    h.engine.ingest(track(), evaluate=False)
    h.engine.whereabouts.name("公司", OFFICE[0], OFFICE[1])
    # an hour of work-repo commits at the office, one resume commit at home
    commits = [Observation("git", at(2026, 10, 21, 11, m), "commit", f"g{m}", {"repo": "work-api"}, sensitivity=Sensitivity.PERSONAL)
               for m in (0, 15, 30, 45)]
    commits.append(Observation("git", at(2026, 10, 21, 9), "commit", "gh", {"repo": "resume"}, sensitivity=Sensitivity.PERSONAL))
    h.engine.ingest(commits, evaluate=False)
    hours, by_place = h.engine.where_and_what(at(2026, 10, 21, 0), at(2026, 10, 21, 22))
    assert round(by_place["公司"]["work"], 2) == round(55 / 60, 2)  # 45 min of commits + 10 min pad
    assert "jobs" in by_place["其他地方"]
    line = h.engine.places_line()
    assert line.startswith("今天的位置:公司 11.8h(Work 0.9h)")


def test_coarse_fix_near_a_named_place_counts_as_there():
    home = Place("家", 25.01740, 121.53970, 150)
    # a precise fix at home, then a cell-tower fix 360 m off with 1.3 km error
    pts = [loc(at(2026, 10, 21, 16), 25.01740, 121.53970), loc(at(2026, 10, 21, 17, 35), 25.01547, 121.53664, i=1)]
    pts[1].payload["acc"] = 1326
    [st] = stays(pts, [home], until=at(2026, 10, 21, 18))
    assert st.place == home and st.hours == 2
    assert place_at(25.01547, 121.53664, [home], acc=1326) == home
    assert place_at(25.01547, 121.53664, [home], acc=10) is None


def test_jitter_at_the_same_place_is_one_stay():
    home = Place("家", 25.01740, 121.53970, 150)
    pts = [loc(at(2026, 10, 21, 8), 25.01740, 121.53970),
           loc(at(2026, 10, 21, 9), 25.01979, 121.54087, i=1),  # ~280 m jitter, precise-looking
           loc(at(2026, 10, 21, 10), 25.01740, 121.53970, i=2)]
    pts[1].payload["acc"] = 300
    got = stays(pts, [home], until=at(2026, 10, 21, 11))
    assert [(s.place.name, s.hours) for s in got] == [("家", 3.0)]


def test_a_place_named_by_saying_so_can_be_taken_back():
    import json

    h = make_harness(at(2026, 10, 21, 17))
    h.engine.ingest(track(), evaluate=False)
    c = h.engine.conversation()
    for words, at_time, name in (("我早上在的地方是家", "08:30", "家"), ("我現在在公司", None, "公司")):
        h.cloud.reply = h.local.reply = json.dumps({"actions": [{"tool": "name_place", "args": {"name": name, "at": at_time}}], "reply": ""})
        [outcome] = c.execute_turn(c.understand_turn(None, words)).outcomes
        assert outcome.text.startswith(f"記住了:{name}(") and outcome.action_id
    assert [p["place"] for p in h.engine.home_view()["places"]] == ["家", "公司"]
    assert h.engine.undo_action(outcome.action_id)  # "公司" was a mistake
    assert [p["place"] for p in h.engine.home_view()["places"]] == ["家", None]
    h.cloud.reply = h.local.reply = json.dumps({"actions": [{"tool": "name_place", "args": {"name": "健身房", "at": "23:00"}}], "reply": ""})
    [missing] = c.execute_turn(c.understand_turn(None, "晚上十一點在健身房")).outcomes
    assert "沒有位置紀錄" in missing.text


def test_renaming_and_undoing_puts_back_what_the_name_meant():
    h = make_harness(at(2026, 10, 21, 17))
    first = h.engine.whereabouts.name("健身房", *HOME)
    second = h.engine.whereabouts.name("健身房", *OFFICE)  # moved gyms
    assert h.engine.whereabouts.saved("健身房")["lat"] == OFFICE[0]
    assert h.engine.undo_action(second.id)
    assert h.engine.whereabouts.saved("健身房")["lat"] == HOME[0]
    assert h.engine.undo_action(first.id) and h.engine.whereabouts.saved("健身房") is None


class Pinger:
    """Another tracking app: points in a batch of its own shape, a reply of its own."""

    name, label = "pinger", "Pinger"

    def points(self, body):
        from datetime import datetime

        from muninn.core.location import LocationPoint

        return [LocationPoint(datetime.fromisoformat(p["at"]), p["y"], p["x"], "pad") for p in (body or {}).get("pings", [])]

    def reply(self):
        return {"result": "ok"}

    def setup(self, url, token, words):
        from muninn.core.location import AppSetup

        return AppSetup(None, [("Endpoint", url), ("Key", token)], "Pinger → Sync")


def test_any_tracking_app_posts_to_its_own_address_and_the_old_one_still_works(tmp_path):
    from starlette.testclient import TestClient

    from muninn.adapters.channels.webpush import VapidKeys
    from muninn.adapters.web.app import create_app
    from muninn.adapters.web.devices import DeviceRegistry

    h = make_harness(at(2026, 10, 21, 9))
    keys = VapidKeys(tmp_path / "secrets" / "vapid.pem")
    keys.ensure()
    devices = DeviceRegistry(tmp_path / "dev.db")
    client = TestClient(create_app(h.engine, keys, "tok", devices, location_apps=(OwnTracks(), Pinger())), base_url="https://testserver")
    auth = {"Authorization": "Bearer tok"}
    r = client.post("/api/ingest/location/pinger", headers=auth, json={"pings": [{"at": "2026-10-21T09:00:00+08:00", "y": 25.03, "x": 121.56}]})
    assert r.json() == {"result": "ok"}
    basic = {"Authorization": "Basic " + base64.b64encode(b"muninn:tok").decode()}
    assert client.post("/api/ingest/owntracks", headers=basic, json={"_type": "location", "lat": 25.04, "lon": 121.51, "tst": 1792540800}).json() == []
    kept = h.store.observations(at(2026, 10, 1), at(2026, 11, 1), kinds=("location",))
    assert sorted(o.payload["app"] for o in kept) == ["owntracks", "pinger"]
    assert client.post("/api/ingest/location/nope", headers=auth, json={}).status_code == 404
    assert client.post("/api/ingest/location/pinger", json={}).status_code == 401
    client.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "x"})
    setup = client.get("/api/setup/location").json()
    assert [a["name"] for a in setup["apps"]] == ["owntracks", "pinger"] and setup["last_app"] in ("owntracks", "pinger")
    pinger = setup["apps"][1]
    assert pinger["link"] is None and pinger["fields"][0]["value"].endswith("/api/ingest/location/pinger")
    assert [f["secret"] for f in pinger["fields"]] == [False, True]  # the token is never shown, only copied
