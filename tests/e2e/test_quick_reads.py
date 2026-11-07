"""However many times a screen is told to read again, it does not ask the server for more than one round at a time."""

from __future__ import annotations

import pytest

from e2e.screen import go, seed

pytestmark = pytest.mark.e2e


def until(page, holds, seconds=15):
    for _ in range(seconds * 20):
        if holds():
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"never happened: {holds.__doc__ or ''}")


def test_coming_back_many_times_while_a_read_is_on_its_way_asks_once_more_not_each_time(page, muninn):
    seed(muninn)
    go(page, muninn)
    held, asked = [], []

    def hold_the_first(route):
        asked.append(1)
        if len(asked) == 1:
            held.append(route)
        else:
            route.continue_()

    page.route("**/api/home", hold_the_first)
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    until(page, lambda: len(asked) >= 1)
    for _ in range(5):
        page.evaluate("window.dispatchEvent(new Event('focus'))")
    page.wait_for_timeout(300)
    assert len(asked) == 1, asked  # the first round is still on its way
    held[0].continue_()
    until(page, lambda: len(asked) >= 2)  # one more, for all that was asked meanwhile
    page.wait_for_timeout(300)
    assert len(asked) <= 3, asked  # (and one for a change the server told of along the way), not one for each
