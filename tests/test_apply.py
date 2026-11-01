"""Application forms filled from packets: answered by a model, the rest asked in the conversation, filled in a browser,
and sent on the user's one yes; a site that stops hands what is left back to the user. (Sites and pages are fakes.)"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import at, make_harness

from muninn.core.apply import Field, Form, SiteResult, Sites, checked
from muninn.core.models import Prepared

FORM = Form("Greenhouse", "https://jobs.example/acme/apply", (
    Field("first_name", "First Name", "text", True),
    Field("email", "Email", "text", True),
    Field("resume", "Resume/CV", "file", True),
    Field("why", "Why do you want to join?", "textarea", True),
    Field("sponsor", "Will you require visa sponsorship?", "select", True, ("Yes", "No")),
    Field("start", "Earliest start date", "text", False),
))


class FakeSite:
    name = "Greenhouse"

    def __init__(self, sending: SiteResult = SiteResult(True, b"sent", said="Thank you for applying")):
        self.sending = sending
        self.filled: list[dict] = []
        self.sent: list[dict] = []

    def handles(self, url):
        return url.startswith("https://jobs.example/")

    def form(self, url):
        return FORM

    def fill(self, form, values, files):
        self.filled.append({"values": dict(values), "files": dict(files)})
        return SiteResult(True, b"jpeg")

    def submit(self, form, values, files):
        self.sent.append({"values": dict(values), "files": dict(files)})
        return self.sending


def setup(tmp_path: Path, model: dict, site: FakeSite | None = None, url: str = "https://jobs.example/acme"):
    h = make_harness(at(2026, 10, 22, 10))
    site = site or FakeSite()
    resume = tmp_path / "resume-en.pdf"
    resume.write_bytes(b"%PDF-1.1 invented")
    asked = []

    def complete(system, prompt, *, json_mode=False):
        asked.append(json.loads(prompt))
        return json.dumps(model, ensure_ascii=False)

    h.local.complete = h.cloud.complete = complete
    a = h.engine.applications
    a.sites = Sites([site])
    a._filler = __import__("muninn.core.apply", fromlist=["FormFiller"]).FormFiller(h.engine.router)
    a._documents = lambda: {"resume-en.pdf": resume}
    a._texts = lambda: [("resume-en.pdf", "Ada Example · ada@example.com · Platform engineer")]
    a._keep = lambda name, data: "0123456789abcdef"
    p = h.store.add_prepared(Prepared(None, h.clock.now(), "jobs", "acme", "application_packet", "Acme Staff Engineer",
                                      {"posting": {"url": url, "title": "Staff Engineer"},
                                       "packet": {"summary": "Platform work fits", "resume": "resume-en.pdf",
                                                  "answers": [{"q": "Why us?", "a": "The platform team."}]}}))
    return h, site, p, asked


ANSWERED = {"values": {"first_name": "Ada", "email": "ada@example.com", "why": "The platform team."},
            "ask": [{"field": "sponsor", "question": "需要簽證擔保嗎?"}]}


def spoken(h):
    return [x["text"] for t in h.engine.conversation().feed() for x in t["turns"] if x.get("spoken")]


def test_a_form_is_answered_and_what_only_the_user_can_say_is_asked_in_the_conversation(tmp_path):
    h, site, p, asked = setup(tmp_path, ANSWERED)
    assert h.engine.applications.run() == [p.id]
    st = h.engine.applications.state(p.id)
    assert st["state"] == "asking" and st["files"] == {"resume": "resume-en.pdf"}  # the résumé the packet chose, attached
    assert [f["id"] for f in asked[0]["fields"]] == ["first_name", "email", "why", "sponsor", "start"]  # no file field to the model
    assert any("需要簽證擔保嗎?" in s for s in spoken(h)) and site.filled == []
    topic = h.engine.about("packet", str(p.id))
    assert topic["form"]["asking"] == [{"field": "sponsor", "label": "Will you require visa sponsorship?", "question": "需要簽證擔保嗎?",
                                        "options": ["Yes", "No"]}]
    assert h.engine.applications.run() == []  # waits on the user: not read again


def test_answering_the_last_question_fills_the_form_and_asks_once_to_send_it(tmp_path):
    h, site, p, _ = setup(tmp_path, ANSWERED)
    h.engine.applications.run()
    said = h.engine.applications.answer(p.id, "sponsor", "no")  # an option, whatever its case
    assert "都填好了" in said
    assert site.filled[0]["values"]["sponsor"] == "No" and site.filled[0]["files"]["resume"].name == "resume-en.pdf"
    view = h.engine.prepared_view(p.id)["form"]
    assert view["state"] == "filled" and view["shot"] == "0123456789abcdef"
    assert {"id": "sponsor", "label": "Will you require visa sponsorship?", "required": True, "value": "No"} in view["fields"]
    [card] = [c for c in h.engine.pending_confirmations() if c.data.get("action", {}).get("name") == "packet_apply"]
    assert card.options == ("送出", "先不要") and site.sent == []  # nothing goes out before the user's yes
    assert h.store.get_state("apply:earlier") == {"Will you require visa sponsorship?": "No"}  # kept for the next form


def test_the_users_yes_sends_it_and_only_a_page_that_took_it_marks_it_applied(tmp_path):
    h, site, p, _ = setup(tmp_path, {**ANSWERED, "ask": [], "values": {**ANSWERED["values"], "sponsor": "No"}})
    h.engine.applications.run()
    [card] = [c for c in h.engine.pending_confirmations() if c.data.get("action", {}).get("name") == "packet_apply"]
    h.engine.questions().answer(card.id, "送出")
    assert len(site.sent) == 1
    assert h.store.get_prepared(p.id).status == "done" and h.plans.plans["jobs"].tracker_item("acme").status == "applied"
    assert h.engine.prepared_view(p.id)["form"]["said"] == "Thank you for applying"


def test_a_site_that_wants_a_person_hands_it_back_and_send_on_the_page_tries_again(tmp_path):
    site = FakeSite(SiteResult(False, None, blocked="captcha"))
    h, _, p, _ = setup(tmp_path, {**ANSWERED, "ask": [], "values": {**ANSWERED["values"], "sponsor": "No"}}, site)
    h.engine.applications.run()
    said = h.engine.send_application(p.id)  # send pressed on the page: the yes to the question that asks it
    assert "CAPTCHA" in said and h.store.get_prepared(p.id).status == "ready"
    assert h.engine.applications.state(p.id)["state"] == "held" and any("CAPTCHA" in s for s in spoken(h))
    site.sending = SiteResult(True, None, said="Application submitted")
    assert "送出了" in h.engine.send_application(p.id)
    assert h.store.get_prepared(p.id).status == "done" and len(site.sent) == 2


def test_a_form_sent_without_the_site_saying_it_was_received_is_never_sent_again(tmp_path):
    site = FakeSite(SiteResult(False, None, blocked="unreachable", pressed=True))  # pressed, then nothing recognisable
    h, _, p, _ = setup(tmp_path, {**ANSWERED, "ask": [], "values": {**ANSWERED["values"], "sponsor": "No"}}, site)
    h.engine.applications.run()
    said = h.engine.send_application(p.id)
    assert "不知道有沒有送成功" in said and h.engine.applications.state(p.id)["state"] == "unsure"
    assert "不知道有沒有送成功" in h.engine.send_application(p.id)  # pressed again on the page: not sent again
    assert len(site.sent) == 1 and h.store.get_prepared(p.id).status == "ready"
    [t] = [t for t in h.engine.conversation().feed() if any("不知道有沒有送成功" in x["text"] for x in t["turns"])]
    assert [s["label"] for s in t["suggest"]] == ["已經送出了"]
    h.engine.conversation().act(t["thread"], 0)  # the user found the confirmation
    assert h.store.get_prepared(p.id).status == "done" and len(site.sent) == 1


def test_a_page_that_never_got_to_its_send_button_can_be_sent_again(tmp_path):
    site = FakeSite(SiteResult(False, None, blocked="unreachable"))  # failed before the press
    h, _, p, _ = setup(tmp_path, {**ANSWERED, "ask": [], "values": {**ANSWERED["values"], "sponsor": "No"}}, site)
    h.engine.applications.run()
    h.engine.send_application(p.id)
    assert h.engine.applications.state(p.id)["state"] == "held"
    site.sending = SiteResult(True, None, said="Application submitted")
    assert "送出了" in h.engine.send_application(p.id) and len(site.sent) == 2


def test_a_posting_no_site_fills_is_said_so_and_no_model_is_asked(tmp_path):
    h, site, p, asked = setup(tmp_path, ANSWERED, url="https://careers.example/job/1")
    h.engine.applications.run()
    assert h.engine.prepared_view(p.id)["form"] == {"state": "unsupported"} and asked == []
    assert h.engine.send_application(p.id) is None


def test_the_conversation_answers_a_field_through_its_tool(tmp_path):
    h, site, p, _ = setup(tmp_path, ANSWERED)
    h.engine.applications.run()
    h.engine.applications.sites = Sites([site])
    tools = h.engine.commands().tools
    assert "answer_form" in tools.names
    h.local.complete = lambda system, prompt, *, json_mode=False: json.dumps(
        {"actions": [{"tool": "answer_form", "args": {"packet": p.id, "field": "sponsor", "value": "No"}}], "reply": ""})
    r = h.engine.command("不需要擔保")
    assert "都填好了" in r.outcomes[0].text and site.filled


def test_a_models_answers_are_kept_to_the_form():
    values, ask = checked(FORM, {"first_name": " Ada ", "sponsor": "maybe", "resume": "x.pdf", "nope": "1", "start": {"a": 1}},
                          [{"field": "why", "question": "Why?"}, {"field": "resume", "question": "Which file?"}])
    assert values == {"first_name": "Ada"}  # an option it does not have, a file, an unknown field and a non-text are dropped
    assert ask == [{"field": "why", "question": "Why?"}, {"field": "email", "question": "Email"},
                   {"field": "sponsor", "question": "Will you require visa sponsorship?"}]  # every required one is answered or asked


def test_a_resume_kept_as_a_web_page_is_typed_in_unless_it_comes_as_a_pdf(tmp_path):
    typed_form = Form(FORM.site, FORM.url, (*FORM.fields, Field("resume_text", "Resume/CV (typed in)", "textarea", False)))
    site = FakeSite()
    site.form = lambda url: typed_form
    h, _, p, _ = setup(tmp_path, {**ANSWERED, "ask": [], "values": {**ANSWERED["values"], "sponsor": "No"}}, site)
    page = tmp_path / "resume-en.html"
    page.write_text("<h1>Ada Example</h1>")
    a = h.engine.applications
    a._documents = lambda: {"resume-en.pdf": page}  # only the web page: the form takes it typed in
    a.run()
    assert site.filled[-1]["files"] == {} and site.filled[-1]["values"]["resume_text"].startswith("Ada Example")
    printed = tmp_path / "resume-en-printed.pdf"
    printed.write_bytes(b"%PDF-1.4 invented")
    a._documents = lambda: {"resume-en.pdf": printed}  # printed to PDF: attached, and no longer typed in
    a.answer(p.id, "sponsor", "Yes")
    assert site.filled[-1]["files"] == {"resume": printed} and "resume_text" not in site.filled[-1]["values"]


def test_a_posting_taken_down_is_said_once_with_a_button_that_sets_the_packet_aside(tmp_path):
    from muninn.core.apply import PostingGone

    site = FakeSite()

    def gone(url):
        raise PostingGone(url)

    site.form = gone
    h, _, p, asked = setup(tmp_path, ANSWERED, site)
    h.engine.applications.run()
    assert h.engine.applications.state(p.id) == {"state": "gone", "site": "Greenhouse"} and asked == []
    [t] = [t for t in h.engine.conversation().feed() if any("已經關了" in x["text"] for x in t["turns"])]
    assert t["about"] == {"kind": "packet", "id": str(p.id)} and [s["label"] for s in t["suggest"]] == ["收掉"]
    assert h.engine.applications.run() == []  # said once: not read again
    h.engine.conversation().act(t["thread"], 0)
    assert h.store.get_prepared(p.id).status == "dismissed"
    assert h.engine.prepared_view(p.id)["form"] == {"state": "gone", "site": "Greenhouse"}
