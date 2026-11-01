"""Creating and putting away a domain: through the gate, by the rule table (the user's words are
done at once with an undo, the model's proposal is asked first), its goals waiting in the plan
review, and its source told on creation and on undo. Invented domains only."""

from __future__ import annotations

import pytest

from muninn.adapters.plans_yaml import YamlPlanRepo
from muninn.core.actions import DecidedBy

from conftest import TZ, at, make_harness

DRAFT = {
    "domain": {"id": "pottery", "title": "Pottery", "priority": 3},
    "timezone": TZ,
    "goals": [{"id": "wheel", "title": "Wheel practice", "metric": "hours", "target": 3, "period": "week",
               "params": {"source": "idea:7 (the user wanted to learn throwing)"}}],
    "deadlines": [{"id": "kiln", "title": "Book a kiln slot", "due": "2026-11-20", "source": "idea:7"}],
}


def test_the_users_words_create_it_at_once_unconfirmed_and_sensitive_and_undo_puts_it_away():
    h = make_harness(at(2026, 10, 24, 10))
    heard = []
    h.engine.plans_actor._on_created = lambda d, s: heard.append(("created", d, s))
    h.engine.plans_actor._on_uncreated = lambda d, s: heard.append(("uncreated", d, s))
    ran = h.engine.plans_actor.create(DRAFT, DecidedBy.USER, source="idea:7")
    assert ran.text == "建立「Pottery」這個領域"
    plan = h.engine._plan("pottery", include_unconfirmed=True)
    assert plan.sensitivity_default.name == "SENSITIVE"  # sensitive unless the draft says otherwise
    assert h.engine._plan("pottery").goals == ()  # its goals wait for the user in the plan review
    assert {q["id"] for q in h.engine.review_queue() if q["domain"] == "pottery"} == {"pottery/goal:wheel", "pottery/deadline:kiln"}
    assert heard == [("created", "pottery", "idea:7")]
    assert h.engine.undo_action(ran.id)
    assert h.engine._plan("pottery", include_unconfirmed=True) is None and "pottery" in h.engine.plans.archived
    assert heard[-1] == ("uncreated", "pottery", "idea:7")


def test_a_domain_the_model_proposes_is_asked_first():
    h = make_harness(at(2026, 10, 24, 10))
    ran = h.engine.plans_actor.create(DRAFT, DecidedBy.INFERRED_UNSURE)
    assert ran.confirm_id and h.engine._plan("pottery", include_unconfirmed=True) is None
    h.engine.answer(ran.confirm_id, "要")
    assert h.engine._plan("pottery", include_unconfirmed=True).title == "Pottery"


def test_archiving_keeps_it_and_undo_brings_it_back():
    h = make_harness(at(2026, 10, 24, 10))
    h.engine.plans_actor.create(DRAFT, DecidedBy.USER)
    ran = h.engine.plans_actor.archive("pottery")
    assert ran.text == "封存「Pottery」這個領域(資料都留著)" and h.engine._plan("pottery", include_unconfirmed=True) is None
    assert h.engine.undo_action(ran.id)
    assert h.engine._plan("pottery", include_unconfirmed=True) is not None


def test_a_draft_without_a_name_is_refused():
    h = make_harness(at(2026, 10, 24, 10))
    with pytest.raises(ValueError, match="名字"):
        h.engine.plans_actor.create({"domain": {"id": "x"}}, DecidedBy.USER)


def test_the_yaml_store_creates_archives_and_restores_whole_directories(tmp_path):
    repo = YamlPlanRepo(tmp_path, TZ)
    assert repo.create(DRAFT) == "pottery" and (tmp_path / "pottery" / "plan.yaml").is_file()
    with pytest.raises(ValueError, match="already"):
        repo.create(DRAFT)
    with pytest.raises(ValueError, match="lowercase"):
        repo.create({**DRAFT, "domain": {"id": "Bad Id", "title": "x"}})
    repo.archive("pottery")
    assert [p.id for p in repo.load()] == [] and (tmp_path / ".archived" / "pottery" / "plan.yaml").is_file()
    with pytest.raises(ValueError, match="already"):
        repo.create(DRAFT)  # an archived domain keeps its id
    repo.restore("pottery")
    assert [p.id for p in repo.load()] == ["pottery"]


def test_a_drafted_domain_can_be_taken_whole_and_given_back_whole():
    h = make_harness(at(2026, 10, 24, 10))
    h.engine.plans_actor.create(DRAFT, DecidedBy.USER)
    ran = h.engine.plan_review.confirm_domain("pottery")
    assert ran.text == "「Pottery」整份照草稿:確認 2 項"
    assert [q for q in h.engine.review_queue() if q["domain"] == "pottery"] == []
    assert [g.id for g in h.engine._plan("pottery").goals] == ["wheel"]  # now acted on
    assert h.engine.plan_review.confirm_domain("pottery") is None  # nothing waits any more
    assert h.engine.undo_action(ran.id)
    assert {q["id"] for q in h.engine.review_queue() if q["domain"] == "pottery"} == {"pottery/goal:wheel", "pottery/deadline:kiln"}
