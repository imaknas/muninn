"""The settings page says what this installation does without, and why: on the phone, in each language."""

from __future__ import annotations

import pytest

from e2e.screen import go
from e2e_support import Muninn, _page, _stop

pytestmark = pytest.mark.e2e

OFF = [{"feature": "行事曆", "why": "沒有設定 Google client(secrets/google_client.json)"},
       {"feature": "每週回顧", "why": "設定關掉了(reflect: false)"}]


@pytest.fixture
def without_some(tmp_path, browser):
    m = Muninn(tmp_path, settings={"off": OFF})
    yield m
    _stop(m, browser)


def test_what_is_not_set_up_is_listed_with_why(browser, without_some):
    ctx, page, errors = _page(browser, without_some, **browser[0].devices["iPhone 14"])
    go(page, without_some, "settings", "沒開的功能")
    part = page.locator("section[aria-label='沒開的功能']")
    assert "2 項" in part.inner_text()
    for o in OFF:
        row = part.locator(".rule", has_text=o["feature"])
        assert o["why"] in row.inner_text()
    ctx.close()
    assert errors == [], errors


def test_nothing_is_listed_when_everything_is_set_up(page, muninn):
    go(page, muninn, "settings", "設定")
    assert page.locator("section[aria-label='沒開的功能']").count() == 0
