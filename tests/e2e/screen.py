"""How the browser tests find things on screen: only by what a person (or a screen reader) is told,
never by layout or CSS classes, so the flows keep working through a redesign. These names are the
pages' contract with the tests (docs/design/ui-contract.md lists them); a redesign keeps them.

The screen is the conversation, its input, one button at the top saying how many things wait, and
the panel it opens. Places in the panel have addresses (#things, #today, #progress, #lists, #settings,
#item/<id>, #packet/<id> …), which links in cards and pushes use as well."""

from __future__ import annotations

import re

SAY = "跟 Muninn 說"  # the one input that talks to Muninn
TALK = "跟 Muninn 的對話"  # where the conversation shows
PANEL = "今天的事與細節"  # what the button at the top opens
STRAY = ("null", "undefined", "NaN", "[object Object]")


FLYING: dict[int, set] = {}  # by page: the app's reads and writes still on their way


def _flying(page) -> set:
    if id(page) not in FLYING:
        FLYING[id(page)] = flying = set()
        page.on("request", lambda r: flying.add(r) if "/api/" in r.url and "/events" not in r.url else None)
        page.on("requestfinished", lambda r: flying.discard(r))
        page.on("requestfailed", lambda r: flying.discard(r))
    return FLYING[id(page)]


def go(page, m, where: str = "", shown: str | None = None):
    """Opens an address (the conversation alone when empty) and waits until `shown` is on screen, and until the page
    has settled: a page that loads reads everything once more when its stream of changes opens, and a change the
    test made just before would come a second later; meanwhile what the test presses could be drawn over."""
    m.app.state.screens.check()  # the server has caught up with what the test wrote: no "changed" comes later for it
    flying = _flying(page)
    url = f"{m.url}/#{where}"
    loads = page.url.split("#")[0] != url.split("#")[0]  # not only another address in the same page
    if loads:
        with page.expect_response(lambda r: "/api/changes/events" in r.url):
            page.goto(url)
    else:
        page.goto(url)
    if shown:
        page.get_by_text(shown).filter(visible=True).first.wait_for()
    else:
        say_box(page).wait_for()
    quiet = 0
    for _ in range(100):  # nothing on its way for a moment: the reads the stream's opening started are done
        page.wait_for_timeout(50)
        quiet = quiet + 1 if not flying else 0
        if quiet >= 3:
            break
    return page


def say_box(page):
    return page.get_by_role("textbox", name=SAY)


def say(page, text: str) -> None:
    box = say_box(page)
    box.fill(text)
    box.press("Enter")


def talk(page):
    """The conversation."""
    return page.get_by_label(TALK)


def pill(page):
    """The button at the top: how many things wait, and today's count; it opens the panel."""
    return page.get_by_role("button", name=re.compile(r"今天 \d+ 件"))


def panel(page):
    return page.get_by_role("complementary", name=PANEL)


def open_panel(page):
    if pill(page).get_attribute("aria-expanded") != "true":
        pill(page).click()
    panel(page).wait_for()
    page.wait_for_function("document.getAnimations().every((a) => a.animationName !== 'panel-in' || a.playState !== 'running')")  # settled
    return panel(page)


def waiting(page, text: str):
    """A thing in the panel's list of what waits on the user, by what it says."""
    return panel(page).get_by_role("region", name="等你").get_by_role("link").filter(has_text=text)


def place(page, name: str):
    """Opens one of the panel's places (今天, 進度, 清單, 設定)."""
    open_panel(page).get_by_role("navigation", name="看更多").get_by_role("link", name=re.compile("^" + name)).click()


def entry(page, name: str):
    """A task or an idea in 清單, named by its title: pressing it talks about it."""
    return panel(page).get_by_role("button", name=name, exact=True)


def button(scope, name: str, exact: bool = False):
    return scope.get_by_role("button", name=re.compile("^" + re.escape(name)) if not exact else name, exact=exact)


def answer_with(m, *actions: tuple[str, dict], reply: str = "") -> None:
    """What the model answers next: these tools, and a sentence."""
    m.replies({"actions": [{"tool": t, "args": a} for t, a in actions], "reply": reply})


def take_back(page, m, what: str = "") -> None:
    """Says 撤回: taking something back is said, not pressed."""
    answer_with(m, ("undo_action", {"what": what}))
    say(page, f"撤回{what}")
    talk(page).get_by_text("撤回了:").last.wait_for()


def eventually(check, seconds: float = 5.0) -> None:
    """Waits until `check()` holds (what the server did, rather than any text that might match too early)."""
    import time

    end = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < end, "never happened"
        time.sleep(0.05)


def no_stray_text(page) -> None:
    text = page.inner_text("body")
    for s in STRAY:
        assert s not in text, s


def fits_the_window(page) -> bool:
    return page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")


# ── invented data ──────────────────────────────────────────────────────────

def seed(m) -> None:
    """Something waiting of every kind."""
    e = m.h.engine
    e.messages.receive([{"id": "m1", "project": "garden", "type": "task", "text": "Order soil for the rose bed", "due": "2026-10-30"},
                        {"id": "q1", "project": "garden", "type": "question", "text": "Red or white roses?"}])
    from muninn.core.actions import DecidedBy, TrackerStatus

    e.gate.run(TrackerStatus("jobs", "acme", "interview", "Acme 寄來面試邀請"), DecidedBy.INFERRED_UNSURE)
    e.ideas.add("幫社區花園做一個澆水排程的小工具")


def chores(m, n: int) -> None:
    for i in range(n):
        m.h.engine.messages.receive([{"id": f"t{i}", "project": "garden", "type": "task", "text": f"Garden chore {i}"}])


def packet(m):
    """An application packet, ready to send."""
    from muninn.core.models import Prepared

    return m.h.store.add_prepared(Prepared(None, m.h.clock.now(), "jobs", "acme", "application_packet", "Acme Staff Engineer",
                                           {"packet": {"summary": "Go and platform work fit", "match": ["Platform"], "gaps": ["Go depth"],
                                                       "answers": [{"q": "Why us?", "a": "The platform team."}], "ask_first": ["Team size?"]}},
                                           prepared_by="claude-cli:sonnet"))
