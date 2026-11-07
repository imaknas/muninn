"""However many times a screen is told to read again, it does not ask the server for more than one round at a time."""

from __future__ import annotations

import pytest

from e2e.screen import go, seed

pytestmark = pytest.mark.e2e


def test_coming_back_many_times_while_a_read_is_on_its_way_asks_once_more_not_each_time(page, muninn):
    seed(muninn)
    go(page, muninn)
    held, asked = [], []

    def hold(route):
        asked.append(1)
        held.append(route)

    page.route("**/api/home", hold)
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    page.wait_for_timeout(300)
    assert len(asked) == 1
    for _ in range(5):
        page.evaluate("window.dispatchEvent(new Event('focus'))")
    page.wait_for_timeout(300)
    assert len(asked) == 1  # the first round is still on its way
    page.unroute("**/api/home")
    page.route("**/api/home", lambda route: (asked.append(1), route.continue_()))
    held[0].continue_()
    page.wait_for_timeout(800)
    assert len(asked) == 2  # one more, for all that was asked meanwhile
