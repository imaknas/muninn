"""Public job postings. Each fetcher reads the hosts it knows and returns None
for the rest; ChainFetcher tries them in order."""

from __future__ import annotations

import html
import logging
import re
from html.parser import HTMLParser
from typing import Sequence
from urllib.parse import parse_qs, urlsplit

import httpx

from muninn.core.models import Posting
from muninn.core.ports import PostingFetcher

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"}


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "tr", "section", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("• ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    p = _Text()
    p.feed(markup)
    text = "".join(p.parts)
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


class GreenhouseFetcher:
    """Greenhouse boards (job-boards.greenhouse.io/<board>/jobs/<id>, the EU
    host, and company pages embedding a board via ?gh_jid=<id>) through the
    public boards API."""

    def __init__(self, client: httpx.Client):
        self._http = client

    @staticmethod
    def locate(url: str) -> tuple[str, str, str] | None:
        """(api host, board, job id) for a URL, if it is a Greenhouse job."""
        u = urlsplit(url)
        host = u.hostname or ""
        m = re.match(r"^/([^/]+)/jobs/(\d+)", u.path)
        if host.endswith("greenhouse.io") and m:
            api = "boards-api.eu.greenhouse.io" if ".eu." in host else "boards-api.greenhouse.io"
            return api, m.group(1), m.group(2)
        jid = parse_qs(u.query).get("gh_jid")
        if jid and host:
            # company careers page embedding its board; the board token is
            # conventionally the company's domain name
            parts = host.split(".")
            board = parts[-2] if len(parts) >= 2 else host
            return "boards-api.greenhouse.io", board, jid[0]
        return None

    def fetch(self, url: str) -> Posting | None:
        loc = self.locate(url)
        if loc is None:
            return None
        api, board, job = loc
        try:
            r = self._http.get(f"https://{api}/v1/boards/{board}/jobs/{job}")
            if r.status_code != 200:
                return None
            d = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.info("greenhouse fetch failed for %s: %s", url, e)
            return None
        return Posting(
            url=url,
            title=str(d.get("title") or ""),
            location=str((d.get("location") or {}).get("name") or ""),
            text=html_to_text(html.unescape(d.get("content") or "")),
        )


class HtmlPageFetcher:
    """Any server-rendered page: its visible text."""

    def __init__(self, client: httpx.Client, min_chars: int = 400):
        self._http = client
        self._min = min_chars

    def fetch(self, url: str) -> Posting | None:
        try:
            r = self._http.get(url, follow_redirects=True)
            if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
                return None
        except httpx.HTTPError as e:
            log.info("page fetch failed for %s: %s", url, e)
            return None
        m = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S | re.I)
        text = html_to_text(r.text)
        if len(text) < self._min:
            return None  # probably rendered by JavaScript; nothing to work with
        return Posting(url=url, title=html.unescape(m.group(1).strip()) if m else "", location="", text=text)


class ChainFetcher:
    def __init__(self, fetchers: Sequence[PostingFetcher]):
        self._fetchers = list(fetchers)

    def fetch(self, url: str) -> Posting | None:
        for f in self._fetchers:
            posting = f.fetch(url)
            if posting is not None and posting.text:
                return posting
        return None


def default_fetcher(timeout: float = 20.0) -> ChainFetcher:
    client = httpx.Client(timeout=timeout, headers=UA)
    return ChainFetcher([GreenhouseFetcher(client), HtmlPageFetcher(client)])
