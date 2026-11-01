from __future__ import annotations

import json

from conftest import CANARY, at, make_harness
from muninn.core.decide import LLMDecider
from muninn.adapters.postings import GreenhouseFetcher, html_to_text
from muninn.core.models import Posting
from muninn.core.evals import packet_fit_truth
from muninn.core.prepare import PacketPreparer, fit_system, packet_system, parse_packet

PACKET_REPLY = json.dumps({
    "summary": "對口", "match": ["a", "b"], "gaps": ["Go"], "resume": "resume-en.pdf",
    "resume_focus": "x", "answers": [{"q": "Why?", "a": "Because."}], "ask_first": ["雇用形式?"],
})


class FakeFetcher:
    def __init__(self):
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        return Posting(url, "Staff Engineer", "Taipei", "We need TypeScript and Go.")


class FakeDocs:
    def documents(self):
        return [("resume-en.pdf", "Resume text: TypeScript, Python")]


def answering(h, verdict="fit", why="履歷有 TypeScript 與 Go,符合基本條件"):
    """The models answer the fit check with `verdict` and anything else with their reply."""
    fit = json.dumps({"verdict": verdict, "why": why}, ensure_ascii=False)
    for llm in (h.cloud, h.local):
        def complete(system, prompt, *, json_mode=False, llm=llm):
            llm.calls.append((system, prompt))
            return fit if system == fit_system(h.engine.words) else llm.reply
        llm.complete = complete


def harness_with_preparer(cloud_drafts: bool, now=None, verdict="fit", why="履歷有 TypeScript 與 Go,符合基本條件"):
    h = make_harness(now or at(2026, 10, 22, 10))
    h.cloud.reply = PACKET_REPLY
    h.local.reply = PACKET_REPLY
    answering(h, verdict, why)
    fetcher = FakeFetcher()
    h.engine.preparer = PacketPreparer(h.engine.router, fetcher, FakeDocs(), decider=LLMDecider(h.engine.router, h.store.set_case_verdict), cloud_drafts=cloud_drafts)
    return h, fetcher


def test_packet_goes_to_cloud_with_only_posting_and_documents():
    h, fetcher = harness_with_preparer(cloud_drafts=True)
    [p] = h.engine.prepare_packets(limit=1)
    assert p.target == "acme" and p.prepared_by == "cloud"
    [prompt] = [p for system, p in h.cloud.calls if system == packet_system(h.engine.words)]
    assert all(CANARY not in p for _, p in h.cloud.calls)  # target notes, pivot, decision tree, filters stay home
    sent = json.loads(prompt)
    assert set(sent) == {"target", "next_action", "answer_language", "posting", "documents"}
    # the fit check carries the user's own conditions: in privacy routing it is the local model's
    assert [system for system, _ in h.local.calls] == [fit_system(h.engine.words)]


def test_without_cloud_drafts_packets_stay_local():
    h, _ = harness_with_preparer(cloud_drafts=False)
    [p] = h.engine.prepare_packets(limit=1)
    assert p.prepared_by == "local" and h.cloud.calls == []


def test_sensitive_domain_packet_never_reaches_cloud():
    from dataclasses import replace

    from muninn.core.models import Sensitivity

    h, _ = harness_with_preparer(cloud_drafts=True)
    h.plans.plans["jobs"] = replace(h.plans.plans["jobs"], sensitivity_default=Sensitivity.SENSITIVE)
    h.engine.prepare_packets(limit=1)
    assert h.cloud.calls == []


def test_candidates_need_url_and_a_due_date_and_are_prepared_once():
    h, fetcher = harness_with_preparer(cloud_drafts=True)
    # globex has no URL in the fixture; acme is due 10/24
    assert [t for _, t in h.engine.packet_candidates()] == ["acme"]
    h.engine.prepare_packets(limit=5)
    assert h.engine.prepare_packets(limit=5) == []
    assert fetcher.urls == ["https://example.com/acme"]


def test_ready_packet_is_announced_without_content():
    h, _ = harness_with_preparer(cloud_drafts=True)
    h.engine.prepare_packets(limit=1)
    assert [[m.text for m in b] for b in h.channel.sent] == [["Acme Staff Engineer 的投遞包準備好了"]]
    home = h.engine.home_view()
    assert home["ready"][0]["title"] == "Acme Staff Engineer"
    assert home["done"][0]["text"].startswith("準備好")


def test_done_marks_applied_and_undo_dismisses():
    h, _ = harness_with_preparer(cloud_drafts=True)
    [p] = h.engine.prepare_packets(limit=1)
    view = h.engine.prepared_view(p.id)
    assert view["url"] == "https://example.com/acme" and view["deadline"]["days_left"] == 2
    rec = h.engine.prepared_done(p.id)
    assert h.plans.plans["jobs"].tracker_item("acme").status == "applied"
    assert h.engine.undo_action(rec.id)  # sent by mistake: the status and the packet both come back
    assert h.plans.plans["jobs"].tracker_item("acme").status == "todo"
    assert h.store.get_prepared(p.id).status == "ready"
    h2, _ = harness_with_preparer(cloud_drafts=True)
    [p2] = h2.engine.prepare_packets(limit=1)
    action = h2.engine.home_view()["done"][0]
    assert h2.engine.undo_action(action["id"])
    assert h2.store.get_prepared(p2.id).status == "dismissed"
    assert packet_fit_truth(fit_case(h2)) == "ask"  # undoing the preparing says it should not have been
    assert h2.engine.home_view()["ready"] == []


def test_unusable_reply_prepares_nothing():
    h, _ = harness_with_preparer(cloud_drafts=True)
    h.cloud.reply = "sorry"
    h.local.reply = "sorry"
    assert h.engine.prepare_packets(limit=1) == []
    assert parse_packet('{"summary": "x"}') is None


def test_greenhouse_urls():
    loc = GreenhouseFetcher.locate
    assert loc("https://job-boards.greenhouse.io/acme/jobs/4000000001") == ("boards-api.greenhouse.io", "acme", "4000000001")
    assert loc("https://job-boards.eu.greenhouse.io/globex/jobs/5000000002")[0] == "boards-api.eu.greenhouse.io"
    assert loc("https://www.example.com/company/careers/open-positions/?gh_jid=8000003") == ("boards-api.greenhouse.io", "example", "8000003")
    assert loc("https://www.amazon.jobs/en/jobs/1") is None


def test_html_to_text():
    t = html_to_text("<html><head><title>x</title><script>var a</script></head><body><h1>Role</h1><ul><li>Go</li><li>TS</li></ul></body></html>")
    assert "var a" not in t and "• Go" in t and "Role" in t


def test_prepared_api(web):
    h, client, devices = web
    h.cloud.reply = PACKET_REPLY
    answering(h)
    h.engine.preparer = PacketPreparer(h.engine.router, FakeFetcher(), FakeDocs(), decider=LLMDecider(h.engine.router), cloud_drafts=True)
    client.post("/api/pair", json={"code": devices.new_pairing_code()})
    [p] = h.engine.prepare_packets(limit=1)
    assert client.get(f"/api/prepared/{p.id}").json()["packet"]["answers"][0]["a"] == "Because."
    assert client.get("/api/home").json()["ready"][0]["id"] == p.id
    r = client.post(f"/api/prepared/{p.id}/done", json={})
    assert r.json()["ok"] and r.json()["action_id"] and r.json()["text"].startswith("送出了")
    assert client.get("/api/documents/nope").status_code == 404


def test_documents_follow_the_posting_language():
    from muninn.core.prepare import matching_documents, text_language

    docs = [("zh.html", "我在分散式系統有八年經驗,負責架構設計與團隊帶領"), ("en.pdf", "Eight years building distributed systems")]
    assert text_language("We are hiring a Staff Engineer in Taipei") == "en"
    assert matching_documents("We are hiring a Staff Engineer", docs) == [docs[1]]
    assert matching_documents("我們正在招募資深工程師,負責後端", docs) == [docs[0]]


def test_local_packet_is_upgraded_once_the_cloud_answers():
    h, _ = harness_with_preparer(cloud_drafts=True)
    h.cloud.up = False
    [p] = h.engine.prepare_packets(limit=1)
    assert p.prepared_by == "local"
    assert h.engine.upgrade_local_packets() == []  # cloud still down: keep it, try again tomorrow
    h.cloud.up = True
    assert h.engine.upgrade_local_packets() == []  # once a day
    h.clock.t = at(2026, 10, 23, 10)
    pushes_before = len(h.channel.sent)
    [new] = h.engine.upgrade_local_packets()
    assert new.prepared_by == "cloud"
    assert [r["id"] for r in h.engine.home_view()["ready"]] == [new.id]
    assert len(h.channel.sent) == pushes_before  # replaced quietly


def test_a_packet_set_aside_is_not_prepared_again_and_can_come_back():
    h, _ = harness_with_preparer(cloud_drafts=True)
    [p] = h.engine.prepare_packets(limit=1)
    rec = h.engine.dismiss_prepared(p.id)
    assert rec.text == "先不投:Acme Staff Engineer" and h.engine.dismiss_prepared(p.id) is None
    assert all(t != "acme" for _, t in h.engine.packets.candidates())
    assert h.engine.undo_action(rec.id)
    assert h.store.get_prepared(p.id).status == "ready"


def test_a_packet_is_a_topic_and_the_conversation_can_set_it_aside():
    h, _ = harness_with_preparer(cloud_drafts=True)
    [p] = h.engine.prepare_packets(limit=1)
    topic = h.engine.about("packet", str(p.id))
    assert topic["title"] == "Acme Staff Engineer" and topic["domain"] == "jobs" and topic["target"] == "acme"
    assert "dismiss_packet" in topic["note"] and h.engine.about("packet", "999") is None
    h.cloud.reply = h.local.reply = json.dumps({"actions": [{"tool": "dismiss_packet", "args": {"why": "條件不符"}}], "reply": "好,這家先不投。"})
    turn = h.engine.conversation().understand_turn(None, "條件都不符合,不投了", {"kind": "packet", "id": str(p.id)})
    prompts = [system + prompt for system, prompt in h.cloud.calls + h.local.calls if "dismiss_packet" in system + prompt]
    assert prompts and "Acme Staff Engineer" in prompts[-1] and '"kind": "packet"' in prompts[-1]  # the model sees the packet it is about
    [outcome] = h.engine.conversation().execute_turn(turn).outcomes
    assert outcome.text == "先不投:Acme Staff Engineer" and outcome.action_id
    assert h.store.get_prepared(p.id).status == "dismissed"
    assert packet_fit_truth(fit_case(h)) == "ask"  # said in the conversation, it is the fit check's answer too


def test_a_clear_fit_is_prepared_and_judged_only_once():
    h, _ = harness_with_preparer(cloud_drafts=True)
    [p] = h.engine.prepare_packets(limit=1)
    assert h.engine.packets.fit("jobs", "acme")["verdict"] == "fit"
    h.engine.dismiss_prepared(p.id)
    h.engine.packets.prepare(limit=1, only="acme")  # prepared again on request: no second fit check
    assert sum(1 for system, _ in h.cloud.calls + h.local.calls if system == fit_system(h.engine.words)) == 1


def test_a_doubtful_fit_is_asked_first_with_the_reason():
    h, _ = harness_with_preparer(cloud_drafts=True, verdict="no", why="基本條件要 7 年分散式系統經驗,履歷約 4 年")
    assert h.engine.prepare_packets(limit=1) == []  # nothing prepared on a poor fit
    [ask] = [c for c in h.engine.pending_confirmations() if c.kind == h.engine.gate.ASK]
    assert ask.question == "還要準備「Acme Staff Engineer」的投遞包嗎?"
    assert ask.data["why"] == "基本條件要 7 年分散式系統經驗,履歷約 4 年"
    assert h.engine.prepare_packets(limit=1) == []  # asked once, not every round
    assert len([c for c in h.engine.pending_confirmations() if c.kind == h.engine.gate.ASK]) == 1
    h.engine.answer(ask.id, "要")  # the user wants it anyway
    [ready] = h.engine.home_view()["ready"]
    assert ready["title"] == "Acme Staff Engineer" and h.engine.packets.fit("jobs", "acme")["decided"] == "yes"


def test_a_doubtful_fit_turned_down_is_not_asked_again():
    h, _ = harness_with_preparer(cloud_drafts=True, verdict="doubt", why="履歷看不出有沒有管理團隊的經驗")
    h.engine.prepare_packets(limit=1)
    [ask] = [c for c in h.engine.pending_confirmations() if c.kind == h.engine.gate.ASK]
    h.engine.answer(ask.id, "不要")
    h.engine.prepare_packets(limit=1)
    assert h.engine.home_view()["ready"] == []
    assert not [c for c in h.engine.pending_confirmations() if c.kind == h.engine.gate.ASK]


def test_a_whole_domain_is_confirmed_through_the_api(web):
    h, client, devices = web
    client.post("/api/pair", json={"code": devices.new_pairing_code()})
    assert client.post("/api/review/domain", json={"domain": "jobs"}).status_code == 404  # the harness confirmed everything already


def test_the_fit_check_weighs_the_users_own_conditions_on_the_cloud_when_chosen_for_everything():
    from muninn.core.llm_router import CloudFirst

    h, _ = harness_with_preparer(cloud_drafts=True)
    h.engine.router._policy = CloudFirst()  # the user chose the cloud for everything
    h.engine.prepare_packets(limit=1)
    [fit] = [json.loads(p) for system, p in h.cloud.calls if system == fit_system(h.engine.words)]
    assert fit["filters"] == [f"only full-time roles {CANARY}"]  # weighed, not left out


def fit_case(h):
    [c] = h.store.eval_cases("packet_fit")
    return c


def test_the_fit_check_is_kept_with_its_verdict_and_the_users_answer():
    h, _ = harness_with_preparer(cloud_drafts=True, verdict="doubt", why="履歷看不出有沒有管理團隊的經驗")
    h.engine.prepare_packets(limit=1)
    c = fit_case(h)
    assert c["verdict"] == {"label": "doubt", "confidence": None, "by": "local"} and c["ms"] is not None
    assert h.engine.packets.fit("jobs", "acme")["case"] == c["id"]
    [ask] = [q for q in h.engine.pending_confirmations() if q.kind == h.engine.gate.ASK]
    h.engine.answer(ask.id, "不要")
    assert packet_fit_truth(fit_case(h)) == "ask"  # the doubt was right: the user did not want it


def test_setting_an_auto_prepared_packet_aside_says_the_fit_was_wrong_and_undo_takes_it_back():
    h, _ = harness_with_preparer(cloud_drafts=True)
    [p] = h.engine.prepare_packets(limit=1)
    assert packet_fit_truth(fit_case(h)) is None  # nothing said yet
    rec = h.engine.dismiss_prepared(p.id)
    assert packet_fit_truth(fit_case(h)) == "ask"
    assert h.engine.undo_action(rec.id)
    assert packet_fit_truth(fit_case(h)) is None
    h.engine.prepared_done(p.id)
    assert packet_fit_truth(fit_case(h)) == "prepare"
