from __future__ import annotations

import json

from conftest import at, make_harness
from muninn.core.models import Observation, Sensitivity
from muninn.core.window_classify import WindowClassifier


def win(i, title, app="Code", minutes=30, hour=10):
    return Observation("aw", at(2026, 10, 21, hour, i), "window", f"w{i}:{title}",
                       {"app": app, "title": title, "duration_s": minutes * 60}, sensitivity=Sensitivity.PERSONAL)


def test_title_naming_a_project_counts_for_it():
    h = make_harness(at(2026, 10, 21, 20))
    h.engine.ingest([win(0, "main.go — go-katas", minutes=60)], evaluate=False)
    [o] = h.store.observations(at(2026, 10, 21, 0), kinds=("window",))
    assert (o.domain, o.goal) == ("jobs", "jobs/golang")


def test_model_classifies_unknown_titles_once_locally_and_hours_count():
    h = make_harness(at(2026, 10, 21, 20))
    h.engine.window_classifier = WindowClassifier(h.engine.router)
    h.cloud.reply = "CLOUD"
    h.engine.ingest([win(0, "Practice: worker pools in Go", minutes=60), win(1, "YouTube", app="Safari", hour=11)], evaluate=False)
    h.local.reply = json.dumps({"windows": [
        {"title": "Practice: worker pools in Go", "domain": "jobs", "goal": "jobs/golang", "sure": True},
        {"title": "YouTube", "domain": None, "goal": None},
    ]})
    assert h.engine.classify_windows() == 1
    assert h.cloud.calls == [] and "worker pools" in h.local.calls[-1][1]
    calls = len(h.local.calls)
    assert h.engine.classify_windows() == 0 and len(h.local.calls) == calls  # remembered, not asked again
    goals = {g["id"]: g for g in h.engine.today_view()["domains"][0]["goals"]}
    assert goals["golang"]["actual"] > 1.0  # an hour in that window
    [o] = [x for x in h.store.observations(at(2026, 10, 21, 0), kinds=("window",)) if x.payload["title"].startswith("Practice")]
    assert o.sensitivity == Sensitivity.SENSITIVE  # stays local-only


def test_invalid_classification_is_ignored():
    h = make_harness(at(2026, 10, 21, 20))
    h.engine.window_classifier = WindowClassifier(h.engine.router)
    h.engine.ingest([win(0, "Something")], evaluate=False)
    h.local.reply = json.dumps({"windows": [{"title": "Something", "domain": "nope", "goal": "jobs/golang"}]})
    h.engine.classify_windows()
    [o] = h.store.observations(at(2026, 10, 21, 0), kinds=("window",))
    assert o.domain is None and o.goal is None


def test_progress_shows_unconfirmed_goals_marked():
    h = make_harness(at(2026, 10, 21, 20), confirm_all=False)
    goals = h.engine.today_view()["domains"][0]["goals"]
    assert goals and all(g["pending"] for g in goals)
    h.engine.review("jobs/goal:apply", "confirm")
    goals = {g["id"]: g for g in h.engine.today_view()["domains"][0]["goals"]}
    assert goals["apply"]["pending"] is False and goals["golang"]["pending"] is True


def _label(h, title, domain, sure):
    h.local.reply = json.dumps({"windows": [{"title": title, "domain": domain, "goal": "jobs/golang" if domain == "jobs" else None,
                                             "sure": sure}]})


def _counted(h, title):
    [o] = [x for x in h.store.observations(at(2026, 10, 21, 0), kinds=("window",)) if x.payload["title"] == title]
    return o.domain


def test_an_unsure_label_counts_only_after_a_second_look_agrees():
    h = make_harness(at(2026, 10, 21, 20))
    h.engine.window_classifier = WindowClassifier(h.engine.router)
    h.engine.ingest([win(0, "tedhenkels (Ted Henkels)", minutes=30)], evaluate=False)
    _label(h, "tedhenkels (Ted Henkels)", "jobs", False)
    h.engine.classify_windows()
    assert _counted(h, "tedhenkels (Ted Henkels)") is None
    h.engine.recheck_windows()  # the same answer twice
    assert _counted(h, "tedhenkels (Ted Henkels)") == "jobs"
    calls = len(h.local.calls)
    h.engine.recheck_windows()
    assert len(h.local.calls) == calls  # settled: not asked again


def test_disagreeing_looks_count_nothing_and_ask_only_for_big_windows():
    h = make_harness(at(2026, 10, 21, 20))
    h.engine.window_classifier = WindowClassifier(h.engine.router)
    h.engine.ingest([win(0, "Notes", minutes=90), win(5, "Small thing", minutes=10)], evaluate=False)
    h.local.reply = json.dumps({"windows": [{"title": "Notes", "domain": "jobs", "sure": False},
                                            {"title": "Small thing", "domain": "jobs", "sure": False}]})
    h.engine.classify_windows()
    h.local.reply = json.dumps({"windows": [{"title": "Notes", "domain": "learning", "sure": False},
                                            {"title": "Small thing", "domain": "learning", "sure": False}]})
    h.engine.recheck_windows()
    assert _counted(h, "Notes") is None and _counted(h, "Small thing") is None
    asks = [c for c in h.engine.pending_confirmations() if c.kind == "window_class"]
    assert len(asks) == 1 and "Notes" in asks[0].question  # 1.5 h is worth a question; 10 minutes is not
    h.engine.answer(asks[0].id, asks[0].options[1])
    assert _counted(h, "Notes") == asks[0].data["domains"][asks[0].options[1]]  # the user's answer counts from now on


def test_a_generic_window_follows_the_project_active_at_the_time():
    h = make_harness(at(2026, 10, 21, 20))
    h.engine.window_classifier = WindowClassifier(h.engine.router)
    h.engine.ingest([win(0, "Claude", app="Claude", minutes=20),
                     Observation("claude", at(2026, 10, 21, 10, 5), "claude_activity", "c1", {"repo": "resume"}, sensitivity=Sensitivity.PERSONAL)],
                    evaluate=False)
    h.engine.classify_windows()
    assert not any("Claude" in p for _, p in h.local.calls)  # never asked: the title says nothing
    h.engine.attribute_by_context()
    [o] = h.store.observations(at(2026, 10, 21, 0), kinds=("window",))
    assert o.domain == "jobs"  # resume → jobs in the test rules


def test_a_window_answer_given_in_a_discussion_can_be_undone():
    from muninn.core.models import Confirmation

    h = make_harness(at(2026, 10, 21, 20))
    h.engine.ingest([win(0, "Notes", minutes=90)], evaluate=False)
    c = h.store.add_confirmation(Confirmation(None, h.clock.now(), "window_class", "「Notes」算哪裡?", ("Jobs", "都不算"),
                                              {"window": "Code\tNotes", "domains": {"Jobs": "jobs"}}, key="w"))
    h.local.reply = json.dumps({"actions": [{"tool": "answer_question", "args": {"answer": "Jobs"}}], "reply": "好"})
    r = h.engine.conversation().execute_turn(h.engine.conversation().understand_turn(None, "算求職", {"kind": "question", "id": str(c.id)}))
    [o] = h.store.observations(at(2026, 10, 21, 0), kinds=("window",))
    assert o.domain == "jobs" and r.outcomes[0].action_id
    h.engine.undo_action(r.outcomes[0].action_id)
    [o] = h.store.observations(at(2026, 10, 21, 0), kinds=("window",))
    assert o.domain is None and "Code\tNotes" not in h.store.get_state("window_classes")


def test_an_answer_given_while_the_model_was_labelling_stays_the_users():
    h = make_harness(at(2026, 10, 21, 20))
    h.engine.window_classifier = WindowClassifier(h.engine.router)
    h.engine.ingest([win(0, "Practice: worker pools in Go", minutes=60)], evaluate=False)

    def complete(system, prompt, *, json_mode=False):  # the user answers (on the phone) while the model thinks
        h.engine.windows.set_domain("Code\tPractice: worker pools in Go", "health")
        return json.dumps({"windows": [{"title": "Practice: worker pools in Go", "domain": "jobs", "goal": "jobs/golang", "sure": True}]})

    h.local.complete = complete
    h.engine.classify_windows()
    assert h.store.get_state("window_classes")["Code\tPractice: worker pools in Go"]["by"] == "user"
