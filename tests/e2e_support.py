"""Browser end-to-end tests: the real app, served over HTTPS from a thread,
driven by WebKit as an iPhone. Models, clock and data are the unit tests'
fakes, so every run is the same. Service workers are blocked so the tests
always run the shipped static files, and any page error or console error
fails the test.

Run: uv run pytest -m e2e (once per machine: uv run playwright install webkit; and npm ci). The app's
TypeScript is compiled once per run, so the browser always gets what the source says.
The fixtures reach the test modules through tests/e2e/conftest.py.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from conftest import TZ, at, make_harness  # noqa: E402
from world import Calendar, ListedSetup  # noqa: E402

from muninn.adapters.channels.webpush import VapidKeys  # noqa: E402
from muninn.adapters.uploads import FileUploads  # noqa: E402
from muninn.adapters.web.app import COOKIE, create_app  # noqa: E402
from muninn.config import Config  # noqa: E402
from muninn.main import IMPLS, LOCATION_APPS  # noqa: E402
from muninn.adapters.web.devices import DeviceRegistry  # noqa: E402
from muninn.core.day_view import DayView  # noqa: E402
from muninn.core.lease import ChannelLease, PermissionDesk  # noqa: E402
from test_lease import FakeRunner  # noqa: E402

playwright = pytest.importorskip("playwright.sync_api")


def _cert(directory: Path) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256()))
    keyfile, certfile = directory / "key.pem", directory / "cert.pem"
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(keyfile), str(certfile)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


SETUP_TOKEN = "e2e-setup-token"


class Muninn:
    """A running app: `h` is the harness (fake models, clock, store), `url` its address.
    Deep-talk is set up with a fake session runner (`runner`), as on the real server. `settings`: what the settings
    page is told of this installation (main.settings_view), where a test needs some."""

    def __init__(self, tmp: Path, plans=None, lang: str = "zh-TW", settings: dict | None = None, https: bool = True):
        """`https=False`: as a fresh install serves itself, plain http on this computer and no HTTPS address yet."""
        # plan items left unconfirmed, so the review cards show; `plans=[]` is a new user with no domain yet
        self.h = make_harness(at(2026, 10, 22, 10), plans=plans, confirm_all=False, lang=lang)
        keys = VapidKeys(tmp / "secrets" / "vapid.pem")
        keys.ensure()
        self.devices = DeviceRegistry(tmp / "dev.db")
        e = self.h.engine
        e.store.set_state("talk:last_seen", self.h.clock.now().isoformat())  # here a moment ago: no opening unless a test leaves
        self.runner = FakeRunner()
        e.leases = ChannelLease(e.store, self.h.clock, self.runner, self.h.channel, lambda now: False, lambda tid: e.conversation().thread(tid), lambda tid, change: e.conversation().update_thread(tid, change), words=e.words)
        self.desk = e.permissions = PermissionDesk(e.store, self.h.clock, self.h.channel, lambda now: False, e.leases, words=e.words)
        self.calendar = Calendar()
        day = DayView(e.store, self.h.clock, e.tz, e.plans.load, e.metrics, e.stays_between, self.calendar, words=e.words)
        self.uploads = FileUploads(tmp / "uploads", words=e.words)
        port = _free_port()
        self.url = f"{'https' if https else 'http'}://127.0.0.1:{port}"
        self.setup = ListedSetup(Config.load(tmp), IMPLS, restart=lambda: False, token=SETUP_TOKEN)
        app = create_app(e, keys, "tok", self.devices, messages=e.messages, lease_runner=self.runner, permissions=self.desk,
                         day_view=day, uploads=self.uploads, location_apps=LOCATION_APPS, setup=self.setup,
                         public_url=self.url if https else None,
                         settings_view=settings)
        self.app = app
        keyfile, certfile = _cert(tmp) if https else (None, None)
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                                    ssl_keyfile=keyfile, ssl_certfile=certfile))
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)

    def pair(self) -> str:
        r = httpx.post(f"{self.url}/api/pair", json={"code": self.devices.new_pairing_code(), "name": "e2e"}, verify=False)
        return r.cookies[COOKIE]

    def replies(self, *texts) -> None:
        """What the (local) model answers next, in order."""
        queue = [t if isinstance(t, str) else json.dumps(t, ensure_ascii=False) for t in texts]

        def complete(system, prompt, *, json_mode=False):
            self.h.local.calls.append((system, prompt))
            return queue.pop(0) if queue else '{"actions": [], "reply": "好"}'

        self.h.local.complete = complete

    def relay_asks(self, lease_id: str, n: int, question: str, *options: str) -> None:
        """What a lent session's relay posts when the session asks the user a multiple-choice question."""
        req = {"v": 2, "id": f"{lease_id}:tu{n}", "lease": lease_id, "project": "garden", "kind": "ask_user", "summary": question,
               "question": {"questions": [{"question": question, "options": [{"label": o} for o in options]}]}}
        r = httpx.post(f"{self.url}/api/permission-requests", json=req, headers={"Authorization": "Bearer tok"}, verify=False)
        assert r.status_code == 202, r.text

    def relay_asks_v2(self, lease_id: str, n: int, question: str, *options: str, multi: bool = False) -> None:
        """The same, as the relay asks in the contract's second version: by kind, with a summary."""
        req = {"v": 2, "id": f"{lease_id}:q{n}", "lease": lease_id, "project": "garden", "kind": "ask_user", "summary": question,
               "question": {"questions": [{"question": question, "multi": multi, "options": [{"label": o} for o in options]}]}}
        r = httpx.post(f"{self.url}/api/permission-requests", json=req, headers={"Authorization": "Bearer tok"}, verify=False)
        assert r.status_code == 202, r.text

    def relay_wants(self, lease_id: str, n: int, project: str, tool: str, risk: str, what: str) -> None:
        """What a session working in a sandbox asks before writing a file or running a command."""
        req = {"v": 2, "id": f"{lease_id}:w{n}", "lease": lease_id, "project": project, "kind": risk, "summary": f"{tool} {what}",
               "target": what}
        r = httpx.post(f"{self.url}/api/permission-requests", json=req, headers={"Authorization": "Bearer tok"}, verify=False)
        assert r.status_code == 202, r.text

    def session_streams(self, lease_id: str, *events: dict) -> None:
        """What the runner streams of a lent session's turn, as it comes, to the views open on it (once one is open:
        what streams before a view opens is not kept for it)."""
        end = time.monotonic() + 10
        while not self.app.state.lease_feed.watching(lease_id):
            assert time.monotonic() < end, f"no view open on lease {lease_id}"
            time.sleep(0.05)
        for ev in events:
            self.app.state.lease_feed.publish(lease_id, ev)

    def stop(self) -> None:
        self.server.should_exit = True


def _stop(m: Muninn, browser) -> None:
    """Pages close before the server they talk to stops, whichever fixture a test named first: a page still
    open on a stopped server reports every request it was making as an error."""
    for ctx in browser[1].contexts:
        ctx.close()
    m.stop()


@pytest.fixture
def muninn(tmp_path, browser):
    m = Muninn(tmp_path)
    yield m
    _stop(m, browser)


@pytest.fixture
def newcomer(tmp_path, browser):
    """A new user: Muninn with no domain at all."""
    m = Muninn(tmp_path, plans=[])
    yield m
    _stop(m, browser)


ROOT = Path(__file__).resolve().parents[1]


def build_app() -> None:
    npm = shutil.which("npm")
    if npm is None:
        pytest.fail("npm not found: the app is TypeScript and must be compiled (install Node, then npm ci)")
    if not (ROOT / "node_modules" / "typescript").exists():  # a fresh checkout: the compiler first
        r = subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=ROOT, capture_output=True, text=True)
        if r.returncode:
            pytest.fail(f"npm ci failed:\n{r.stdout}{r.stderr}")
    r = subprocess.run([npm, "run", "--silent", "build"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        pytest.fail(f"the app's TypeScript does not compile:\n{r.stdout}{r.stderr}")


def build_once(tmp_path_factory) -> None:
    """The app compiled once a run: with the tests spread over workers (pytest -n), the first worker compiles it
    and the others wait, so no worker reads a script while another is writing it."""
    if not os.environ.get("PYTEST_XDIST_WORKER"):
        build_app()
        return
    shared = tmp_path_factory.getbasetemp().parent  # this run's, the same for every worker
    with open(shared / "build.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (shared / "build.done").exists():
            build_app()
            (shared / "build.done").touch()


@pytest.fixture(scope="session")
def browser(tmp_path_factory):
    build_once(tmp_path_factory)
    with playwright.sync_playwright() as p:
        b = p.webkit.launch()
        yield p, b
        b.close()


EXPECTED: dict[int, list[str]] = {}  # by page: console errors a test says it causes on purpose


def expect_console(page, text: str) -> None:
    """The test causes this console error on purpose (e.g. a 404 it is checking is said well): not a failure."""
    EXPECTED.setdefault(id(page), []).append(text)


def _page(browser, muninn, **context):
    """A paired page on the app; fails the test on any page or console error it did not say it causes."""
    p, b = browser
    # the user's time zone, as the app's: the screen says times as the user's phone would, whatever the machine's is
    ctx = b.new_context(**{"timezone_id": TZ, **context}, ignore_https_errors=True, service_workers="block")
    ctx.add_cookies([{"name": COOKIE, "value": muninn.pair(), "url": muninn.url}])
    pg = ctx.new_page()
    pg.clock.set_system_time(muninn.h.clock.now())  # the screen's today is the app's today, as on a real device
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(f"page error: {e}"))
    pg.on("console", lambda m: errors.append(f"console {m.type}: {m.text}")
          if m.type == "error" and not any(x in m.text for x in EXPECTED.get(id(pg), [])) else None)
    return ctx, pg, errors


@pytest.fixture
def page(browser, muninn):
    """An iPhone."""
    ctx, pg, errors = _page(browser, muninn, **browser[0].devices["iPhone 14"])
    yield pg
    ctx.close()
    assert errors == [], errors


@pytest.fixture
def desktop(browser, muninn):
    """A laptop browser window; `desktop.set_viewport_size` for other widths."""
    ctx, pg, errors = _page(browser, muninn, viewport={"width": 1440, "height": 900})
    yield pg
    ctx.close()
    assert errors == [], errors
