"""Muninn set to English: every place on screen is in English, with nothing in Chinese left over, on the phone
and the wide screen. The data is invented and in English too, so any Chinese on screen is Muninn's own."""

from __future__ import annotations

import re

import pytest

from e2e.screen import fits_the_window, no_stray_text
from e2e_support import Muninn, _page, _stop

pytestmark = pytest.mark.e2e
HAN = re.compile(r"[　-〿一-鿿＀-￯]")


@pytest.fixture
def english(tmp_path, browser):
    m = Muninn(tmp_path, plans=[], lang="en")
    e = m.h.engine
    e.messages.receive([{"id": "m1", "project": "garden", "type": "task", "text": "Order soil for the rose bed", "due": "2026-10-30"},
                        {"id": "q1", "project": "garden", "type": "question", "text": "Red or white roses?"}])
    e.ideas.add("A watering schedule for the community garden")
    yield m
    _stop(m, browser)


def no_chinese(pg, where: str) -> None:
    text = pg.inner_text("body")
    found = sorted({line.strip() for line in text.splitlines() if HAN.search(line)} - {"繁體中文"})  # each language names itself
    assert found == [], (where, found)
    for attr in ("aria-label", "placeholder", "title", "alt"):
        values = pg.eval_on_selector_all(f"[{attr}]", f"els => els.map(e => e.getAttribute('{attr}'))")
        assert [v for v in values if HAN.search(v or "")] == [], (where, attr)


@pytest.mark.parametrize("context", [{"device": "iPhone 14"}, {"viewport": {"width": 1440, "height": 900}}])
def test_every_place_is_in_english(browser, english, context):
    if "device" in context:
        context = browser[0].devices[context["device"]]
    ctx, pg, errors = _page(browser, english, **context)
    try:
        assert pg.goto(f"{english.url}/", wait_until="domcontentloaded").ok and pg.evaluate("document.documentElement.lang") == "en"
        pg.get_by_role("textbox", name="Talk to Muninn").wait_for()
        for where in ("", "things", "today", "progress", "lists", "settings"):
            pg.goto(f"{english.url}/#{where}", wait_until="domcontentloaded")
            pg.get_by_role("textbox", name="Talk to Muninn").wait_for()
            pg.wait_for_timeout(500)  # the place's own reads
            no_chinese(pg, where)
            no_stray_text(pg)
            assert fits_the_window(pg), where
    finally:
        ctx.close()
    assert errors == [], errors
