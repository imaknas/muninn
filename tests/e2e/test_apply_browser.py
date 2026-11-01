"""Filling and sending an application form in a real (headless Chromium) browser, on an invented page drawn the way
Greenhouse draws its forms (tests/fixtures/apply_form.html): what was filled is what the page got, and a page that
wants a person, or refuses a field, is handed back as such."""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

from muninn.adapters.apply.browser import browse
from muninn.adapters.apply.greenhouse import Greenhouse
from muninn.core.apply import Field, Form

pytestmark = pytest.mark.e2e
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIELDS = (Field("first_name", "First Name", "text", True), Field("country", "Country", "text", True),
          Field("candidate-location", "Location", "text", True), Field("question_1", "Sponsorship?", "select", True, ("Yes", "No")),
          Field("question_2[]", "Guidelines", "multiselect", True, ("Acknowledge/Confirm",)),
          Field("resume", "Resume", "file", True), Field("question_3", "Why?", "textarea", True))
VALUES = {"first_name": "Ada", "country": "Taiwan", "candidate-location": "Taipei", "question_1": "No",
          "question_2[]": ["Acknowledge/Confirm"], "question_3": "The platform team."}


@pytest.fixture(scope="module")
def site():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/apply_form.html"
    server.shutdown()


def apart(work):
    """Runs in a thread of its own: the browser tests' own Playwright holds this one."""
    out = []
    t = threading.Thread(target=lambda: out.append(work()))
    t.start()
    t.join(120)
    return out[0]


def greenhouse():
    return Greenhouse(lambda url: None, browse)


@pytest.fixture
def resume(tmp_path):
    f = tmp_path / "resume.pdf"
    f.write_bytes(b"%PDF-1.1 invented")
    return f


def test_a_form_is_filled_without_sending_and_then_sent_with_what_was_filled(site, resume):
    form = Form("Greenhouse", site, FIELDS)
    filled = apart(lambda: greenhouse().fill(form, VALUES, {"resume": resume}))
    assert filled.ok and filled.refused == () and filled.shot[:2] == b"\xff\xd8"  # a JPEG picture of it
    sent = apart(lambda: greenhouse().submit(form, VALUES, {"resume": resume}))
    assert sent.ok and sent.said == "Thank you for applying."


def test_what_the_page_got_is_what_was_filled(site, resume):
    from muninn.adapters.apply.greenhouse import fill_in
    from muninn.core.apply import SiteResult

    def work(page):
        assert fill_in(page, Form("Greenhouse", site, FIELDS), VALUES, {"resume": resume}) == []
        page.click_submit()
        page.wait(0.5)
        return SiteResult(True, said=page.text())

    got = apart(lambda: browse(work)).said
    assert "Ada;country=Taiwan +886;candidate-location=Taipei, Taipei City, Taiwan;question_1=No;files=1;checked=true" in got


@pytest.mark.parametrize("then,blocked,refused", [("captcha", "captcha", ()), ("code", "code", ()), ("invalid", "invalid", ("question_3",))])
def test_a_page_that_wants_a_person_or_refuses_a_field_is_handed_back(site, resume, then, blocked, refused):
    form = Form("Greenhouse", f"{site}?then={then}", FIELDS)
    r = apart(lambda: greenhouse().submit(form, VALUES, {"resume": resume}))
    assert not r.ok and r.blocked == blocked and r.refused == refused


def test_a_field_the_page_does_not_have_is_said_not_sent(site, resume):
    form = Form("Greenhouse", site, (*FIELDS, Field("question_9", "Missing", "text", True)))
    r = apart(lambda: greenhouse().submit(form, {**VALUES, "question_9": "x"}, {"resume": resume}))
    assert not r.ok and r.refused == ("question_9",)


def test_a_resume_kept_as_a_web_page_is_printed_to_pdf_once_until_it_changes(tmp_path):
    import os

    from muninn.adapters.apply.browser import as_pdf

    page = tmp_path / "resume.html"
    page.write_text("<html><body><h1>Ada Example</h1><p>Platform engineer</p></body></html>")
    pdf = apart(lambda: as_pdf(page, tmp_path / "out"))
    assert pdf.read_bytes()[:5] == b"%PDF-"
    made = pdf.stat().st_mtime
    assert apart(lambda: as_pdf(page, tmp_path / "out")).stat().st_mtime == made  # unchanged: not printed again
    os.utime(page, (made + 10, made + 10))
    assert apart(lambda: as_pdf(page, tmp_path / "out")).stat().st_mtime > made
