"""Application forms on Greenhouse (job-boards.greenhouse.io, its EU twin, and company career pages that embed it with
?gh_jid=): the fields come from Greenhouse's public job board API, and the form is filled in its embedded page in a
browser (browser.py)."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlparse

from muninn.core.apply import Field, Form, PostingGone, SiteResult

from .browser import Page

log = logging.getLogger(__name__)

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}?questions=true"
BOARD = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
KINDS = {"input_text": "text", "textarea": "textarea", "input_file": "file",
         "multi_value_single_select": "select", "multi_value_multi_select": "multiselect"}
# the page asks these beside the API's questions: the phone number's country, and where the applicant is
COUNTRY = Field("country", "Country (of the phone number)", "text", True, note="the country's name in English, e.g. Taiwan")
LOCATION_ID = "candidate-location"
# what the page shows after it took the application, and what it shows when a person has to step in
RECEIVED = re.compile(r"thank you for (applying|your application)|application (was )?(submitted|received)", re.I)
CODE_ASKED = re.compile(r"(security|verification) code", re.I)


def job_ref(url: str) -> tuple[str, str, bool] | None:
    """(board, job id, on the EU host) for a Greenhouse posting's address; None for any other."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    q = parse_qs(u.query)
    eu = ".eu.greenhouse.io" in host
    if host.endswith("greenhouse.io"):
        if (q.get("for") and q.get("token")):
            return q["for"][0], q["token"][0], eu
        m = re.match(r"^/([\w-]+)/jobs/(\d+)", u.path)
        return (m.group(1), m.group(2), eu) if m else None
    if q.get("gh_jid") and q["gh_jid"][0].isdigit():  # a company's own career page with Greenhouse inside
        labels = [x for x in host.split(".") if x not in ("www", "careers", "jobs")]
        return (labels[-2] if len(labels) >= 2 else labels[0], q["gh_jid"][0], False) if labels else None
    return None


def embed_url(board: str, job: str, eu: bool) -> str:
    """The form alone, as company pages embed it: the same page for every board."""
    return f"https://job-boards{'.eu' if eu else ''}.greenhouse.io/embed/job_app?for={board}&token={job}"


def form_of(job: Mapping[str, Any], url: str, site: str = "Greenhouse") -> Form:
    """The form a job's API record describes (questions, where the applicant is, demographic and compliance questions)."""
    fields: list[Field] = []
    for q in job.get("questions") or []:
        kinds = [f for f in q.get("fields") or [] if f.get("type") in KINDS]
        has_file = any(f["type"] == "input_file" for f in kinds)
        for f in kinds:
            kind = KINDS[f["type"]]
            typed = has_file and kind == "textarea"  # the résumé typed in instead of attached
            fields.append(Field(f["name"], f"{q['label']} (typed in)" if typed else q["label"], kind,
                                bool(q.get("required")) and not typed, tuple(v["label"] for v in f.get("values") or []),
                                _plain(q.get("description"))))
            if f["name"] == "email":
                fields.append(COUNTRY)
    for q in job.get("location_questions") or []:
        if any(f.get("name") == "location" for f in q.get("fields") or []):
            fields.append(Field(LOCATION_ID, q.get("label") or "Location", "text", bool(q.get("required")),
                                note="the city the applicant lives in, e.g. Taipei"))
    for q in (job.get("demographic_questions") or {}).get("questions") or []:
        fields.append(Field(str(q["id"]), q["label"], KINDS.get(q.get("type"), "select"), bool(q.get("required")),
                            tuple(o["label"] for o in q.get("answer_options") or []), "voluntary self-identification"))
    for part in job.get("compliance") or []:
        for q in part.get("questions") or []:
            for f in q.get("fields") or []:
                if f.get("type") in KINDS:
                    fields.append(Field(f["name"], q["label"], KINDS[f["type"]], bool(q.get("required")),
                                        tuple(v["label"] for v in f.get("values") or []), "voluntary self-identification"))
    if not any(f.id == COUNTRY.id for f in fields):
        fields.insert(0, COUNTRY)
    return Form(site, url, tuple(fields))


def _plain(html: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()[:300]


class Greenhouse:
    name = "Greenhouse"

    def __init__(self, get_json: Callable[[str], tuple[int, Any]], browse: Callable[[Callable[[Page], SiteResult]], SiteResult]):
        """`get_json(url)`: the HTTP status and the API record (0 and None when it could not be reached); `browse(work)`:
        runs work on a fresh page."""
        self._get = get_json
        self._browse = browse

    def handles(self, url: str) -> bool:
        return job_ref(url) is not None

    def form(self, url: str) -> Form | None:
        ref = job_ref(url)
        if ref is None:
            return None
        board, job, eu = ref
        status, record = self._get(API.format(board=board, job=job))
        if status == 404 and self._get(BOARD.format(board=board))[0] == 200:
            raise PostingGone(url)  # the company's board is there and this job is not: taken down (a wrong board is not)
        if not isinstance(record, dict) or not record.get("questions"):
            return None
        return form_of(record, embed_url(board, job, eu), self.name)

    def fill(self, form: Form, values: Mapping[str, Any], files: Mapping[str, Path]) -> SiteResult:
        def work(page: Page) -> SiteResult:
            refused = fill_in(page, form, values, files)
            return SiteResult(not refused, page.shot(), blocked="invalid" if refused else "", refused=tuple(refused))

        return self._browse(work)

    def submit(self, form: Form, values: Mapping[str, Any], files: Mapping[str, Path]) -> SiteResult:
        pressed = False

        def work(page: Page) -> SiteResult:
            nonlocal pressed
            refused = fill_in(page, form, values, files)
            if refused:
                return SiteResult(False, page.shot(), blocked="invalid", refused=tuple(refused))
            pressed = True  # before the click: one that fails part-way may still have sent it
            page.click_submit()
            return after_sending(page, form)

        r = self._browse(work)
        return replace(r, pressed=True) if pressed else r


def fill_in(page: Page, form: Form, values: Mapping[str, Any], files: Mapping[str, Path]) -> list[str]:
    """Fills every field that has a value or a file; the ids of those the page did not take."""
    page.open(form.url)
    refused = []
    for f in form.fields:
        if (f.id in values or f.id in files) and not page.has(f.id):
            refused.append(f.id)  # the page has no such field (any more)
            continue
        try:
            if f.kind == "file":
                if f.id in files:
                    page.attach(f.id, files[f.id])
                continue
            if f.id not in values:
                continue
            v = values[f.id]
            if f.kind in ("select", "multiselect"):
                for choice in v if isinstance(v, list) else [v]:
                    page.choose(f.id, str(choice))
            elif page.is_picker(f.id):  # where the applicant is, the phone's country: typed, then the first match
                page.pick_first(f.id, str(v))
            else:
                page.type_in(f.id, str(v))
        except Exception as e:  # noqa: BLE001 — a field the page would not take is said, not fatal
            log.info("greenhouse: %s not filled: %s", f.id, e)
            refused.append(f.id)
    return refused


def after_sending(page: Page, form: Form, seconds: float = 30) -> SiteResult:
    """What the page did once sent: took it (it says so), wants a person (a CAPTCHA, a code from their mail), or
    refused fields (marked invalid)."""
    for _ in range(int(seconds * 2)):
        text = page.text()
        if RECEIVED.search(text):
            return SiteResult(True, page.shot(), said=page.heading() or RECEIVED.search(text).group(0))
        if page.captcha():
            return SiteResult(False, page.shot(), blocked="captcha")
        if CODE_ASKED.search(text):
            return SiteResult(False, page.shot(), blocked="code")
        if bad := [f.id for f in form.fields if page.invalid(f.id)]:
            return SiteResult(False, page.shot(), blocked="invalid", refused=tuple(bad))
        page.wait(0.5)
    return SiteResult(False, page.shot(), blocked="unreachable")
