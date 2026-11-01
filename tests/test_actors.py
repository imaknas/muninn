"""Actors and the topic router: one actor per kind of topic, and the engine's topics come from the actors."""

from __future__ import annotations

import pytest

from muninn.core.actors import TopicRouter

from conftest import at, make_harness


class Topics:
    def __init__(self, *kinds):
        self.kinds = kinds

    def topic(self, kind, ref):
        return {"kind": kind, "ref": ref}


def test_a_topic_goes_to_the_actor_that_answers_for_its_kind():
    router = TopicRouter([Topics("task"), Topics("review", "question")])
    assert router.topic("question", "7") == {"kind": "question", "ref": "7"}
    assert router.topic("packet", "1") is None and router.kinds == {"task", "review", "question"}


def test_two_actors_cannot_answer_for_the_same_kind():
    with pytest.raises(ValueError, match="task"):
        TopicRouter([Topics("task"), Topics("task")])


def test_the_engine_routes_every_topic_kind_it_had_before():
    h = make_harness(at(2026, 10, 22, 10))
    assert {"review", "task", "question", "project_question", "packet"} <= h.engine.topic_router().kinds


def test_a_tick_runs_the_actors_jobs_by_phase_and_survives_a_failing_one():
    from muninn.core.actors import Job

    h = make_harness(at(2026, 10, 22, 10))
    ran = []

    def boom():
        ran.append("boom")
        raise RuntimeError("down")

    h.engine.engine.jobs = lambda: [Job("after", lambda: ran.append("after"), "after"), Job("boom", boom),
                                    Job("late", lambda: ran.append("late"), "late"), Job("early", lambda: ran.append("early"))]
    h.engine.tick()
    assert ran == ["boom", "early", "late", "after"]


def test_the_front_publishes_the_jobs_the_engine_used_to_run():
    h = make_harness(at(2026, 10, 22, 10))
    assert [(j.name, j.phase) for j in h.engine.jobs()] == [
        ("reading the projects' messages", "early"), ("sorting or closing tasks", "early"), ("matching to-dos", "early"), ("asking about offline time", "early"),
        ("window classification", "early"), ("mail triage", "late"), ("preparing packets", "after"),
        ("delivering handoffs", "after"), ("setting aside quiet topics", "after"), ("keeping each topic's state", "after"),
        ("speaking up when the user pauses", "after")]


def test_a_project_side_that_keeps_its_own_inbox_and_outbox_is_read_and_written_on_each_tick():
    """The external side posts and pulls (nothing to do here); a side Muninn reads itself is polled."""
    class Inbox:
        def __init__(self):
            self.waiting = [{"id": "garden-q1", "project": "garden", "type": "question", "text": "Red or white roses?"}]

        def collect(self):
            out, self.waiting = self.waiting, []
            return out

    class Outbox:
        def __init__(self):
            self.got = []

        def deliver(self, handoffs):
            self.got += handoffs
            return [h["id"] for h in handoffs]

    h = make_harness(at(2026, 10, 22, 10))
    h.engine.project_inbox, h.engine.handoff_outbox = Inbox(), Outbox()
    hid = h.engine.handoff_log.add(project="garden", item="Order soil", fact="soil ordered", effect="resolves")["id"]
    h.engine.tick()
    assert [q["text"] for q in h.engine.messages.open_questions()] == ["Red or white roses?"]
    assert [x["id"] for x in h.engine.handoff_outbox.got] == [hid]
    assert h.engine.handoff_log.pending() == []  # delivered


def test_the_external_side_leaves_handoffs_pending_for_the_projects_to_fetch():
    h = make_harness(at(2026, 10, 22, 10))
    hid = h.engine.handoff_log.add(project="garden", item="Order soil", fact="soil ordered", effect="resolves")["id"]
    h.engine.tick()
    assert [x["id"] for x in h.engine.handoff_log.pending()] == [hid]
