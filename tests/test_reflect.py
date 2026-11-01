from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from conftest import at, make_harness
from muninn.adapters.sources.conversations import TranscriptTextSource, clean
from muninn.core.models import Turn
from muninn.core.reflect import Reflector, aggregate, excerpt, export_digests, parse_digest

SECRET = "PRIVATE-WORDS-91c2"
DIGEST = json.dumps({"summary": "想投遞", "domain": "jobs", "commitments": ["今晚投 A"], "decisions": [{"what": "先投台北", "why": "要勞健保"}],
                     "themes": ["投遞", "房子"], "mood": "焦慮", "friction": ["拖延"]}, ensure_ascii=False)


def harness():
    h = make_harness(at(2026, 10, 25, 21))
    h.local.reply = DIGEST
    h.cloud.reply = "cloud"
    h.engine.reflector = Reflector(h.store, h.engine.router, h.clock)
    return h


def turns(session: str, day: int, words: str) -> list[Turn]:
    t = at(2026, 10, day, 10)
    return [Turn(f"{session}:1", session, t, "user", "resume", words),
            Turn(f"{session}:2", session, t + timedelta(minutes=1), "assistant", "resume", "好的,我來幫你。" * 100)]


def test_conversation_text_never_reaches_the_cloud():
    h = harness()
    h.store.add_turns(turns("s1", 22, f"我覺得 {SECRET} 很累") + turns("s2", 23, "明天投 A"))
    assert h.engine.reflector.digest(h.engine.domains(), words=h.engine.words) == 2
    h.local.reply = "這週你在想投遞。"
    h.clock.t = at(2026, 10, 26, 8, 5)  # Monday after (stats exist, quiet hours over): reflects on 10/19–10/25
    [item] = h.engine.reflection()
    assert item.kind == "reflection" and "這週你在想投遞" in item.body
    assert h.cloud.calls == []
    assert any(SECRET in p for _, p in h.local.calls)


def test_local_model_down_means_no_digest_and_no_cloud():
    h = harness()
    h.local.up = False
    h.store.add_turns(turns("s1", 22, "hi"))
    assert h.engine.reflector.digest(h.engine.domains(), words=h.engine.words) == 0
    h.clock.t = at(2026, 10, 26, 8, 5)
    assert h.engine.reflection() == [] and h.cloud.calls == []


def test_digests_are_incremental_per_session():
    h = harness()
    h.store.add_turns(turns("s1", 22, "a"))
    h.engine.reflector.digest(h.engine.domains(), words=h.engine.words)
    assert h.engine.reflector.digest(h.engine.domains(), words=h.engine.words) == 0
    h.store.add_turns([Turn("s1:3", "s1", at(2026, 10, 24, 9), "user", "resume", "b")])
    assert h.engine.reflector.digest(h.engine.domains(), words=h.engine.words) == 1
    # the second digest only saw the new part
    assert "[user] b" in h.local.calls[-1][1] and "[user] a" not in h.local.calls[-1][1]


def test_budget_stops_early():
    h = harness()
    ticks = iter(range(0, 10_000, 100))
    h.engine.reflector = Reflector(h.store, h.engine.router, h.clock, monotonic=lambda: next(ticks))
    for i in range(5):
        h.store.add_turns(turns(f"s{i}", 22, "x"))
    # a session starts only if a whole call (reserve) still fits: 100+50, 200+50 fit 250; 300+50 does not
    assert h.engine.reflector.digest(h.engine.domains(), budget_s=250, reserve_s=50, words=h.engine.words) == 2


def test_preview_does_not_use_up_the_week():
    h = harness()
    h.store.add_turns(turns("s1", 22, "a"))
    h.engine.reflector.digest(h.engine.domains(), words=h.engine.words)
    h.local.reply = "反思"
    [p] = h.engine.reflection(preview=True)
    assert p.title.endswith("(預覽)") and h.channel.sent == []
    h.clock.t = at(2026, 10, 26, 8, 5)
    [item] = h.engine.reflection()
    assert item.title == "本週反思 10/19–10/25" and len(h.channel.sent) == 1
    assert h.engine.reflection() == []
    assert h.engine.home_view()["reflection"]["title"].startswith("本週反思")


def test_parse_and_aggregate():
    d = parse_digest("noise " + DIGEST, {"jobs"})
    assert d["mood"] == "anxious" and d["domain"] == "jobs"  # a mood written as it was before moods were ids: read as its id
    assert parse_digest('{"summary": "x", "mood": "tired"}', set())["mood"] == "tired"
    assert parse_digest('{"summary": "x", "domain": "nope", "mood": "??"}', {"jobs"})["domain"] is None
    assert parse_digest("garbage", {"jobs"}) is None
    agg = aggregate([dict(d), dict(d)], {"jobs": "求職"})
    assert agg["repeated_commitments"] == ["今晚投 A"] and agg["themes"][0] == ("投遞", 2)
    assert parse_digest('{"summary": "一句話:x", "commitments": [{"text": "修 bug", "scope": "project"}, "睡早點"]}', set())["commitments"] == [
        {"text": "修 bug", "scope": "project"}, {"text": "睡早點", "scope": "personal"}]
    ex = excerpt(turns("s", 22, "我的話"))
    assert "[user] 我的話" in ex and len(ex) < 600  # assistant trimmed
    assert parse_digest('{"summary": "One sentence: wanted to apply"}', set())["summary"] == "wanted to apply"
    assert parse_digest('{"summary": "一句話：想投遞"}', set())["summary"] == "想投遞"


def test_digests_kept_with_a_mood_in_words_are_read_as_its_id():
    kept = {"summary": "想投遞", "mood": "疲累", "themes": [], "commitments": [], "decisions": [], "friction": []}
    assert aggregate([kept, {**kept, "mood": "tired"}], {})["moods"] == [("tired", 2)]
    [out], _ = export_digests([{**kept, "session": "s", "until": at(2026, 10, 22, 10)}], "Asia/Taipei", lambda d: False)
    assert out["mood"] == "tired"


def _line(**kw):
    return json.dumps(kw, ensure_ascii=False) + "\n"


def test_transcript_text_source(tmp_path):
    root = tmp_path / "projects" / "p"
    root.mkdir(parents=True)
    ws = tmp_path / "ws"
    t0 = datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc)
    z = lambda m: (t0 + timedelta(minutes=m)).isoformat().replace("+00:00", "Z")  # noqa: E731
    cwd = str(ws / "resume")
    lines = [
        _line(type="assistant", sessionId="agent", timestamp=z(0), cwd=cwd, message={"content": [{"type": "text", "text": "agent alone"}]}),
        _line(type="user", turnOrigin="human", sessionId="s1", uuid="u1", timestamp=z(1), cwd=cwd,
              message={"content": "幫我改履歷<system-reminder>injected</system-reminder>"}),
        _line(type="assistant", sessionId="s1", uuid="a1", timestamp=z(2), cwd=cwd, message={"content": [
            {"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "好"}, {"type": "tool_use", "name": "Read", "input": {"file": "x"}}]}),
        _line(type="user", sessionId="s1", uuid="r1", timestamp=z(3), cwd=cwd, toolUseResult={}, message={"content": [{"type": "tool_result", "content": "FILE CONTENT"}]}),
    ]
    (root / "s.jsonl").write_text("".join(lines))
    src = TranscriptTextSource([tmp_path / "projects"], [ws])
    got, cursor = src.collect(None, t0 + timedelta(hours=1))
    assert [(t.role, t.text) for t in got] == [("user", "幫我改履歷"), ("assistant", "好")]
    assert got[0].project == "resume"
    assert src.collect(cursor, t0 + timedelta(hours=1))[0] == []
    assert clean("x" * 9000).count("[…]") == 1


def test_item_api(web):
    h, client, devices = web
    h.engine.reflector = Reflector(h.store, h.engine.router, h.clock)
    h.local.reply = DIGEST
    client.post("/api/pair", json={"code": devices.new_pairing_code()})
    h.store.add_turns(turns("s1", 21, "a"))
    h.engine.reflector.digest(h.engine.domains(), words=h.engine.words)
    h.local.reply = "反思內容"
    [item] = h.engine.reflection(preview=True)
    r = client.get(f"/api/items/{item.id}").json()
    assert r["body"] == "反思內容" and r["data"]["aggregate"]["sessions"] == 1


def test_known_project_keeps_its_domain():
    h = harness()
    h.store.add_turns([Turn("w:1", "w", at(2026, 10, 22, 10), "user", "work-api", "fix the PR")])
    h.engine.reflect_digest(budget_s=600, reserve_s=0)
    [d] = h.store.digests(at(2026, 10, 19, 0), at(2026, 10, 26, 0))
    assert d["domain"] == "work"  # the model said jobs; the attribution rule wins


class FakeRecords:
    def __init__(self, stats=None):
        self.stats = stats

    def projects(self):
        return {"resume"}

    def todos(self):
        from muninn.core.models import ExternalTodo

        return [ExternalTodo("resume", "me", "投 A"), ExternalTodo("resume", "agent", "整理履歷"), ExternalTodo("other", "pending", "決定 X")]

    def state(self, project):
        return f"STATE of {project}: 決定先投台北"

    def weekly_stats(self, iso_week):
        return self.stats if iso_week == "2026-W43" else None


def test_tracked_projects_defer_to_their_records():
    h = harness()
    h.engine.records = FakeRecords(stats={"totals": {"prompts": 99}, "rhythm": {"late_share": 0.3}, "other": 1})
    h.local.reply = json.dumps({"summary": "改履歷", "commitments": [{"text": "修 README", "scope": "project"}, {"text": "今晚早睡", "scope": "personal"}],
                                "decisions": [{"what": "改標題", "why": "x"}], "themes": ["履歷"], "mood": "專注"}, ensure_ascii=False)
    h.store.add_turns(turns("s1", 22, "a"))
    h.engine.reflector.digest(h.engine.domains(), words=h.engine.words)
    h.local.reply = "反思"
    h.clock.t = at(2026, 10, 26, 8, 5)
    [item] = h.engine.reflection()
    sent = json.loads(h.local.calls[-1][1])
    assert sent["personal_commitments"] == ["今晚早睡"]  # project work belongs to the project's records
    assert sent["decisions_outside_projects"] == [] and "決定先投台北" in sent["project_states"]["resume"]
    assert sent["conversation_rhythm"] == {"totals": {"prompts": 99}, "rhythm": {"late_share": 0.3}}
    assert "project_states" not in item.data["aggregate"]


def test_project_todos_on_home_and_brief():
    h = harness()
    h.engine.records = FakeRecords()
    assert h.engine.home_view()["my_project_todos"] == [{"project": "resume", "items": ["投 A"]}]
    todos = h.engine.today_view()["project_todos"]
    assert todos["agent"] == {"resume": ["整理履歷"]} and todos["pending"] == {"other": ["決定 X"]}
    h.clock.t = at(2026, 10, 26, 8, 30)
    [brief] = h.engine.morning()
    assert "各專案還有 1 件只有你能做的事" in brief.body


def test_parse_external_todos():
    from muninn.adapters.project_records import FileProjectRecords, parse_todos

    text = "# t\n## Only me\n### a\n- [ ] one\n- [x] done\n## Machines can\n### b\n- [ ] two\n## Undecided\n### a\n- [ ] three\n"
    got = parse_todos(text, {"Only me": "me", "Machines": "agent"})
    assert [(t.project, t.owner, t.text) for t in got] == [("a", "me", "one"), ("b", "agent", "two"), ("a", "pending", "three")]
    r = FileProjectRecords(None, {}, None, None)
    assert r.todos() == [] and r.state("../x") is None and r.weekly_stats("bad") is None


def test_week_digests_carry_summaries_never_conversation_text():
    h = harness()
    h.store.add_turns(turns("s1", 22, f"我覺得 {SECRET} 很累"))
    h.store.add_turns([Turn("w:1", "w", at(2026, 10, 22, 10), "user", "work-api", f"fix {SECRET}")])
    h.engine.reflect_digest(budget_s=600, reserve_s=0)
    # the model put a session in no domain, and one in work although its project is unknown
    h.store.save_digest("n", "scratch", at(2026, 10, 23, 9), {"summary": "?", "domain": None}, h.clock.now())
    h.store.save_digest("x", "client-x", at(2026, 10, 23, 9), {"summary": "客戶 X", "domain": "work"}, h.clock.now())
    h.store.save_digest("old", "resume", at(2026, 10, 18, 23), {"summary": "上週", "domain": "jobs"}, h.clock.now())
    h.store.add_turns([Turn("s9:1", "s9", at(2026, 10, 25, 20), "user", "resume", "還沒整理")])
    out = h.engine.week_digests("2026-W43")
    assert SECRET not in json.dumps(out, ensure_ascii=False)
    [d] = out["sessions"]
    assert d == {"session": "s1", "project": "resume", "domain": "jobs", "date": "2026-10-22",
                 "last_turn": "2026-10-22T10:00:00+08:00", "summary": "想投遞", "themes": ["投遞", "房子"], "mood": "anxious",
                 "commitments": [{"text": "今晚投 A", "scope": "personal"}], "decisions": [{"what": "先投台北", "why": "要勞健保"}],
                 "friction": ["拖延"], "user_turns": 1}
    assert out["withheld"] == 3  # work by attribution, work by the model, and no domain
    assert (out["since"], out["until"]) == ("2026-10-19T00:00:00+08:00", "2026-10-26T00:00:00+08:00")
    assert out["undigested_sessions"] == 1 and out["complete"] is False  # the week isn't over, and s9 waits
    h.engine.reflect_digest(budget_s=600, reserve_s=0)
    h.clock.t = at(2026, 10, 26, 8)
    out = h.engine.week_digests("2026-W43")
    assert out["complete"] is True and len(out["sessions"]) == 2
    assert h.engine.week_digests("2026-W42")["sessions"][0]["summary"] == "上週"
    # a project the attribution rules know gives the domain even when the model gave none
    h.store.save_digest("r", "resume", at(2026, 10, 24, 9), {"summary": "履歷", "domain": None}, h.clock.now())
    assert next(d for d in h.engine.week_digests("2026-W43")["sessions"] if d["session"] == "r")["domain"] == "jobs"


def test_digests_cli(tmp_path, capsys):
    import pytest

    from muninn import main

    assert main.main(["--data", str(tmp_path), "digests", "--week", "2026-W43", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sessions"] == [] and out["withheld"] == 0 and out["week"] == "2026-W43"
    with pytest.raises(SystemExit) as e:
        main.main(["--data", str(tmp_path), "digests", "--week", "2026-43"])
    assert e.value.code == 2
