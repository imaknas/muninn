"""What this design looks like: a light screen; the conversation in one column with its input under it; on a
wide screen the panel floats under the button at the top right and the conversation does not move; on a phone the panel
comes up from below and ends above the input, which stays. These check this design and are replaced with
the next one's own; what the user does is in test_flows.py and stays."""

from __future__ import annotations

import pytest

from e2e.screen import go, open_panel, panel, say_box, seed, talk

pytestmark = pytest.mark.e2e


def box(locator):
    b = locator.bounding_box()
    assert b, locator
    return b


def test_the_screen_is_light_and_the_conversation_one_column_over_its_input(desktop, muninn):
    seed(muninn)
    go(desktop, muninn)
    assert desktop.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(247, 247, 245)"
    log, say = box(desktop.locator("#log")), box(say_box(desktop))
    assert log["width"] <= 721 and abs((log["x"] + log["width"] / 2) - desktop.viewport_size["width"] / 2) < 2  # centred
    assert say["y"] > log["y"] and say["y"] + say["height"] <= desktop.viewport_size["height"]  # the input at the bottom, on screen


def test_on_a_wide_screen_the_panel_floats_at_the_top_right_and_nothing_moves(desktop, muninn):
    seed(muninn)
    go(desktop, muninn)
    before, say_before = box(desktop.locator("#log")), box(say_box(desktop))
    open_panel(desktop)
    p, c, say = box(panel(desktop)), box(desktop.locator("#log")), box(say_box(desktop))
    assert (c["x"], c["width"], say["x"], say["width"]) == (before["x"], before["width"], say_before["x"], say_before["width"])
    right = desktop.viewport_size["width"] - (p["x"] + p["width"])
    assert 8 <= right <= 24 and p["y"] >= 40 and p["y"] + p["height"] <= say["y"] - 4  # floating, ending above the input
    assert desktop.evaluate("getComputedStyle(document.querySelector('#panel')).borderRadius") == "20px"
    assert talk(desktop).is_visible()


def test_on_a_phone_the_panel_comes_up_from_below_and_ends_above_the_input(page, muninn):
    seed(muninn)
    go(page, muninn)
    open_panel(page)
    p, say = box(panel(page)), box(say_box(page))
    assert p["y"] > 0 and p["y"] + p["height"] <= say["y"] + 1  # above the input, which is still there
    assert say["y"] + say["height"] <= page.viewport_size["height"]
