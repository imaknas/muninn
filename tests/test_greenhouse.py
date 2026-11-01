"""Greenhouse postings: which addresses are theirs, and the form a job's API record describes (an invented record)."""

from __future__ import annotations

from muninn.adapters.apply.greenhouse import COUNTRY, LOCATION_ID, Greenhouse, embed_url, form_of, job_ref

RECORD = {
    "questions": [
        {"label": "First Name", "required": True, "fields": [{"name": "first_name", "type": "input_text", "values": []}]},
        {"label": "Email", "required": True, "fields": [{"name": "email", "type": "input_text", "values": []}]},
        {"label": "Resume/CV", "required": True, "fields": [{"name": "resume", "type": "input_file", "values": []},
                                                             {"name": "resume_text", "type": "textarea", "values": []}]},
        {"label": "Will you require sponsorship?", "required": True, "description": "<p>For <b>this</b> role</p>",
         "fields": [{"name": "question_11", "type": "multi_value_single_select", "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]}]},
        {"label": "Guidelines", "required": True,
         "fields": [{"name": "question_12[]", "type": "multi_value_multi_select", "values": [{"label": "Acknowledge", "value": 5}]}]},
    ],
    "location_questions": [{"label": "Location", "required": True, "fields": [{"name": "latitude", "type": "input_hidden"},
                                                                              {"name": "location", "type": "input_text"}]}],
    "demographic_questions": {"questions": [{"id": 130, "label": "Gender", "required": False, "type": "multi_value_single_select",
                                             "answer_options": [{"label": "Female"}, {"label": "I don't wish to answer"}]}]},
}


def test_greenhouse_addresses_and_the_page_their_form_is_filled_on():
    assert job_ref("https://job-boards.greenhouse.io/example/jobs/123") == ("example", "123", False)
    assert job_ref("https://job-boards.eu.greenhouse.io/example/jobs/456?gh_src=x") == ("example", "456", True)
    assert job_ref("https://www.example.com/careers/open-positions/?gh_jid=789") == ("example", "789", False)  # a company's own page
    assert job_ref("https://job-boards.greenhouse.io/embed/job_app?for=example&token=1") == ("example", "1", False)
    assert job_ref("https://jobs.example.com/en/jobs/1/staff") is None
    assert embed_url("example", "456", True) == "https://job-boards.eu.greenhouse.io/embed/job_app?for=example&token=456"


def test_the_form_a_record_describes():
    form = form_of(RECORD, "https://job-boards.greenhouse.io/embed/job_app?for=example&token=1")
    assert [(f.id, f.kind, f.required) for f in form.fields] == [
        ("first_name", "text", True), ("email", "text", True), (COUNTRY.id, "text", True),  # the phone's country, which the API leaves out
        ("resume", "file", True), ("resume_text", "textarea", False),  # typed in instead: not required beside the file
        ("question_11", "select", True), ("question_12[]", "multiselect", True), (LOCATION_ID, "text", True), ("130", "select", False)]
    sponsor = form.field("question_11")
    assert sponsor.options == ("Yes", "No") and sponsor.note == "For this role"
    assert form.field("130").options == ("Female", "I don't wish to answer")


def test_a_job_no_longer_on_its_board_is_gone_and_a_board_that_is_not_there_is_only_unread():
    import pytest

    from muninn.core.apply import PostingGone

    asked = []

    def board_is_there(url):
        asked.append(url)
        return (200, {"jobs": []}) if url.endswith("/jobs") else (404, None)

    with pytest.raises(PostingGone):
        Greenhouse(board_is_there, lambda work: None).form("https://www.example.com/careers/?gh_jid=789")
    assert asked == ["https://boards-api.greenhouse.io/v1/boards/example/jobs/789?questions=true",
                     "https://boards-api.greenhouse.io/v1/boards/example/jobs"]
    site = Greenhouse(lambda url: (404, None), lambda work: None)  # the board guessed from the address is not one
    assert site.form("https://www.example.com/careers/?gh_jid=789") is None
    assert Greenhouse(lambda url: (0, None), lambda work: None).form("https://job-boards.greenhouse.io/example/jobs/1") is None
    assert not site.handles("https://jobs.example.com/1")


def test_a_send_that_fails_once_its_button_was_pressed_says_it_may_have_gone():
    from muninn.core.apply import Field, Form, SiteResult

    form = Form("Greenhouse", "https://job-boards.greenhouse.io/embed/job_app?for=example&token=1",
                (Field("first_name", "First Name", "text", True),))

    class Page:
        def __init__(self, fails_at: str):
            self.fails_at = fails_at

        def open(self, url):
            if self.fails_at == "open":
                raise TimeoutError("page did not load")

        def has(self, fid):
            return True

        def is_picker(self, fid):
            return False

        def type_in(self, fid, value):
            pass

        def shot(self):
            return None

        def click_submit(self):
            raise TimeoutError("no answer after the click")

    def browse(page):  # as the real one: what fails inside is "unreachable"
        def run(work):
            try:
                return work(page)
            except Exception:  # noqa: BLE001
                return SiteResult(False, blocked="unreachable")
        return run

    pressed = Greenhouse(lambda url: (0, None), browse(Page("click"))).submit(form, {"first_name": "Ada"}, {})
    assert (pressed.ok, pressed.blocked, pressed.pressed) == (False, "unreachable", True)
    unopened = Greenhouse(lambda url: (0, None), browse(Page("open"))).submit(form, {"first_name": "Ada"}, {})
    assert (unopened.blocked, unopened.pressed) == ("unreachable", False)
