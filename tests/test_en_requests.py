"""In English, a request going live, and one rolled back, is said in English where it was asked
(core/requests.py). Invented requests only."""

from __future__ import annotations

import re

from muninn.core.requests import KEY

from conftest import at, make_harness


class FakeVersion:
    unknown = None

    def __init__(self, *commits: str, lines: list[str] | None = None):
        self.commits, self.lines = set(commits), list(lines or [])

    def contains(self, commit):
        return commit in self.commits

    def deploy_lines(self):
        return list(self.lines)


def run(fn):
    return fn()


def said(h, tid="t1"):
    return [t for t in (h.engine.conversation().thread(tid) or {}).get("turns") or [] if t["role"] == "muninn"]


def test_live_and_rolled_back_are_said_in_english():
    h = make_harness(at(2026, 10, 25, 10), lang="en")
    h.store.save_thread("t1", {"about": None, "ts": "2026-10-25T09:00", "turns": []})
    h.engine.commands().execute({"actions": [{"tool": "request_feature", "args": {"text": "A weekly watering summary"}}],
                                 "reply": "", "thread": "t1"})
    reqs = h.store.get_state(KEY)
    reqs[0]["commit"] = "c0ffee1"
    h.store.set_state(KEY, reqs)
    version = FakeVersion("c0ffee1")
    h.engine.requests.version = version
    assert h.engine.check_live(run) == 1
    live = said(h)[-1]
    assert live["text"] == 'Live now: "A weekly watering summary"'
    [done] = [a for a in h.engine.undoable_actions() if a.text.startswith("Marked live")]
    assert done.text == "Marked live: A weekly watering summary (going by commit c0ffee1)"
    version.commits = {"aaa0001"}
    version.lines.append("2026-10-25 11:00 rollback c0ffee1 -> aaa0001: the home page did not load")
    assert h.engine.check_live(run) == 1
    back = said(h)[-1]["text"]
    assert back == ("The check after going live failed (the home page did not load), so Muninn went back to the previous "
                    'version: "A weekly watering summary" isn\'t live yet.')
    assert re.search(r"[一-鿿]", live["text"] + done.text + back) is None
