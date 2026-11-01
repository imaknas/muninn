from __future__ import annotations

import json

from conftest import RecordingLLM, at, make_harness
from muninn.core.evals import Evaluator, reference


def case(h, task, reply, *, by_local=False, data=None, feedback=()):
    cid = h.store.add_eval_case(h.clock.now(), task, "sys", data or {"said": "x"}, reply, "local" if by_local else "cloud",
                                by_local=by_local, cloud_ok=True, json_mode=True)
    for f in feedback:
        h.store.add_case_feedback(cid, f)
    return cid


ROUTE = json.dumps({"actions": [{"tool": "set_status", "args": {"target": "jobs/acme", "status": "applied"}}], "reply": "好"})
FACTS = json.dumps({"effects": [{"id": "todo:a:0", "effect": "resolves"}, {"id": "todo:b:1", "effect": "changes"}]})
ITEMS = {"fact": "f", "open_items": [{"id": "todo:a:0", "text": "問 A"}, {"id": "todo:b:1", "text": "問 B"}]}


def test_same_answer_scores_full_and_a_wrong_tool_scores_zero():
    h = make_harness(at(2026, 10, 21, 21))
    case(h, "route", ROUTE)
    assert Evaluator(h.store, RecordingLLM("cand", True, reply=ROUTE)).run()["route"].mean == 1.0
    wrong = json.dumps({"actions": [{"tool": "today", "args": {}}], "reply": ""})
    rep = Evaluator(h.store, RecordingLLM("cand", True, reply=wrong)).run()["route"]
    assert rep.mean == 0.0 and rep.misses[0]["said"] == "x"


def test_facts_score_partial_and_corrections_change_the_reference():
    h = make_harness(at(2026, 10, 21, 21))
    case(h, "facts", FACTS, data=ITEMS,
         feedback=[{"kind": "handoff_cancelled", "item": "問 B"}])
    got = json.dumps({"effects": [{"id": "todo:a:0", "effect": "resolves"}]})
    assert Evaluator(h.store, RecordingLLM("cand", True, reply=got)).run()["facts"].mean == 1.0  # B was turned down


def test_no_reference_from_local_replies_or_undone_actions():
    h = make_harness(at(2026, 10, 21, 21))
    case(h, "route", ROUTE, by_local=True)
    case(h, "route", ROUTE, feedback=[{"kind": "undone"}])
    assert [reference(c) for c in h.store.eval_cases()] == [None, None]
    rep = Evaluator(h.store, RecordingLLM("cand", True, reply=ROUTE)).run()["route"]
    assert rep.scored == 0 and rep.skipped == 2


def test_a_cloud_candidate_never_sees_work_cases():
    h = make_harness(at(2026, 10, 21, 21))
    h.store.add_eval_case(h.clock.now(), "route", "sys", {"said": "客戶"}, ROUTE, "cloud", by_local=False, cloud_ok=False, json_mode=True)
    cand = RecordingLLM("other-cloud", False, reply=ROUTE)
    Evaluator(h.store, cand).run()
    assert cand.calls == []


def test_corrections_reach_the_case():
    h = make_harness(at(2026, 10, 21, 21))
    cid = case(h, "facts", FACTS, data=ITEMS)
    h.engine.link_case("handoff:1", cid)
    h.engine._add_handoff(project="b", fact="f", item="問 B", effect="changes", why="")
    h.engine.set_handoff_status(1, "cancelled")
    assert h.store.eval_cases()[0]["feedback"][0]["kind"] == "handoff_cancelled"


def test_cloud_first_sends_everything_to_the_cloud_and_never_to_the_local_model():
    from muninn.core.llm_router import CloudFirst, LLMRequest, LLMRouter
    from muninn.core.models import Sensitivity

    from muninn.core.evals import CaseRecorder

    h = make_harness(at(2026, 10, 21, 21))
    router = LLMRouter([h.cloud, h.local], ["work"], CloudFirst(), CaseRecorder(h.store, h.clock))
    work = LLMRequest("s", "客戶的合約", Sensitivity.SENSITIVE, "work", task="window_classify")
    assert router.complete_with_backend(work)[1] == "cloud"
    h.cloud.up = False
    assert router.complete_with_backend(work) == (None, None)  # nothing answers; no local model guesses
    tasks = [(c["task"], c["backend"], c["cloud_ok"]) for c in h.store.eval_cases()]
    assert tasks == [("window_classify", None, True), ("window_classify", "cloud", True)]


def test_window_labels_score_by_title_and_the_users_answer_wins():
    h = make_harness(at(2026, 10, 21, 21))
    ref = json.dumps({"windows": [{"title": "A", "domain": "jobs"}, {"title": "B", "domain": "phd"}]})
    case(h, "window_classify", ref, data={"windows": []}, feedback=[{"kind": "window_label", "title": "B", "domain": "learning"}])
    got = json.dumps({"windows": [{"title": "A", "domain": "jobs"}, {"title": "B", "domain": "learning"}]})
    assert Evaluator(h.store, RecordingLLM("cand", True, reply=got)).run()["window_classify"].mean == 1.0


def test_label_tasks_compare_one_field():
    h = make_harness(at(2026, 10, 21, 21))
    case(h, "mail_triage", json.dumps({"category": "interview"}), data={"subject": "x"})
    rep = Evaluator(h.store, RecordingLLM("cand", True, reply=json.dumps({"category": "fyi"}))).run()["mail_triage"]
    assert rep.mean == 0.0


def test_a_raw_text_request_replays_as_text():
    from muninn.core.evals import request_text
    from muninn.core.llm_router import LLMRequest
    from muninn.core.models import Sensitivity

    h = make_harness(at(2026, 10, 21, 21))
    h.engine.router.complete(LLMRequest("s", "not json", Sensitivity.SENSITIVE, None, task="reflect_week"))

    assert request_text(h.store.eval_cases("reflect_week")[0]) == "not json"


def test_the_model_that_answered_a_case_is_scored_on_it_only_where_the_user_said_something():
    h = make_harness(at(2026, 10, 21, 21))
    case(h, "facts", FACTS, data=ITEMS)  # answered by "cloud", nobody said anything of it
    case(h, "facts", FACTS, data=ITEMS, feedback=[{"kind": "declined", "id": "todo:b:1"}])
    rep = Evaluator(h.store, RecordingLLM("cloud", False, reply=FACTS)).run()["facts"]
    assert (rep.scored, rep.skipped) == (1, 1)  # it would only agree with itself on the first


def test_a_cloud_candidate_reads_a_case_with_its_credentials_masked():
    h = make_harness(at(2026, 10, 21, 21))
    case(h, "route", ROUTE, data={"said": "存這個 ghp_" + "a1B2" * 9})
    cand = RecordingLLM("anthropic:claude-haiku-5-5", False, reply=ROUTE)
    Evaluator(h.store, cand).run()
    assert "ghp_" not in cand.calls[0][1] and "[masked GitHub token]" in cand.calls[0][1]


def test_eval_scores_any_configured_model_named_provider_and_model(tmp_path):
    import pytest

    from muninn.config import Config
    from muninn.main import eval_candidate

    (tmp_path / "config.yaml").write_text("llm: {providers: {anthropic: {impl: anthropic, key: {secret: k}}}}\n")
    cfg = Config.load(tmp_path)
    assert eval_candidate(cfg, "anthropic:claude-haiku-5-5", None).name == "anthropic:claude-haiku-5-5"
    assert eval_candidate(cfg, "ollama:qwen3:8b", None).name == "ollama:qwen3:8b"
    with pytest.raises(ValueError, match="known: ollama, anthropic"):
        eval_candidate(cfg, "openai:gpt-x", None)
    with pytest.raises(ValueError, match="no model to score"):
        eval_candidate(cfg, None, None)
