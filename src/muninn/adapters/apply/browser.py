"""A browser page that application forms are filled in: headless Chromium through Playwright, a fresh profile each time
(nothing kept between forms). Playwright is an optional part of Muninn (the `autofill` extra, with its Chromium):
without it no site fills forms, and the packet says to apply by hand."""

from __future__ import annotations

import importlib.util
import logging
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from muninn.core.apply import SiteResult

log = logging.getLogger(__name__)


def available() -> bool:
    """Whether Playwright is installed (its browser may still be missing: that shows when a form is first filled)."""
    return importlib.util.find_spec("playwright") is not None


def installed() -> bool:
    """Whether the browser itself is there."""
    def look() -> bool:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()

    return bool(_apart(look, 30))


def _apart(work: Callable[[], Any], seconds: float = 60) -> Any:
    """Runs `work` in a thread of its own (Playwright's sync API will not start where an event loop runs, or inside
    another of its own); None when it failed or took too long."""
    import threading

    out: list[Any] = []

    def run() -> None:
        try:
            out.append(work())
        except Exception as e:  # noqa: BLE001
            log.info("browser: %s", e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    return out[0] if out else None


def as_pdf(html: Path, out_dir: Path) -> Path | None:
    """A résumé kept as a web page, printed to PDF (what application forms take), again only when the page changed."""
    pdf = out_dir / f"{html.stem}.pdf"
    if pdf.exists() and pdf.stat().st_mtime >= html.stat().st_mtime:
        return pdf

    def work() -> Path:
        from playwright.sync_api import sync_playwright

        out_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(html.resolve().as_uri(), wait_until="networkidle")
                page.emulate_media(media="print")
                page.pdf(path=str(pdf), format="A4", print_background=True)
            finally:
                browser.close()
        return pdf

    return _apart(work)


class PdfDocuments:
    """The user's documents as application forms get them: one kept as a web page is handed over as its PDF (same
    name in the list, so the packet's choice still finds it); the text is read from the original."""

    def __init__(self, documents: Any, out_dir: Path):
        self._documents = documents
        self._out = out_dir

    def files(self) -> dict[str, Path]:
        out = {}
        for name, path in self._documents.files().items():
            out[name] = (as_pdf(path, self._out) or path) if path.suffix.lower() in (".html", ".htm") else path
        return out

    def documents(self) -> list[tuple[str, str]]:
        return self._documents.documents()


class Page(Protocol):
    """What filling in a form needs of a page; fields are found by their element id."""

    def open(self, url: str) -> None: ...

    def has(self, fid: str) -> bool: ...

    def type_in(self, fid: str, text: str) -> None: ...

    def choose(self, fid: str, option: str) -> None:
        """Picks one option of a select (a dropdown, or a group of checkboxes) by its exact label."""
        ...

    def is_picker(self, fid: str) -> bool:
        """Whether the field is typed into and then picked from what it suggests."""
        ...

    def pick_first(self, fid: str, text: str) -> None: ...

    def attach(self, fid: str, path: Path) -> None: ...

    def click_submit(self) -> None: ...

    def text(self) -> str: ...

    def heading(self) -> str: ...

    def captcha(self) -> bool:
        """Whether a check meant for a person is showing."""
        ...

    def invalid(self, fid: str) -> bool: ...

    def wait(self, seconds: float) -> None: ...

    def shot(self) -> bytes | None: ...


class PlaywrightPage:
    def __init__(self, page: Any):
        self._page = page

    def _el(self, fid: str) -> Any:
        return self._page.locator(f'[id="{fid}"]')

    def open(self, url: str) -> None:
        self._page.goto(url, wait_until="networkidle")

    def has(self, fid: str) -> bool:
        return self._el(fid).count() > 0

    def type_in(self, fid: str, text: str) -> None:
        self._el(fid).fill(text)

    def choose(self, fid: str, option: str) -> None:
        el = self._el(fid)
        tag = el.evaluate("e => e.tagName")
        if tag == "FIELDSET":
            el.get_by_label(option, exact=True).check()
            return
        if tag == "SELECT":
            el.select_option(label=option)
            return
        el.click()  # a dropdown drawn by the page: open it, narrow it down when the option is not in view, pick it
        choice = self._page.get_by_role("option", name=option, exact=True)
        if not choice.count():
            el.press_sequentially(option[:25], delay=20)
        choice.first.click()

    def is_picker(self, fid: str) -> bool:
        return self._el(fid).get_attribute("role") == "combobox"

    def pick_first(self, fid: str, text: str) -> None:
        el = self._el(fid)
        el.click()
        el.press_sequentially(text, delay=40)
        first = self._page.get_by_role("option").first
        first.wait_for(timeout=8000)
        first.click()

    def attach(self, fid: str, path: Path) -> None:
        self._el(fid).set_input_files(str(path))

    def click_submit(self) -> None:
        self._page.locator("button[type=submit]").first.click()

    def text(self) -> str:
        return self._page.locator("body").inner_text()

    def heading(self) -> str:
        h = self._page.locator("h1, h2").first
        return h.inner_text().strip() if h.count() else ""

    def captcha(self) -> bool:
        return self._page.locator('iframe[src*="bframe"], iframe[title*="challenge" i], iframe[src*="hcaptcha"][src*="challenge"]').filter(visible=True).count() > 0

    def invalid(self, fid: str) -> bool:
        el = self._el(fid)
        return el.count() > 0 and el.first.get_attribute("aria-invalid") == "true"

    def wait(self, seconds: float) -> None:
        self._page.wait_for_timeout(seconds * 1000)

    def shot(self) -> bytes | None:
        try:
            return self._page.screenshot(full_page=True, type="jpeg", quality=55)
        except Exception as e:  # noqa: BLE001 — a missing picture does not stop the form
            log.info("no picture of the page: %s", e)
            return None


def browse(work: Callable[[Page], SiteResult], *, timeout_ms: int = 20_000) -> SiteResult:
    """Runs `work` on a fresh page of a headless browser, closed after; a page that fails to load or a browser that
    is missing is UNREACHABLE (logged)."""
    from playwright.sync_api import sync_playwright

    started = time.monotonic()
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            log.warning("no browser to fill forms in (playwright install chromium?): %s", e)
            return SiteResult(False, blocked="unreachable")
        try:
            context = browser.new_context(locale="en-US", viewport={"width": 1100, "height": 900})
            context.set_default_timeout(timeout_ms)
            return work(PlaywrightPage(context.new_page()))
        except Exception as e:  # noqa: BLE001
            log.warning("filling a form failed after %.0fs: %s", time.monotonic() - started, e)
            return SiteResult(False, blocked="unreachable")
        finally:
            browser.close()
