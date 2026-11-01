"""Every stored undo shape reverses through the registry, as the old switch did."""

from __future__ import annotations

from muninn.core.actions import REVERSALS, TASK_KINDS, reversal


class Ctx:
    AUTO_TAG = "auto"

    def __init__(self, calendar=True):
        self.calls = []
        self.store = self
        self.plans = self
        self.tasks = self
        self.ideas = self
        self.plans_actor = self
        self.conversation = lambda: self
        self.whereabouts = self
        self.requests = self
        self.calendar = self if calendar else None
        self.state = {"window_classes": {"Code\tNotes": {"domain": "jobs"}}}

    def __getattr__(self, name):  # every other operation is recorded
        def call(*a, **k):
            self.calls.append(name)
            return True
        return call

    def get_state(self, key):
        return self.state.get(key)

    def set_state(self, key, value):
        self.state[key] = value

    def observations(self, *a, **k):
        return []


# the shapes found in the live store, plus the kinds no record uses yet
STORED = [
    {"kind": "calendar_event", "id": "e1"},
    {"kind": "deadline_done", "id": "jobs/call-bank"},
    {"kind": "dismiss_prepared", "id": 3},
    {"kind": "restore_prepared", "id": 3},
    {"kind": "packet_sent", "id": 3, "change": 7},
    {"kind": "packet_sent", "id": 3, "change": None},
    {"kind": "place_named", "place": "家", "prev": None},
    {"kind": "alerts_unread", "ids": [4, 5]},
    {"kind": "idea_captured", "n": 2},
    {"kind": "idea_settled", "n": 2, "prev": "open"},
    {"kind": "sources_assigned", "repos": [["garden-app", "jobs"]], "apps": [["Blender", None]],
     "places": [{"name": "家", "lat": 25.0, "lon": 121.5, "radius_m": 150, "domain": None}]},
    {"kind": "place_named", "place": "家", "prev": {"name": "家", "lat": 25.0, "lon": 121.5, "radius_m": 150, "domain": None}},
    {"kind": "plan_edit", "domain": "jobs", "edit": {"op": "deadline_due", "deadline": "x", "due": "2026-11-01"}},
    {"kind": "review", "id": "jobs/goal:apply"},
    {"kind": "review_edit", "id": "jobs/goal:apply", "domain": "jobs", "edit": {"op": "goal_target", "goal": "apply", "target": 4}},
    {"kind": "task_merge", "into": {}, "key": "m1", "refs": ["m2"], "task": {}},
    {"kind": "task_status", "key": "m1", "prev": "open"},
    {"kind": "task_due", "key": "m1", "prev": None},
    {"kind": "status_change", "id": 7},
    {"kind": "calendar_move", "id": "e2", "title": "t", "start": "2026-10-22T10:00:00+08:00", "end": "2026-10-22T11:00:00+08:00"},
    {"kind": "calendar_restore", "id": "e3", "title": "t", "start": "2026-10-22T10:00:00+08:00", "end": "2026-10-22T11:00:00+08:00"},
    {"kind": "hours", "goal": "jobs/apply", "hours": 1.5},
    {"kind": "hours", "goal": "jobs/apply", "hours": 1.5, "date": "2026-10-21"},
    {"kind": "window_label", "key": "Code\tNotes", "prev": None},
    {"kind": "offline", "start": "2026-10-22T10:00:00+08:00"},
    {"kind": "domain_created", "id": "pottery", "source": "idea:7"},
    {"kind": "domain_created", "id": "pottery"},
    {"kind": "domain_archived", "id": "pottery"},
    {"kind": "review_many", "ids": ["pottery/goal:wheel", "pottery/deadline:kiln"]},
    {"kind": "ask_settled", "thread": "t1", "ask": {"text": "等你量長度", "ts": "2026-10-22T10:00:00+08:00"}},
    {"kind": "request_live", "id": 2, "commit": "abc1234"},
]


def test_every_stored_shape_reverses():
    for undo in STORED:
        r = reversal(undo)
        assert r is not None and r.run(Ctx()) is True, undo


def test_every_kind_is_covered():
    assert {u["kind"] for u in STORED} == set(REVERSALS) | set(TASK_KINDS)


def test_what_cannot_be_reversed():
    assert reversal({}) is None  # a draft saved for the user to send: nothing to take back
    assert reversal({"kind": "no-such-kind", "id": 1}) is None
    assert reversal({"kind": "deadline_done"}) is None  # malformed: no id
    for kind in ("calendar_move", "calendar_restore"):
        undo = next(u for u in STORED if u["kind"] == kind)
        assert reversal(undo).run(Ctx(calendar=False)) is False  # no calendar connected: not now


def test_the_move_puts_the_old_block_back_and_removes_the_new_one():
    ctx = Ctx()
    reversal(next(u for u in STORED if u["kind"] == "calendar_move")).run(ctx)
    assert ctx.calls == ["delete_auto_event", "create"]
    ctx = Ctx()
    reversal(next(u for u in STORED if u["kind"] == "calendar_restore")).run(ctx)
    assert ctx.calls == ["create"]


def test_a_failed_undo_does_not_count_against_the_judgment():
    from conftest import at, make_harness
    from muninn.core.models import ActionRecord

    h = make_harness(at(2026, 10, 22, 10))
    rec = h.store.log_action(ActionRecord(None, h.clock.now(), "muninn", "moved", {"kind": "calendar_move", "id": "e", "title": "t",
                                                                                     "start": "2026-10-22T10:00:00+08:00",
                                                                                     "end": "2026-10-22T11:00:00+08:00"}))
    cid = h.store.add_eval_case(h.clock.now(), "route", "s", {}, "{}", "cloud", by_local=False, cloud_ok=True, json_mode=True)
    h.engine.link_case(f"action:{rec.id}", cid)
    assert h.engine.undo_action(rec.id) is False  # no calendar in the harness
    assert h.store.eval_cases()[0]["feedback"] == []


def test_the_rules_table():
    from muninn.core.actions import AskFirst, DecidedBy as D, DoWithUndo, HOLD_INFERRED, Reach as R, default_rules

    assert default_rules(R.INTERNAL, D.USER) == DoWithUndo()
    assert default_rules(R.INTERNAL, D.INFERRED_SURE) == DoWithUndo()
    assert default_rules(R.INTERNAL, D.AGENT) == DoWithUndo()
    assert default_rules(R.OUTWARD_HELD, D.USER) == DoWithUndo()
    assert default_rules(R.OUTWARD_HELD, D.INFERRED_SURE) == DoWithUndo(hold=HOLD_INFERRED)
    for by in D:
        assert default_rules(R.OUTWARD_FINAL, by) == AskFirst()
    for reach in R:
        assert default_rules(reach, D.INFERRED_UNSURE) == AskFirst()


def test_asking_first_shows_what_would_be_done_and_yes_does_it():
    from conftest import at, make_harness
    from muninn.core.actions import DecidedBy, TaskClose

    h = make_harness(at(2026, 10, 22, 10))
    h.engine.messages.receive([{"id": "m1", "project": "garden", "type": "task", "text": "Order soil"}])
    ran = h.engine.gate.run(TaskClose("m1", "done", "maybe the receipt mail", "evidence"), DecidedBy.INFERRED_UNSURE)
    [c] = [x for x in h.engine.pending_confirmations() if x.kind == "action"]
    assert c.id == ran.confirm_id and "Order soil" in c.question and h.engine.tasks.get("m1")["status"] == "open"
    assert c.options == ("做完了,結案", "還沒")  # what is asked, and what each answer does
    h.engine.answer(c.id, "做完了,結案")
    assert h.engine.tasks.get("m1")["status"] == "done"


def test_a_task_redate_goes_through_the_gate_and_undoes():
    from conftest import at, make_harness

    h = make_harness(at(2026, 10, 22, 10))
    h.engine.messages.receive([{"id": "m1", "project": "garden", "type": "task", "text": "Order soil", "due": "2026-11-01"}])
    ran = h.engine.tasks.set_due("m1", "2026-11-08")
    assert h.engine.tasks.get("m1")["due"] == "2026-11-08" and "11-08" in ran.text
    assert h.engine.undo_action(ran.id) and h.engine.tasks.get("m1")["due"] == "2026-11-01"


def test_a_status_undo_that_finds_nothing_says_so():
    class Gone(Ctx):
        def undo_status(self, change_id):
            return False

    assert reversal({"kind": "status_change", "id": 99}).run(Gone()) is False


def test_a_sent_packet_whose_status_cannot_go_back_stays_sent():
    class Gone(Ctx):
        def undo_status(self, change_id):
            return False

    ctx = Gone()
    assert reversal({"kind": "packet_sent", "id": 3, "change": 7}).run(ctx) is False
    assert "set_prepared_status" not in ctx.calls  # not half undone: the packet stays as sent
