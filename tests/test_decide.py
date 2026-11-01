from __future__ import annotations

import json

from conftest import at, make_harness
from muninn.core.decide import LLMDecider, Question
from muninn.core.evals import Evaluator, decisions, reference
from muninn.core.models import Sensitivity
from conftest import RecordingLLM


def question(**kw):
    return Question(**{"point": "packet_fit", "system": "sys", "content": json.dumps({"q": "x"}), "labels": ("fit", "doubt", "no"),
                       "sensitivity": Sensitivity.PERSONAL, "domain": "jobs", "field": "verdict", **kw})


def test_the_general_model_decides_among_the_labels_and_the_verdict_is_kept_on_its_case():
    h = make_harness(at(2026, 10, 24, 21))
    h.cloud.reply = h.local.reply = json.dumps({"verdict": "fit", "why": "符合"})
    kept = {}
    v = LLMDecider(h.engine.router, lambda case, verdict: kept.update({case: verdict})).decide(question())
    assert (v.label, v.confidence, v.why) == ("fit", None, "符合") and v.case is not None
    assert kept == {v.case: {"label": "fit", "confidence": None, "by": v.by}}


def test_an_answer_outside_the_labels_or_no_answer_decides_nothing():
    h = make_harness(at(2026, 10, 24, 21))
    h.cloud.reply = h.local.reply = json.dumps({"verdict": "maybe"})
    assert LLMDecider(h.engine.router).decide(question()) is None
    h.cloud.reply = h.local.reply = "不知道"
    assert LLMDecider(h.engine.router).decide(question()) is None


def fit_case(h, label, feedback=(), *, by_local=False, ms=1200):
    cid = h.store.add_eval_case(h.clock.now(), "packet_fit", "sys", {"q": "x"}, json.dumps({"verdict": label}),
                                "local" if by_local else "cloud", by_local=by_local, cloud_ok=True, json_mode=True, ms=ms)
    h.store.set_case_verdict(cid, {"label": label, "confidence": None, "by": "cloud"})
    for f in feedback:
        h.store.add_case_feedback(cid, f)
    return cid


def test_the_users_word_is_the_reference_for_a_decision_even_on_a_local_models_case():
    h = make_harness(at(2026, 10, 24, 21))
    fit_case(h, "doubt", [{"kind": "answer", "accepted": True}], by_local=True)
    [c] = h.store.eval_cases("packet_fit")
    assert reference(c) == "prepare"
    rep = Evaluator(h.store, RecordingLLM("cand", True, reply=json.dumps({"verdict": "fit"}))).run("packet_fit")["packet_fit"]
    assert rep.mean == 1.0 and len(rep.ms) == 1 and rep.ref_mean_ms == 1200


def test_the_decisions_report_counts_what_the_users_word_settles():
    h = make_harness(at(2026, 10, 24, 21))
    fit_case(h, "fit", [{"kind": "sent"}])  # right
    fit_case(h, "fit", [{"kind": "set_aside"}], ms=800)  # wrong: prepared on its own, set aside
    fit_case(h, "no", [{"kind": "answer", "accepted": False}])  # right
    fit_case(h, "doubt")  # nothing said yet
    points = {"packet_fit": {"kind": "decide"}, "answer": {"kind": "generate"}, "mail_triage": {"kind": "decide"}}
    rows = {r["point"]: r for r in decisions(points, h.store.eval_cases())}
    assert set(rows) == {"packet_fit", "mail_triage"}
    r = rows["packet_fit"]
    assert (r["cases"], r["settled"], r["right"]) == (4, 3, 2) and round(r["accuracy"], 2) == 0.67
    assert r["mean_ms"] == (1200 * 3 + 800) / 4 and r["by"] == ["cloud"]
    assert rows["mail_triage"]["cases"] == 0 and rows["mail_triage"]["accuracy"] is None
