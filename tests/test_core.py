from __future__ import annotations

from datetime import date, time, timedelta

from conftest import ZH, ATTRIBUTION, HEALTH_RAW, LEARNING_RAW, TZ, at, jobs_plan, make_harness
from muninn.core.attribution import Attributor, rules_from_config
from muninn.core.composer import Composer, ComposerConfig, DomainPolicy
from muninn.core.metrics import LateActivityMinutes, SleepFromActivityGap, TrackerDateCount, estimate_hours, night_window
from muninn.core.models import Observation, Sensitivity, Signal, status_at_least
from muninn.core.plan import parse_plan
from muninn.core.timewin import elapsed_fraction


def obs(ts, repo=None, kind="commit", langs=(), sensitivity=Sensitivity.PERSONAL, key=None, **payload):
    p = dict(payload)
    if repo:
        p["repo"] = repo
    if langs:
        p["langs"] = list(langs)
    return Observation("test", ts, kind, key or f"k:{repo}:{ts.isoformat()}:{kind}", p, sensitivity=sensitivity)


# ── attribution ─────────────────────────────────────────────────────────────


def attributor():
    return Attributor(
        rules_from_config(ATTRIBUTION),
        {"jobs": Sensitivity.PERSONAL, "health": Sensitivity.PERSONAL, "work": Sensitivity.SENSITIVE},
        ATTRIBUTION["sensitive_domains"],
    )


def test_repo_rule_sets_domain_and_keeps_personal():
    o = attributor().attribute(obs(at(2026, 10, 20), "resume"))
    assert (o.domain, o.goal, o.sensitivity) == ("jobs", None, Sensitivity.PERSONAL)


def test_language_rule_tags_goal_independently_of_domain():
    o = attributor().attribute(obs(at(2026, 10, 20), "resume", langs=["go"]))
    assert (o.domain, o.goal) == ("jobs", "jobs/golang")
    o = attributor().attribute(obs(at(2026, 10, 20), "go-katas"))
    assert o.goal == "jobs/golang"


def test_unattributed_is_sensitive():
    o = attributor().attribute(obs(at(2026, 10, 20), "unknown-repo"))
    assert o.domain is None and o.sensitivity == Sensitivity.SENSITIVE


def test_work_domain_is_always_sensitive():
    o = attributor().attribute(obs(at(2026, 10, 20), "work-api", sensitivity=Sensitivity.PUBLIC))
    assert o.domain == "work" and o.sensitivity == Sensitivity.SENSITIVE


def test_sensitivity_is_never_lowered():
    o = attributor().attribute(obs(at(2026, 10, 20), "resume", sensitivity=Sensitivity.SENSITIVE))
    assert o.domain == "jobs" and o.sensitivity == Sensitivity.SENSITIVE


def test_unknown_explicit_domain_is_dropped():
    o = Observation("pwa", at(2026, 10, 20), "note", "n1", {"text": "x"}, domain="nope", sensitivity=Sensitivity.PERSONAL)
    assert attributor().attribute(o).domain is None


# ── metrics and time ────────────────────────────────────────────────────────


def test_estimate_hours_clusters_spans():
    t0 = at(2026, 10, 20, 10)
    pts = [t0, t0 + timedelta(minutes=10), t0 + timedelta(minutes=25), t0 + timedelta(hours=2)]
    # span 1: 25 min + 10 pad; span 2: 0 + 10 pad
    assert abs(estimate_hours(pts, timedelta(minutes=20), timedelta(minutes=10)) - 45 / 60) < 1e-9


def test_week_fraction_uses_active_hours():
    assert elapsed_fraction("week", at(2026, 10, 19, 7), TZ) == 0  # Monday before 08:00
    assert abs(elapsed_fraction("week", at(2026, 10, 25, 23, 30), TZ) - 1) < 1e-9


def test_tracker_count_respects_window(harness):
    from dataclasses import replace

    plan = jobs_plan()
    plan = replace(plan, tracker=tuple(replace(r, applied=date(2026, 10, 19)) if r.target == "acme" else r for r in plan.tracker))
    m = TrackerDateCount()
    goal = plan.goal("apply")
    assert m.measure(plan, goal, at(2026, 10, 19, 0), at(2026, 10, 20, 14), harness.store) == 1
    assert m.measure(plan, goal, at(2026, 10, 20, 0), at(2026, 10, 20, 14), harness.store) == 0


def test_night_window_belongs_to_the_evening_before():
    s, e = night_window(at(2026, 10, 21, 1, 30), TZ, time(23, 30), time(6))
    assert s == at(2026, 10, 20, 23, 30) and e == at(2026, 10, 21, 6)
    s, _ = night_window(at(2026, 10, 21, 15), TZ, time(23, 30), time(6))
    assert s == at(2026, 10, 21, 23, 30)


def test_late_activity_minutes(harness):
    plan = parse_plan(HEALTH_RAW, (), TZ)
    goal = plan.goals[0]
    harness.store.add_observations([obs(at(2026, 10, 20, 23, 40), "x"), obs(at(2026, 10, 20, 23, 55), "x")])
    m = LateActivityMinutes()
    assert m.measure(plan, goal, at(2026, 10, 20, 0), at(2026, 10, 20, 23, 0), harness.store) == 0
    assert round(m.measure(plan, goal, at(2026, 10, 21, 0), at(2026, 10, 21, 0, 30), harness.store)) == 25


def test_sleep_from_activity_gap(harness):
    plan = parse_plan(HEALTH_RAW, (), TZ)
    goal = plan.goals[0]
    m = SleepFromActivityGap()
    harness.store.add_observations([obs(at(2026, 10, 20, 23), "x"), obs(at(2026, 10, 20, 23, 50), "x")])
    assert m.measure(plan, goal, at(2026, 10, 21, 0), at(2026, 10, 21, 5), harness.store) is None  # not up yet
    harness.store.add_observations([obs(at(2026, 10, 21, 7, 20), "x")])
    assert abs(m.measure(plan, goal, at(2026, 10, 21, 0), at(2026, 10, 21, 9), harness.store) - 7.5) < 1e-9


def test_status_order():
    assert status_at_least("interview", "applied")
    assert not status_at_least("todo", "applied")
    assert status_at_least("rejected", "applied")  # closed: nothing left to do


# ── drift ───────────────────────────────────────────────────────────────────


def signals(h):
    from muninn.core.drift import evaluate_all

    return {s.key: s for s in evaluate_all(h.engine.rules, h.engine._ctx(h.engine._plans(), h.clock.now()))}


def test_goal_behind_pro_rata():
    h = make_harness(at(2026, 10, 19, 10))  # Monday morning: not behind yet
    assert "jobs:goal:apply" not in signals(h)
    h.clock.t = at(2026, 10, 21, 20)  # Wednesday evening, nothing applied
    s = signals(h)["jobs:goal:apply"]
    assert s.level >= 1 and s.actions == ("Apply to the top open target today",)


def test_deadline_lead_window_and_done_when_targets():
    h = make_harness(at(2026, 10, 21, 9))
    assert "jobs:deadline:first-batch" not in signals(h)  # 3 days left, lead 2
    h.clock.t = at(2026, 10, 22, 9)
    s = signals(h)["jobs:deadline:first-batch"]
    assert "Acme Staff Engineer:Apply; ask salary band" in s.actions
    h.plans.set_tracker_status("jobs", "acme", "applied", date(2026, 10, 22))
    h.plans.set_tracker_status("jobs", "globex", "screening", date(2026, 10, 22))
    assert "jobs:deadline:first-batch" not in signals(h)


def test_manual_deadline_done_by_user():
    h = make_harness(at(2026, 10, 29, 9))
    assert "jobs:deadline:call-bank" in signals(h)
    h.engine.set_done("jobs/call-bank")
    assert "jobs:deadline:call-bank" not in signals(h)


def test_late_activity_goal_bypasses_quiet_hours():
    h = make_harness(at(2026, 10, 20, 23, 50))
    h.engine.ingest([obs(at(2026, 10, 20, 23, 35), "resume"), obs(at(2026, 10, 20, 23, 45), "resume")], evaluate=False)
    s = signals(h)["health:goal:no-late-work"]
    assert s.bypass_quiet and s.domain_priority == 2
    comp = h.engine.evaluate()
    assert any(m.domain == "health" for m in comp.pushes)  # 23:50 is quiet hours, still pushed


def test_generic_late_night_rule_stands_down_when_a_goal_covers_it():
    h = make_harness(at(2026, 10, 21, 2))
    h.store.add_observations([obs(at(2026, 10, 21, 1), "resume")])
    assert not any(k.startswith("anomaly:late-night") for k in signals(h))
    h2 = make_harness(at(2026, 10, 21, 2), plans=[jobs_plan()])
    h2.store.add_observations([obs(at(2026, 10, 21, 1), "resume")])
    assert any(k.startswith("anomaly:late-night") for k in signals(h2))


def test_idle_domain_only_after_observed_activity():
    h = make_harness(at(2026, 10, 20, 12))
    assert "jobs:idle" not in signals(h)  # never observed: no claim of drift
    h.engine.ingest([obs(at(2026, 10, 15, 12), "resume")], evaluate=False)
    assert signals(h)["jobs:idle"].level == 3  # 5 days idle, threshold 3: level 5 - 3 + 1


def test_overdue_next_action():
    h = make_harness(at(2026, 10, 26, 9))
    assert signals(h)["jobs:overdue-actions"].level == 2


# ── composer ────────────────────────────────────────────────────────────────


def sig(key="k", domain="jobs", level=1, prio=1, bypass=False):
    return Signal(key, domain, "t", 1, level, f"title {key}", domain_priority=prio, bypass_quiet=bypass)


def test_dedupe_same_day_unless_worse(harness):
    c = Composer(harness.store, ComposerConfig(tz=TZ), words=ZH)
    now = at(2026, 10, 20, 10)
    assert len(c.compose_alerts([sig(level=1)], now).items) == 1
    assert len(c.compose_alerts([sig(level=1)], now).items) == 0
    assert len(c.compose_alerts([sig(level=2)], now).items) == 1
    assert len(c.compose_alerts([sig(level=1)], at(2026, 10, 21, 10)).items) == 1  # new day


def test_quiet_hours_store_but_do_not_push(harness):
    c = Composer(harness.store, ComposerConfig(tz=TZ), words=ZH)
    comp = c.compose_alerts([sig("a"), sig("b", bypass=True)], at(2026, 10, 20, 23, 30))
    assert len(comp.items) == 2 and [m.count for m in comp.pushes] == [1]


def test_low_priority_domains_go_to_digest_unless_escalated(harness):
    c = Composer(harness.store, ComposerConfig(tz=TZ, escalation_level=3), words=ZH)
    pol = {"learning": DomainPolicy(4), "jobs": DomainPolicy(1)}
    comp = c.compose_alerts([sig("l1", "learning", 1, 4), sig("j1", "jobs", 1, 1)], at(2026, 10, 20, 10), pol)
    assert [m.domain for m in comp.pushes] == ["jobs"]
    assert next(i for i in comp.items if i.domain == "learning").data["digest_only"]
    comp = c.compose_alerts([sig("l2", "learning", 3, 4)], at(2026, 10, 20, 11), pol)
    assert [m.domain for m in comp.pushes] == ["learning"] and comp.tiers["learning"] == "escalation"


def test_observe_only_never_pushes(harness):
    c = Composer(harness.store, ComposerConfig(tz=TZ), words=ZH)
    comp = c.compose_alerts([sig("x", "work", 9, 1)], at(2026, 10, 20, 10), {"work": DomainPolicy(1, observe_only=True)})
    assert comp.items and not comp.pushes


def test_tier_quotas_protect_high_priority(harness):
    h = harness
    cfg = ComposerConfig(tz=TZ, tier_quota={"1": 2, "2": 1, "3": 1, "escalation": 1}, daily_push_cap=10)
    c = Composer(h.store, cfg, words=ZH)
    pol = {"health": DomainPolicy(2), "jobs": DomainPolicy(1)}
    day = date(2026, 10, 20)
    for i in range(3):
        comp = c.compose_alerts([sig(f"h{i}", "health", 1, 2)], at(2026, 10, 20, 9 + i), pol)
        for m in comp.pushes:
            h.store.record_push(day, at(2026, 10, 20, 9 + i), m.domain, comp.tiers[m.domain])
    assert h.store.pushes_by_tier(day) == {"2": 1}  # health used its one push
    comp = c.compose_alerts([sig("j", "jobs", 1, 1)], at(2026, 10, 20, 13), pol)
    assert [m.domain for m in comp.pushes] == ["jobs"]  # jobs still has its own quota


def test_learning_plan_is_observe_only():
    assert parse_plan(LEARNING_RAW, (), TZ).observe_only


def test_goal_follows_the_winning_domain():
    rules = rules_from_config({"repos": {"proj": {"domain": "jobs", "goal": "golang"}}},
                              learned={"repo:proj": {"domain": "health", "goal": None}})
    attr = Attributor(rules, {"jobs": Sensitivity.PERSONAL, "health": Sensitivity.PERSONAL}, [])
    o = attr.attribute(obs(at(2026, 10, 20), "proj"))
    assert (o.domain, o.goal) == ("health", None)


def test_a_window_showing_a_file_a_deep_talk_gave_goes_where_that_project_goes():
    from muninn.core.models import Observation

    rules = rules_from_config({"repos": {"sofa-fit": "outward"}}, shown_files={"a208605ac77cff18": "sofa-fit", "0123456789abcdef": "nowhere"})
    win = lambda title: Observation("laptop", at(2026, 10, 26, 9), "window", f"w:{title}", {"app": "Claude", "title": title})  # noqa: E731
    attributed = lambda title: next((r.match(win(title)) for r in rules if r.match(win(title))), None)  # noqa: E731
    assert attributed("a208605ac77cff18 (1200×900)").domain == "outward"
    assert attributed("0123456789abcdef (800×600)") is None  # its project belongs nowhere yet: left to be asked
    assert attributed("ffffffffffffffff (800×600)") is None  # not a file a talk gave


def test_the_store_knows_which_project_each_shown_file_came_from():
    from muninn.adapters.store_sqlite import SqliteStore

    s = SqliteStore(":memory:")
    s.add_lease({"lease_id": "L1", "thread": "t", "project": "sofa-fit", "mode": "work", "state": "attached",
                 "started": "2026-10-26T09:00"})
    s.add_lease_turn("L1", "file", "量好的圖", at(2026, 10, 26, 9), files=[{"id": "a208605ac77cff18", "name": "sofa.png"}])
    assert s.shown_files() == {"a208605ac77cff18": "sofa-fit"}


def test_a_repo_question_a_rule_has_since_answered_is_not_asked():
    from muninn.core.models import Confirmation

    h = make_harness(at(2026, 10, 26, 9))
    h.store.add_observations([obs(at(2026, 10, 26, 8), repo="lit-search")])
    h.store.add_confirmation(Confirmation(None, at(2026, 10, 26, 8), "attribution", "「lit-search」這個 repo 的活動要歸到哪個領域?",
                                          ("jobs", "都不是"), {"rule": "repo", "match": "lit-search"}, key="attr:lit-search"))
    assert any(c.kind == "attribution" for c in h.engine.pending_confirmations())
    h.store.learn_rule("repo", "lit-search", "jobs", None)  # answered another way since
    assert not any(c.kind == "attribution" for c in h.engine.pending_confirmations())
