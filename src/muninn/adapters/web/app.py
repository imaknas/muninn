"""HTTP side of the PWA channel: the app shell, its JSON API, push
subscription, and the machine-to-machine ingest endpoint.

Binds to 127.0.0.1 only; `tailscale serve` exposes it to the tailnet. Being on
the tailnet is not enough: every /api call needs a paired-device cookie
(see devices.py), and optionally a Tailscale login from an allowlist. POSTs
must be JSON and the cookie is SameSite=Strict, so a cross-site page cannot
drive the API; no CORS headers are ever sent. Ingest uses a bearer token.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import html
import json
from datetime import timedelta
from urllib.parse import quote
import logging
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from muninn.adapters.channels.webpush import VapidKeys
from muninn.package import version
from muninn.adapters.sources.health import parse_health
from muninn.adapters.web.devices import DeviceRegistry
from muninn.adapters.web.lease_feed import LeaseFeed
from muninn.adapters.web.screen_watch import ScreenWatch
from muninn.core.day_view import DayView
from muninn.core.location import observation as location_observation
from muninn.core.actions import DecidedBy
from muninn.core.assembly import Muninn
from muninn.collectors import COLLECTORS
from muninn.core import activity, ph
from muninn.core.models import Attachment, InboxItem
from muninn.core.conversation import FEED
from muninn.core.lease import PermissionDesk, talk_lines
from muninn.core.ports import CloudLoginLost, LocationApp, PushMessage, RunnerError, SessionRunner, Uploads
from muninn.setup_flow import Setup, qr_svg
from muninn.core.project_messages import VERSION as MESSAGES_VERSION, ProjectMessages
from muninn.core.sidecar import Sidecar, aside
from muninn.core.timewin import local_date

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
VERSION = version()
SCRIPTS = ("app.js", "sw.js")  # what the TypeScript build makes (npm run build): without them the page is empty


def unbuilt() -> list[str]:
    """The web app's built scripts that are missing (none: it was built, as every released package is)."""
    return [name for name in SCRIPTS if not (STATIC / name).is_file()]
COOKIE = "muninn_device"
MAX_FILES = 8  # sent with one sentence
SHOWN_FILES_KEPT = timedelta(days=30)  # how long the runner keeps a file a deep talk showed the user
COOKIE_MAX_AGE = 400 * 24 * 3600
LIVE_CHECK_S = 15 * 60  # how often the server looks whether what runs now includes a request's commit


def _item(i: InboxItem) -> dict[str, Any]:
    return {
        "id": i.id, "ts": i.ts.isoformat(), "domain": i.domain, "kind": i.kind, "title": i.title,
        "body": i.body, "data": i.data, "level": i.level, "read": i.read,
    }


class RevalidatedStatic(StaticFiles):
    """Static files the browser must check with the server before reusing,
    so a deploy reaches the home-screen app on its next open."""

    async def get_response(self, path: str, scope) -> Response:
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def create_app(
    engine: Muninn,
    keys: VapidKeys,
    ingest_token: str,
    devices: DeviceRegistry,
    allowed_logins: Sequence[str] = (),
    settings_view: dict[str, Any] | None = None,
    document_files: Callable[[], dict[str, Path]] = dict,
    connections: dict[str, Any] | None = None,
    public_url: str | None = None,
    messages: ProjectMessages | None = None,
    lease_runner: SessionRunner | None = None,
    permissions: PermissionDesk | None = None,
    smoke_checks: Sequence[tuple[str, Callable[[], str]]] = (),
    day_view: DayView | None = None,
    uploads: Uploads | None = None,
    location_apps: Sequence[LocationApp] = (),
    setup: Setup | None = None,
) -> Starlette:
    """`settings_view`: read-only facts about notification rules for the PWA.
    `messages`: what the projects post for the user; without it the
    project-message routes are left out. `lease_runner` and `permissions`:
    lending a thread to a project's session (engine.leases) and the requests
    its relay puts to the user; without them those routes are left out.
    `uploads`: the files the user sends with what they say (photos, PDFs); without it nothing can be sent.
    `day_view`: today as a timeline and goals day by day (the wide screen);
    without it those routes answer empty. `setup`: first-run setup (which model, its key, pairing the phone); open
    to the one-time token printed at start until a device is paired, then to paired devices only."""
    settings_view = settings_view or {}
    words = engine.words  # what the server says itself (errors, the pages a sign-in returns to)
    lock = threading.RLock()  # engine calls touch YAML files; serialize them (re-entrant: a lease's thread
    # write can happen inside an event the feed is already applying under it)
    allowed = {a.lower() for a in allowed_logins}

    def tailnet_ok(request: Request) -> bool:
        """With an allowlist, requests must come through `tailscale serve`
        from one of these logins (serve sets the header; clients cannot)."""
        if not allowed:
            return True
        return request.headers.get("tailscale-user-login", "").lower() in allowed

    def authed(handler):
        async def wrapper(request: Request) -> Response:
            if not tailnet_ok(request):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            device = await run_in_threadpool(devices.check, request.cookies.get(COOKIE))
            if device is None:
                return JSONResponse({"error": "not paired"}, status_code=401)
            request.state.device = device
            return await handler(request)

        return wrapper

    async def call(fn, *args, **kwargs):
        def run():
            with lock:
                return fn(*args, **kwargs)

        return await run_in_threadpool(run)

    async def body(request: Request) -> dict[str, Any]:
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise ValueError("content-type must be application/json")
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    def guarded(handler):
        async def wrapper(request: Request) -> Response:
            try:
                return await handler(request)
            except (ValueError, KeyError, TypeError) as e:
                log.warning("%s %s refused: %s", request.method, request.url.path, str(e)[:300])  # what the screen was told
                return JSONResponse({"error": str(e)}, status_code=400)

        return wrapper

    # the page in the language Muninn speaks: the script reads its words from <html lang>
    page = (STATIC / "index.html").read_text(encoding="utf-8").replace('<html lang="zh-TW">', f'<html lang="{engine.words.code}">', 1)

    async def index(request: Request) -> Response:
        if unbuilt():  # installed from source without its build: a page that says so, not an empty one
            text = html.escape(words("web.not_built"))
            return HTMLResponse(f'<!doctype html><html lang="{words.code}"><meta charset="utf-8"><title>Muninn</title>'
                                f'<body style="font:16px/1.5 system-ui;max-width:36em;margin:3em auto;padding:0 1em"><p>{text}</p>',
                                status_code=503, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(page, headers={"Cache-Control": "no-cache"})

    async def sw(request: Request) -> Response:
        if unbuilt():
            return Response(status_code=404)
        return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    async def manifest(request: Request) -> Response:
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    privacy_page = STATIC / ("privacy.en.html" if words.code == "en" else "privacy.html")

    async def privacy(request: Request) -> Response:
        return FileResponse(privacy_page, headers={"Cache-Control": "no-cache"})

    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True, "version": VERSION})  # the version answering (what an upgrade waits for)

    @authed
    async def today(request: Request) -> Response:
        return JSONResponse(await call(engine.today_view))

    @authed
    async def inbox(request: Request) -> Response:
        unread = request.query_params.get("unread") == "1"
        items = await call(engine.store.inbox, limit=100, unread_only=unread)
        return JSONResponse({"items": [_item(i) for i in items]})

    @authed
    @guarded
    async def mark_read(request: Request) -> Response:
        data = await body(request)
        ids = [int(i) for i in data.get("ids", [])]
        r = await call(engine.alerts.seen, ids, str(data.get("title") or ""))  # the same action as saying so
        return JSONResponse({"ok": True, "text": r.text, "action_id": r.id} if r else {"ok": True})

    @authed
    @guarded
    async def settle_idea(request: Request) -> Response:
        """An idea set aside, dropped or taken back up from a button: the same action as saying so."""
        data = await body(request)
        r = await call(engine.ideas.settle, request.path_params["n"], str(data.get("decision") or ""), str(data.get("why") or ""))
        return JSONResponse({"ok": True, "text": r.text, "action_id": r.id} if r else {"ok": True})

    @authed
    async def confirmations(request: Request) -> Response:
        cs = await call(engine.pending_confirmations)
        return JSONResponse(
            {"items": [{"id": c.id, "ts": c.ts.isoformat(), "kind": c.kind, "question": c.question, "options": list(c.options), "data": c.data,
                        "talk": await call(engine.conversation().suggestions_for, "question", c.id)} for c in cs]}
        )

    @authed
    @guarded
    async def answer(request: Request) -> Response:
        data = await body(request)
        c = await call(engine.answer, int(request.path_params["cid"]), str(data["answer"]))
        return JSONResponse({"ok": c is not None}, status_code=200 if c else 404)

    connections = connections or {}

    async def oauth_callback(request: Request) -> Response:
        """The provider redirects here after consent. Checked by the
        single-use state (the consent browser has no device cookie), plus the
        tailnet allowlist."""
        client = connections.get(request.path_params["provider"])
        if not tailnet_ok(request) or client is None:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        q = request.query_params
        if q.get("error"):
            detail = q.get("error_description") or q["error"]
            return HTMLResponse(f"<meta name='viewport' content='width=device-width'><p style='font:16px system-ui;padding:24px'>{html.escape(words('web.connect_refused', why=detail))}</p>", status_code=400)
        try:
            await run_in_threadpool(client.complete, q.get("code", ""), q.get("state", ""))
        except Exception as e:
            return HTMLResponse(f"<p>{html.escape(words('web.connect_failed', why=e))}</p>", status_code=400)
        return HTMLResponse(f"<meta name='viewport' content='width=device-width'><p style='font:18px system-ui;padding:24px'>{html.escape(words('web.connected'))}</p>")

    @authed
    async def connections_status(request: Request) -> Response:
        return JSONResponse({name: {"configured": c.configured, "authorized": c.authorized, "status": c.status()}
                             for name, c in connections.items()})

    @authed
    @guarded
    async def connect(request: Request) -> Response:
        await body(request)
        client = connections.get(request.path_params["provider"])
        if client is None or not client.configured:
            return JSONResponse({"error": "not set up"}, status_code=409)
        return JSONResponse({"url": await run_in_threadpool(client.authorization_url)})

    @authed
    @guarded
    async def command(request: Request) -> Response:
        """One turn of a conversation: a new one, or `thread` to go on, with
        `about` ({kind, id}: an item the user is discussing; the actors' topic kinds) when it discusses one."""
        data = await body(request)
        about = data.get("about") if isinstance(data.get("about"), dict) else None
        if about is not None:
            about = {"kind": str(about.get("kind")), "id": str(about.get("id"))}
        thread = str(data["thread"]) if data.get("thread") else None
        text = str(data.get("text", ""))[:1000]
        try:
            files = sent_files(data.get("attachments"))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        # before any understanding: slash commands are handled mechanically, and a
        # thread lent to a project's session goes to that session, never to a model here
        if text.strip().startswith("/"):
            return JSONResponse(await lease_command(thread, text.strip()))
        # which conversation it goes on with: the one on screen, another still going, or a new one (a model judges;
        # not when the user just opened one, or names an item, themselves). One lent to a project's session (its talk
        # going on) is judged once for both where it goes and what in it is for Muninn; any other is Muninn's, and
        # going into a talk again is Muninn's to do, as going into one first is.
        leases = engine.leases

        async def going_on(tid: str | None) -> dict[str, Any] | None:  # the talk a conversation is lent to, still going
            lease = await run_in_threadpool(leases.lease_for, tid) if tid and leases is not None else None
            return lease if lease is not None and lease.get("state") != "detaching" else None

        here = await going_on(thread)
        def said_to(lease: dict[str, Any]) -> list[str]:  # that talk as it went last, to tell what his words refer to
            return talk_lines(engine.store.lease_turns(lease["lease_id"]), leases.label(lease["project"]))

        choice = await run_in_threadpool(engine.topics().choose, thread, text, pinned=bool(data.get("pinned")) or about is not None,
                                         lent_to=leases.label(here["project"]) if here else None, lent_talk=said_to(here) if here else [])
        part = choice.for_muninn if here and not choice.switched and sidecar else None  # what in it is Muninn's
        if choice.switched:
            thread, about = choice.thread, choice.about
            there = await going_on(thread) if sidecar else None
            if there is not None:  # into another deep talk: what in it is Muninn's is judged as said to that one
                again = await run_in_threadpool(engine.topics().choose, thread, text, pinned=True, lent_to=leases.label(there["project"]),
                                                lent_talk=said_to(there))
                part = again.for_muninn
        topic = {"title": choice.title, "switched": choice.switched}
        lent = await going_on(thread)
        if lent is not None:
            sent = await run_in_threadpool(leases.send, thread, text, str(data.get("client_msg_id") or ""), files,
                                           aside(part, words) if part else "")
            if choice.end and not choice.switched:  # he is done with it: it wraps up, then comes back to Muninn
                await call(leases.end_when_done, thread)
            # what in it was for Muninn is done beside the talk, after the answer has gone: the user never waits on it
            beside = BackgroundTask(sidecar.act, lent["lease_id"], thread, part) if sidecar and part else None
            return JSONResponse({**sent, "topic": topic}, background=beside)
        # understanding takes the model several seconds: keep it outside the
        # engine lock so the rest of the app stays responsive
        turn = await run_in_threadpool(engine.conversation().understand_turn, thread, text, about, files)
        r = await call(engine.conversation().execute_turn, turn)
        outcomes = []
        for o in r.outcomes:
            options = await call(engine.confirmation_options, o.confirm_id) if o.confirm_id else []
            outcomes.append({"text": o.text, "action_id": o.action_id, "link": o.link, "confirm_id": o.confirm_id,
                             "options": options, "handoff_id": o.handoff_id})
        return JSONResponse({"thread": turn["thread"], "reply": r.reply, "understood": r.understood, "outcomes": outcomes,
                             "suggest": await buttons(turn["thread"]),
                             "stood_in": r.stood_in, "topic": {**topic, "title": topic["title"] or (r.topic or {}).get("title")},
                             **await lend(turn["thread"], r.outcomes, files)})

    @authed
    @guarded
    async def close_task(request: Request) -> Response:
        data = await body(request)
        status = "dropped" if data.get("status") == "dropped" else "done"
        rec = await call(engine.tasks.close, request.path_params["key"], status, why=str(data.get("why") or ""))
        if rec is None:
            return JSONResponse({"error": "no such open task"}, status_code=404)
        return JSONResponse({"text": rec.text, "action_id": rec.id})

    async def buttons(thread: str) -> list[dict[str, Any]]:
        """The conversation's next steps as they are after a round: every one still to press, the other lines' too
        (the screen draws them again, so none it shows is one already answered)."""
        conv = engine.conversation()
        t = await call(conv.thread, thread)
        return await call(conv.buttons, t) if t else []

    async def lend(thread: str, outcomes: list[Any], files: Sequence[Attachment] = ()) -> dict[str, Any]:
        """A round that handed the conversation to a project (one the user asked about, or an idea's sandbox)
        goes on there: lent once the round is saved (outside the engine lock, like every runner call).
        {"lent": ...}, or nothing to add."""
        project = next((o.lend for o in outcomes if o.lend), None)
        if project is None or engine.leases is None:
            return {}
        try:
            return {"lent": await run_in_threadpool(engine.leases.lend, thread, project, files)}
        except ValueError as e:
            return {"lent": {"error": words("web.not_lent", project=project, why=e)}}

    def sent_files(ids: Any) -> list[Attachment]:
        """The files a sentence was sent with, by the ids /api/uploads gave; ValueError for one not kept here."""
        if not ids:
            return []
        if uploads is None or not isinstance(ids, list) or len(ids) > MAX_FILES:
            raise ValueError(words("web.too_many_files", most=MAX_FILES) if uploads is not None else words("web.no_uploads"))
        files = [uploads.get(str(i)) for i in ids]
        if not all(files):
            raise ValueError(words("web.file_gone"))
        return [f for f in files if f]

    @authed
    @guarded
    async def upload(request: Request) -> Response:
        """A file the user picked, pasted or dropped: kept here at once, so sending what they say never waits on it."""
        if uploads is None:
            return JSONResponse({"error": words("web.no_uploads")}, status_code=409)
        if request.headers.get("x-muninn-upload") != "1":  # a header only this page sets: another site's form cannot post here
            return JSONResponse({"error": "missing upload header"}, status_code=400)
        data = await request.body()
        try:
            meta = await run_in_threadpool(uploads.save, request.query_params.get("name", "file"),
                                           request.headers.get("content-type", ""), data)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse(meta)

    @authed
    async def uploaded(request: Request) -> Response:
        """A file the user sent, to show it again in the conversation."""
        f = await run_in_threadpool(uploads.get, request.path_params["uid"]) if uploads else None
        if f is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return Response(f.data, media_type=f.media_type, headers={"Cache-Control": "private, max-age=86400",
                                                                 "Content-Disposition": f"inline; filename*=UTF-8''{quote(f.name)}"})

    async def lease_command(thread: str | None, text: str) -> dict[str, Any]:
        """/attach <project>, /detach, and /work or /discuss (let a lent idea sandbox act, or back to
        read-only). Anything else starting with / is not
        sent to a model either. Runner calls happen outside the engine lock."""
        leases = engine.leases
        if leases is None:
            raise ValueError(words("web.no_leases"))
        if m := re.fullmatch(r"/attach\s+(\S+)", text):
            return await run_in_threadpool(leases.attach, thread, m.group(1))
        if re.fullmatch(r"/detach", text):
            if not thread or not await run_in_threadpool(leases.lease_for, thread):
                raise ValueError(words("web.not_lent_out"))
            return await run_in_threadpool(leases.detach, thread)
        if m := re.fullmatch(r"/(work|discuss)", text):
            if not thread or not await run_in_threadpool(leases.lease_for, thread):
                raise ValueError(words("web.not_lent_out"))
            return await run_in_threadpool(leases.set_mode, thread, m.group(1))
        raise ValueError(words("web.unknown_command"))

    @authed
    async def waiting_list(request: Request) -> Response:
        """What waits on the user, in order: the panel draws it, each entry by its kind."""
        return JSONResponse({"items": [w.to_dict() for w in await call(engine.waiting)]})

    @authed
    async def topics_today(request: Request) -> Response:
        """The conversation's topics for the panel: every one still open, and those closed or set aside today."""
        return JSONResponse({"items": await call(engine.topics().today)})

    @authed
    async def conversation_feed(request: Request) -> Response:
        """The latest conversations, as the one stream the user sees; `before` (a thread's ts) reaches further back."""
        before = request.query_params.get("before") or None
        return JSONResponse({"items": await call(engine.conversation().feed, before)})

    @authed
    @guarded
    async def command_act(request: Request) -> Response:
        """The user pressed a next step a discussion offered."""
        data = await body(request)
        try:
            r = await (call(engine.conversation().act, str(data["thread"]), button=str(data["id"])) if data.get("id")
                       else call(engine.conversation().act, str(data["thread"]), int(data["index"])))
        except (KeyError, ValueError, TypeError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        outcomes = [{"text": o.text, "action_id": o.action_id, "link": o.link, "confirm_id": o.confirm_id,
                     "options": (await call(engine.confirmation_options, o.confirm_id)) if o.confirm_id else [],
                     "handoff_id": o.handoff_id} for o in r.outcomes]
        lent = await lend(str(data["thread"]), r.outcomes)
        return JSONResponse({"reply": r.reply, "outcomes": outcomes, "suggest": await buttons(str(data["thread"])), **lent})

    @authed
    @guarded
    async def cancel_handoff(request: Request) -> Response:
        await body(request)
        ok = await call(engine.set_handoff_status, int(request.path_params["hid"]), "cancelled")
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    def screens_stamp() -> str:
        """A fingerprint of what the screens show: the conversation, the deep talks, what waits on the user, what
        Muninn did today (a deep talk's text as it streams is not in it: that view follows the talk itself)."""
        with lock:
            today = datetime.combine(engine.clock.now().date(), datetime.min.time(), engine.clock.now().tzinfo)
            parts: list[Any] = [engine.store.thread_stamps(FEED),
                                [(x["lease_id"], x["project"], engine.leases.label(x["project"]) if engine.leases else "", x["state"],
                                  x["mode"], x["warn_state"], x.get("error"))
                                 for x in engine.store.leases()],  # what the screens show of each lease (its name too)
                                [c.id for c in engine.pending_confirmations()],
                                [str(x.get("id")) for x in engine.review_queue()],
                                [(a.id, a.undone) for a in engine.store.actions(today)]]
            if messages is not None:
                parts.append([str(q.get("id")) for q in messages.open_questions()])
            if permissions is not None:
                parts.append([str(p.get("id")) for p in permissions.pending()])
        return hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:16]

    screens = ScreenWatch(engine.store.version, screens_stamp)

    @authed
    async def changes(request: Request) -> Response:
        """An open screen's one stream while it is in view: "changed" each time what the screens show changed,
        so what was said or done elsewhere (another device, Muninn on its own) is drawn at once."""
        q = screens.subscribe()

        async def stream():
            try:
                yield ": open\n\n"
                while True:
                    try:
                        await asyncio.wait_for(q.get(), timeout=15)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ": ping\n\n"
                        continue
                    yield "data: changed\n\n"
            finally:
                screens.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @authed
    async def review_queue(request: Request) -> Response:
        return JSONResponse({"items": await call(engine.review_queue)})

    @authed
    @guarded
    async def review_decide(request: Request) -> Response:
        data = await body(request)
        rec = await call(engine.review, str(data["id"]), str(data["decision"]), data.get("value"))
        return JSONResponse({"ok": True, "text": rec.text, "action_id": rec.id})

    @authed
    @guarded
    async def review_domain(request: Request) -> Response:
        """A drafted domain taken whole: every item of it still waiting is confirmed at once (undoable)."""
        data = await body(request)
        rec = await call(engine.plan_review.confirm_domain, str(data["domain"]), by=DecidedBy.USER)
        if rec is None:
            return JSONResponse({"error": "nothing waiting in that domain"}, status_code=404)
        return JSONResponse({"ok": True, "text": rec.text, "action_id": rec.id})

    @authed
    @guarded
    async def review_why(request: Request) -> Response:
        data = await body(request)
        answer = await run_in_threadpool(engine.review_why, str(data["id"]))  # local model: outside the lock
        return JSONResponse({"answer": answer or words("web.no_why")})

    @authed
    async def feature_requests(request: Request) -> Response:
        def view():
            return {"items": [{"id": r.get("id"), "text": r["text"], "ts": r["ts"]} for r in engine.feature_requests()],
                    "live_check_off": engine.requests.version.unknown}

        return JSONResponse(await call(view))

    @authed
    async def mail_message(request: Request) -> Response:
        m = await call(engine.read_mail, request.path_params["account"], request.path_params["mid"])
        return JSONResponse(m) if m else JSONResponse({"error": "not found"}, status_code=404)

    @authed
    @guarded
    async def save_draft(request: Request) -> Response:
        data = await body(request)
        ok = await call(engine.save_reply_draft, int(request.path_params["pid"]), str(data["body"]))
        return JSONResponse({"ok": ok}, status_code=200 if ok else 409)

    @authed
    @guarded
    async def replan(request: Request) -> Response:
        await body(request)
        acts = await call(engine.schedule_today, force=True)
        return JSONResponse({"ok": True, "blocks": [a.text for a in acts]})

    @authed
    async def item(request: Request) -> Response:
        i = await call(engine.store.get_inbox, int(request.path_params["iid"]))
        if i is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        await call(engine.store.mark_read, [i.id])
        return JSONResponse(_item(i))

    @authed
    async def home(request: Request) -> Response:
        return JSONResponse(await call(engine.home_view))

    @authed
    async def prepared(request: Request) -> Response:
        pid = int(request.path_params["pid"])
        view = await call(engine.prepared_view, pid)
        if not view:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({**view, "talk": await call(engine.conversation().suggestions_for, "packet", pid)})

    @authed
    @guarded
    async def prepared_done(request: Request) -> Response:
        await body(request)
        rec = await call(engine.prepared_done, int(request.path_params["pid"]))
        if rec is None:
            return JSONResponse({"error": "already handled"}, status_code=404)
        return JSONResponse({"ok": True, "text": rec.text, "action_id": rec.id})

    @authed
    @guarded
    async def prepared_send(request: Request) -> Response:
        """Send pressed on the packet's page: the filled application form goes out (the user's yes)."""
        await body(request)
        said = await call(engine.send_application, int(request.path_params["pid"]))
        if said is None:
            return JSONResponse({"error": engine.words("apply.no_form")}, status_code=404)
        return JSONResponse({"ok": True, "text": said})

    @authed
    @guarded
    async def prepared_dismiss(request: Request) -> Response:
        await body(request)
        rec = await call(engine.dismiss_prepared, int(request.path_params["pid"]))
        if rec is None:
            return JSONResponse({"error": "already handled"}, status_code=404)
        return JSONResponse({"ok": True, "text": rec.text, "action_id": rec.id})

    @authed
    @guarded
    async def undo_action(request: Request) -> Response:
        await body(request)
        ok = await call(engine.undo_action, int(request.path_params["aid"]))
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    @authed
    async def document(request: Request) -> Response:
        path = document_files().get(request.path_params["name"])
        if path is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(path, filename=path.name, headers={"Cache-Control": "no-store"})

    @authed
    async def decisions(request: Request) -> Response:
        return JSONResponse({"items": await call(engine.attribution_decisions)})

    @authed
    @guarded
    async def set_decision(request: Request) -> Response:
        data = await body(request)
        n = await call(engine.set_repo_domain, str(data["repo"]), data.get("domain") or None)
        return JSONResponse({"ok": True, "reattributed": n})

    @authed
    @guarded
    async def note(request: Request) -> Response:
        data = await body(request)
        text = str(data.get("text", "")).strip()
        if not text:
            raise ValueError("text is empty")
        await call(engine.add_note, text, data.get("domain") or None)
        return JSONResponse({"ok": True})

    @authed
    @guarded
    async def hours(request: Request) -> Response:
        data = await body(request)
        day = date.fromisoformat(data["date"]) if data.get("date") else None
        await call(engine.correct_hours, str(data["goal"]), float(data["hours"]), day, str(data.get("note", "")))
        return JSONResponse({"ok": True})

    @authed
    @guarded
    async def status(request: Request) -> Response:
        data = await body(request)
        change = await call(engine.set_status, str(data["domain"]), str(data["target"]), str(data["status"]))
        return JSONResponse({"ok": True, "change": change})

    @authed
    @guarded
    async def undo(request: Request) -> Response:
        ok = await call(engine.undo_status, int(request.path_params["change"]))
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    @authed
    @guarded
    async def done(request: Request) -> Response:
        data = await body(request)
        await call(engine.set_done, str(data["id"]), bool(data.get("done", True)))
        return JSONResponse({"ok": True})

    @authed
    async def vapid_key(request: Request) -> Response:
        return JSONResponse({"key": keys.application_server_key()})

    @authed
    @guarded
    async def subscribe(request: Request) -> Response:
        data = await body(request)
        sub = data["subscription"]
        if not (isinstance(sub, dict) and sub.get("endpoint", "").startswith("https://") and "keys" in sub):
            raise ValueError("invalid subscription")
        await call(engine.store.add_subscription, {"endpoint": sub["endpoint"], "keys": sub["keys"]}, engine.clock.now())
        return JSONResponse({"ok": True})

    @authed
    @guarded
    async def client_error(request: Request) -> Response:
        """Something went wrong on a screen (an error, a request that failed): kept in the log, so a tap that seemed
        to do nothing can be traced."""
        data = await body(request)
        log.warning("screen error on %s: %s (at %s)", request.state.device["name"], str(data.get("message"))[:500],
                    str(data.get("where"))[:200])
        return JSONResponse({"ok": True})

    @authed
    @guarded
    async def push_ack(request: Request) -> Response:
        data = await body(request)
        log.info("push received by device %s (%s)", request.state.device["name"], str(data.get("stage"))[:20])
        await call(engine.store.set_state, f"push:last_ack:{request.state.device['id']}", engine.clock.now().isoformat())
        return JSONResponse({"ok": True})

    @authed
    @guarded
    async def opened(request: Request) -> Response:
        """The app came into view: after a while away, Muninn opens with what waits (said before this answers, so the
        conversation read next has it)."""
        await body(request)
        return JSONResponse({"said": await call(engine.opened)})

    @authed
    @guarded
    async def push_test(request: Request) -> Response:
        await body(request)
        n = await call(engine.channel.send, [PushMessage("muninn", 1)])
        return JSONResponse({"ok": n > 0, "delivered": n})

    def setup_gate(handler):
        """Paired devices, once there is one; before that, whoever holds the token printed at start. Not where the
        request comes from: through tailscale serve, every request arrives from this machine."""
        async def wrapper(request: Request) -> Response:
            if setup is None or not tailnet_ok(request):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            if await run_in_threadpool(devices.any_paired):
                if await run_in_threadpool(devices.check, request.cookies.get(COOKIE)) is None:
                    return JSONResponse({"error": "not paired"}, status_code=401)
            elif not (setup.token and hmac.compare_digest(request.headers.get("x-setup-token", ""), setup.token)):
                return JSONResponse({"error": "setup token required"}, status_code=403)
            return await handler(request)

        return wrapper

    @setup_gate
    async def setup_state(request: Request) -> Response:
        assert setup is not None
        return JSONResponse(await run_in_threadpool(setup.state))

    @setup_gate
    @guarded
    async def setup_models(request: Request) -> Response:
        assert setup is not None
        data = await body(request)
        try:
            listed = await run_in_threadpool(setup.models, data)
        except CloudLoginLost:
            return JSONResponse({"error": words("web.key_refused")}, status_code=400)
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"models": listed})

    @setup_gate
    @guarded
    async def setup_save(request: Request) -> Response:
        assert setup is not None
        data = await body(request)
        restarting = await run_in_threadpool(setup.save, data, str(data.get("fast") or ""), data.get("standard"),
                                             data.get("language"))
        return JSONResponse({"ok": True, "restarting": restarting})

    @setup_gate
    @guarded
    async def setup_pair(request: Request) -> Response:
        await body(request)
        if not public_url:
            return JSONResponse({"error": words("web.needs_https")},
                                status_code=400)
        code = await run_in_threadpool(devices.new_pairing_code)
        url = f"{public_url.rstrip('/')}/?pair={code}"
        return JSONResponse({"code": code, "url": url, "qr": qr_svg(url)})

    @guarded
    async def pair(request: Request) -> Response:
        if not tailnet_ok(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        data = await body(request)
        token = await run_in_threadpool(devices.pair, str(data.get("code", "")), str(data.get("name", "")).strip())
        if token is None:
            return JSONResponse({"error": "invalid or expired code"}, status_code=403)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, token, max_age=COOKIE_MAX_AGE, httponly=True, secure=True, samesite="strict", path="/")
        return resp

    @authed
    async def me(request: Request) -> Response:
        device = request.state.device
        last_ack = await call(engine.store.get_state, f"push:last_ack:{device['id']}")
        return JSONResponse({"device": device, "last_push_received": last_ack})

    @authed
    async def settings(request: Request) -> Response:
        return JSONResponse(settings_view)

    @authed
    @guarded
    async def settings_language(request: Request) -> Response:
        """The language Muninn speaks, changed: written into config, then Muninn starts again in it."""
        if setup is None:
            return JSONResponse({"error": "settings cannot be changed here"}, status_code=404)
        data = await body(request)
        restarting = await run_in_threadpool(setup.set_language, str(data.get("language") or ""))
        return JSONResponse({"ok": True, "restarting": restarting})

    def ingest_authorized(request: Request) -> bool:
        """Bearer token, or HTTP Basic with the token as password (what phone
        trackers such as OwnTracks can send)."""
        auth = request.headers.get("authorization", "")
        if hmac.compare_digest(auth, f"Bearer {ingest_token}"):
            return True
        if auth.lower().startswith("basic "):
            try:
                _, _, password = base64.b64decode(auth[6:]).decode().partition(":")
            except (ValueError, UnicodeDecodeError):
                return False
            return hmac.compare_digest(password, ingest_token)
        return False

    trackers = {a.name: a for a in location_apps}

    async def ingest_location(request: Request) -> Response:
        """Where the phone is, from the tracking app named in the address: its points kept, the app answered as it
        expects (what it sends that is not a location is ignored, never refused, so the app does not retry it)."""
        app = trackers.get(request.path_params.get("app") or "owntracks")
        if app is None:
            return JSONResponse({"error": "no such tracking app"}, status_code=404)
        if not ingest_authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse(app.reply())
        points = app.points(data)
        if points:
            await call(engine.ingest, [location_observation(p, app.name) for p in points])
        return JSONResponse(app.reply())

    async def ingest_health(request: Request) -> Response:
        """Steps and sleep from an iOS Shortcuts automation (see health_setup)."""
        if not ingest_authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "expected JSON"}, status_code=400)
        now = engine.clock.now()
        obs = parse_health(data, now, local_date(now, engine.tz)) if isinstance(data, dict) else []
        if not obs:
            return JSONResponse({"error": "expected steps or sleep"}, status_code=400)
        await call(engine.ingest, obs)
        return JSONResponse({"ok": True, "got": {o.kind: o.payload for o in obs}})

    async def ingest_focus(request: Request) -> Response:
        """An iPhone Focus (e.g. Reading) turned on or off, from a Shortcuts automation."""
        if not ingest_authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "expected JSON"}, status_code=400)
        state = str(data.get("state", "")).lower() if isinstance(data, dict) else ""
        if state not in ("on", "off"):
            return JSONResponse({"error": "state must be on or off"}, status_code=400)
        name = str(data.get("focus") or words("web.focus"))[:40]
        await call(engine.focus, name, state == "on")
        return JSONResponse({"ok": True})

    @authed
    async def health_setup(request: Request) -> Response:
        base = public_url.rstrip("/") if public_url else f"https://{request.headers.get('host')}"
        since = engine.clock.now() - timedelta(days=14)
        last = {k: (obs[-1].payload | {"at": obs[-1].ts.isoformat()}) if (obs := await call(engine.store.observations, since, kinds=(k,))) else None
                for k in ("steps", "sleep")}
        return JSONResponse({"url": f"{base}/api/ingest/health", "focus_url": f"{base}/api/ingest/focus",
                             "authorization": f"Bearer {ingest_token}", "last": last,
                             "focus_open": await call(engine.store.get_state, "focus:open")})

    @authed
    async def location_setup(request: Request) -> Response:
        """How to set up each tracking app to send here (a link that configures it, or what to type), and when a point
        last came."""
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        base = public_url.rstrip("/") if public_url else f"https://{host}" if host else str(request.base_url).rstrip("/")
        last = await call(engine.store.observations, engine.clock.now() - timedelta(days=30), kinds=("location",))
        apps = []
        for a in location_apps:
            s = a.setup(f"{base}/api/ingest/location/{a.name}", ingest_token, words)
            apps.append({"name": a.name, "label": a.label, "link": s.link, "note": s.note,
                         "fields": [{"label": k, "value": v, "secret": v == ingest_token} for k, v in s.fields]})
        return JSONResponse({"apps": apps, "last_received": last[-1].ts.isoformat() if last else None,
                             "last_app": last[-1].payload.get("app") if last else None})

    @authed
    async def activity_setup(request: Request) -> Response:
        """How each computer's collector is installed here (a line to run, the token it asks for), and when it last
        sent something."""
        base = address(request)
        last = await call(engine.store.observations, engine.clock.now() - timedelta(days=30), kinds=("window",))
        return JSONResponse({"collectors": [{"name": c.name, "label": c.label(words), "command": c.command(base), "note": c.note(words)}
                                            for c in COLLECTORS],
                             "token": ingest_token, "last_received": last[-1].ts.isoformat() if last else None,
                             "last_source": last[-1].source if last else None})

    async def collector_file(request: Request) -> Response:
        """A collector's files, for a computer to install it: public code, the address filled in, no secret in them."""
        c = next((c for c in COLLECTORS if c.name == request.path_params["name"]), None)
        data = c.file(request.path_params["file"], address(request)) if c else None
        if data is None:
            return Response("not found", status_code=404, media_type="text/plain")
        return Response(data, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-cache"})

    def address(request: Request) -> str:
        """Where Muninn is reached: the public address, else the one this request came to."""
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        return public_url.rstrip("/") if public_url else f"https://{host}" if host else str(request.base_url).rstrip("/")

    @authed
    @guarded
    async def name_place(request: Request) -> Response:
        data = await body(request)
        r = await call(engine.whereabouts.name, str(data["name"]), float(data["lat"]), float(data["lon"]))
        if r is None:
            return JSONResponse({"error": "not saved"}, status_code=400)
        return JSONResponse({"ok": True, "text": r.text, "action_id": r.id})

    @guarded
    async def ingest(request: Request) -> Response:
        auth = request.headers.get("authorization", "")
        if not hmac.compare_digest(auth, f"Bearer {ingest_token}"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        data = await body(request)
        try:
            obs, refused = activity.observations(data.get("observations", []))  # contracts/activity.v1.md
        except activity.Refused as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        new = await call(engine.ingest, obs)
        if refused:
            log.warning("ingest: %d observation(s) refused: %s", len(refused), refused[0]["why"])
        return JSONResponse({"ok": True, "new": len(new), "refused": refused})

    @guarded
    async def project_message_post(request: Request) -> Response:
        """Projects post here (same bearer token as ingest): questions for the
        user, progress, done. Retrying a post is safe: ids already seen are
        skipped."""
        auth = request.headers.get("authorization", "")
        if not hmac.compare_digest(auth, f"Bearer {ingest_token}"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        data = await body(request)
        if data.get("v", MESSAGES_VERSION) != MESSAGES_VERSION:
            raise ValueError(f"unsupported messages version {data.get('v')!r}")
        raw = data.get("messages")
        if not isinstance(raw, list):
            raise ValueError("messages must be a list")
        new = await call(messages.receive, raw)
        return JSONResponse({"ok": True, "new": new})

    @guarded
    async def records_post(request: Request) -> Response:
        """Principal Handoff records from the projects (same bearer token): each checked on its own, so one bad
        record never blocks the rest; a record already held is a duplicate, and posting again is safe."""
        if not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {ingest_token}"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise ValueError("content-type must be application/json")
        data = ph.loads(await request.body())  # as I-JSON: a member name twice makes that record invalid
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        raw = data.get("records")
        if not isinstance(raw, list):
            raise ValueError("records must be a list")
        return JSONResponse({"ok": True, "results": await call(engine.records_in.receive, raw)})

    @guarded
    async def records_get(request: Request) -> Response:
        """What Muninn tells `project`, as Principal Handoff records (in the version it speaks), after `after` (the cursor the last read
        returned; 0 for everything Muninn still keeps)."""
        if not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {ingest_token}"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        project = request.query_params.get("project") or ""
        if not project:
            raise ValueError("project is required")
        after, limit = int(request.query_params.get("after") or 0), min(int(request.query_params.get("limit") or 200), 1000)
        records, cursor = await call(engine.records_out.since, project, after, limit)
        return JSONResponse({"records": records, "cursor": cursor})

    @guarded
    async def records_ack(request: Request) -> Response:
        """The project has the records up to `through` (a cursor)."""
        if not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {ingest_token}"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        data = await body(request)
        project, through = str(data.get("project") or ""), data.get("through")
        if not project or isinstance(through, bool) or not isinstance(through, int):
            raise ValueError("project and through (a cursor) are required")
        return JSONResponse({"ok": True, "through": await call(engine.records_ack, project, through)})

    @authed
    async def project_questions(request: Request) -> Response:
        items = await call(messages.open_questions)
        out = []
        for q in items:
            ctx = str(q.get("context") or "")
            # the questions a lent conversation left behind lead back to that conversation
            lease_id = ctx.removeprefix("lease:") if engine.leases and ctx.startswith("lease:") else None
            thread = engine.leases.thread_of(lease_id) if lease_id else None
            out.append({**q, "lease_thread": thread, "lease_id": lease_id if thread else None,
                        **({"label": engine.leases.label(q["project"])} if engine.leases and q.get("project") else {}),
                        "talk": await call(engine.conversation().suggestions_for, "project_question", q["id"])})
        return JSONResponse({"items": out})

    @authed
    @guarded
    async def project_question_answer(request: Request) -> Response:
        data = await body(request)
        h = await call(messages.answer, request.path_params["mid"], str(data["answer"]))
        return JSONResponse({"ok": h is not None, "handoff_id": h and h["id"]}, status_code=200 if h else 404)

    @authed
    @guarded
    async def project_question_dismiss(request: Request) -> Response:
        h = await call(messages.dismiss, request.path_params["mid"])
        return JSONResponse({"ok": h is not None, "handoff_id": h and h["id"]}, status_code=200 if h else 404)

    # ── lending a thread to a project's session ─────────────────────────────

    def locked(fn):
        with lock:
            return fn()

    checked = {"at": float("-inf")}

    def check_live() -> None:
        """Whether what runs now includes the requests' commits: when the server starts, then every LIVE_CHECK_S on the
        lease ticker."""
        if time.monotonic() - checked["at"] < LIVE_CHECK_S:
            return
        checked["at"] = time.monotonic()
        try:
            engine.check_live(locked)
        except Exception:
            log.exception("live check failed")

    feed: LeaseFeed | None = None
    sidecar: Sidecar | None = None
    if engine.leases is not None and lease_runner is not None:
        feed = LeaseFeed(engine.store, lease_runner, engine.leases, permissions, locked,
                         background=[lambda: engine.idea_reports().run(locked), lambda: engine.promote_waiting(locked), check_live])
        engine.leases.serialize_with(locked)  # thread writes under the lock; the runner's calls outside it
        # what an open talk shows of its lease (its name after a rename, its mode, its state), as it changes
        engine.leases.on_change(lambda lease_id, lease: feed.publish(lease_id, {"type": "lease", "lease": lease}))
        engine.leases.on_relay(feed.publish)  # what Muninn told a talk, shown once it has heard it
        if uploads is not None:
            engine.leases.files_from(lambda ids: [f for i in ids if (f := uploads.get(str(i)))])
        sidecar = Sidecar(lambda tid, said, beside: engine.conversation().understand_turn(tid, said, beside=beside), engine.commands, engine.store,
                          engine.clock, locked, feed.publish, relay=engine.leases.relay, working=engine.leases.working_beside,
                          label=engine.leases.label, words=words)

    @authed
    async def lease_view(request: Request) -> Response:
        """The conversation's lease (if any) with its turns, and what can be attached."""
        leases = engine.leases
        if leases is None:
            return JSONResponse({"enabled": False})
        thread = request.query_params.get("thread")
        if request.query_params.get("active"):  # the home page: a way back to every lent conversation
            return JSONResponse({"enabled": True, "active": await run_in_threadpool(leases.active)})
        if past := request.query_params.get("lease"):  # rereading one that came back
            return JSONResponse({"enabled": True, "transcript": await run_in_threadpool(leases.transcript, past)})
        view = await run_in_threadpool(leases.view, thread) if thread else None
        return JSONResponse({"enabled": True, "lease": view, "attachable": await run_in_threadpool(leases.attachable),
                             "quota_warning": await run_in_threadpool(leases.quota_warning)})

    @authed
    @guarded
    async def lease_attach(request: Request) -> Response:
        data = await body(request)
        if engine.leases is None:
            raise ValueError(words("web.no_leases"))
        r = await run_in_threadpool(engine.leases.attach, str(data["thread"]) if data.get("thread") else None,
                                    str(data["project"]), confirm_quota=bool(data.get("confirm_quota")))
        if feed is not None and r.get("lease_id"):
            feed.ensure()
        return JSONResponse(r)

    @authed
    @guarded
    async def lease_action(request: Request) -> Response:
        """keepalive (answering the idle question), terminal (go on in a terminal), detach (take it back),
        work / discuss (let an idea sandbox act, each write asked first; or back to read-only)."""
        data = await body(request)
        if engine.leases is None:
            raise ValueError(words("web.no_leases"))
        action = request.path_params["action"]
        leases = engine.leases
        fn = {"keepalive": leases.keepalive, "terminal": leases.terminal, "detach": leases.detach,
              "work": lambda thread: leases.set_mode(thread, "work"),
              "discuss": lambda thread: leases.set_mode(thread, "discuss")}.get(action)
        if fn is None:
            return JSONResponse({"error": "unknown action"}, status_code=404)
        return JSONResponse(await run_in_threadpool(fn, str(data["thread"])))  # runner calls: not under the lock

    @authed
    async def lease_file(request: Request) -> Response:
        """A file a deep talk's session showed the user: only to that talk's own conversation (?thread=), only one
        it did show there, passed on as it came (its type, shown in place, never sniffed); the browser never reaches
        the runner itself. Gone once it is past the days it is kept: said so (410). Not there before that, or the
        runner not answering: to be asked again in a moment (503), never taken for gone."""
        lease_id, file_id = request.path_params["lease_id"], request.path_params["file_id"]
        lease = await run_in_threadpool(engine.store.lease, lease_id)
        shown = next((t for t in await run_in_threadpool(engine.store.lease_turns, lease_id) if t["role"] == "file"
                      and any(f.get("id") == file_id for f in t.get("files") or [])), None) \
            if lease is not None and lease["thread"] == request.query_params.get("thread") else None
        if shown is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            content, mime = await run_in_threadpool(lease_runner.file, lease_id, file_id)
        except RunnerError as e:
            kept = engine.clock.now() - datetime.fromisoformat(shown["ts"]) < SHOWN_FILES_KEPT
            if e.category == "not_found" and not kept:
                return Response(words("web.file_expired"), status_code=410, media_type="text/plain; charset=utf-8")
            return Response(words("web.file_not_yet"), status_code=503, media_type="text/plain; charset=utf-8",
                            headers={"Retry-After": "2"})
        return Response(content, media_type=mime, headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff",
                                                           "Cache-Control": "private, max-age=86400"})

    @authed
    async def lease_events(request: Request) -> Response:
        """The lease's events as they come (text as it is written, tool lines, state), for an open conversation."""
        if feed is None:
            return JSONResponse({"error": "not set up"}, status_code=404)
        lease_id = request.path_params["lease_id"]
        q = feed.subscribe(lease_id)
        # first, the talk as it is now: a change made before this stream opened (or while it was reconnecting) is
        # not missed. Subscribed before it is read, so nothing falls between the two.
        lease = await run_in_threadpool(engine.store.lease, lease_id) if engine.leases is not None else None
        now = {"type": "lease", "lease": engine.leases.shown(lease)} if lease is not None else None

        async def stream():
            try:
                yield ": open\n\n"
                if now is not None:
                    yield f"data: {json.dumps(now, ensure_ascii=False)}\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ": ping\n\n"
                        continue
                    yield f"id: {ev.get('seq', '')}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            finally:
                feed.unsubscribe(lease_id, q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ── what a lent session's relay asks the user ───────────────────────────

    waiting: dict[str, asyncio.Event] = {}

    def relay_ok(request: Request) -> bool:
        return hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {ingest_token}")

    @guarded
    async def permission_post(request: Request) -> Response:
        """The relay asks (Bearer = ingest token); the id makes a retry harmless.
        200 with the decision if there is one already, else 202 pending."""
        if not relay_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        data = await body(request)
        if data.get("v") != 2:  # contracts/permission-requests.v2.schema.json
            return JSONResponse({"error": "permission requests are v2"}, status_code=400)
        row = await call(permissions.receive, data)
        res = await run_in_threadpool(permissions.result, row["id"])
        if feed is not None and row.get("note") and row.get("lease_id"):  # allowed without asking: said in the talk
            feed.publish(row["lease_id"], {"type": "permission_answered", "id": row["id"], "text": row["note"]})
        if res:
            return JSONResponse(res, status_code=200)
        card = await run_in_threadpool(permissions.card, row["id"])
        if feed is not None and card and card.get("lease"):
            feed.publish(card["lease"], {"type": "permission_card", "card": card})  # answerable in the conversation itself
        return JSONResponse({"v": int(row.get("v") or 1), "id": row["id"], "pending": True}, status_code=202)

    async def permission_wait(request: Request) -> Response:
        """The relay waits for the decision (?wait= up to 25 s). Waiting holds no lock, and every check reads the
        stored row, so a decision made before a restart is still found."""
        if not relay_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        rid = request.path_params["rid"]
        try:
            wait = max(0.0, min(float(request.query_params.get("wait") or 0), 25.0))
        except ValueError:
            wait = 0.0
        deadline = asyncio.get_running_loop().time() + wait
        while True:
            try:
                res = await run_in_threadpool(permissions.result, rid)
            except KeyError:
                return JSONResponse({"v": 1, "error": "not_found"}, status_code=404)
            if res:
                waiting.pop(rid, None)
                return JSONResponse(res)
            left = deadline - asyncio.get_running_loop().time()
            if left <= 0:
                return JSONResponse({"v": 1, "id": rid, "pending": True}, status_code=202)
            ev = waiting.setdefault(rid, asyncio.Event())
            try:
                await asyncio.wait_for(ev.wait(), timeout=min(left, 5))
            except asyncio.TimeoutError:
                pass

    @authed
    async def permission_list(request: Request) -> Response:
        return JSONResponse({"items": await run_in_threadpool(permissions.pending)})

    @authed
    @guarded
    async def permission_decide(request: Request) -> Response:
        data = await body(request)
        rid = request.path_params["rid"]
        answers = data.get("answers") if isinstance(data.get("answers"), dict) else None
        try:
            row = await call(permissions.decide, rid, str(data["decision"]),
                             {str(k): [str(x) for x in v] if isinstance(v, list) else str(v) for k, v in answers.items()}
                             if answers else None)  # a question with several to pick is answered with a list
        except KeyError:
            return JSONResponse({"error": "no such request"}, status_code=404)
        if rid in waiting:
            waiting[rid].set()
        if feed is not None and row.get("note") and row.get("lease_id"):
            feed.publish(row["lease_id"], {"type": "permission_answered", "id": rid, "text": row["note"]})
        return JSONResponse({"ok": True, "note": row.get("note")})

    @authed
    @guarded
    async def auto_commands(request: Request) -> Response:
        """Whether commands of a kind the user allowed before run without asking (POST {on}: turned so)."""
        on = (await body(request)).get("on") if request.method == "POST" else None
        got = await call(permissions.auto_commands, None if on is None else bool(on))
        return JSONResponse({"on": got})

    async def smoke(request: Request) -> Response:
        """The running server checks its own dependencies (ingest key; read-only)."""
        if not ingest_authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        results = []
        for name, check in smoke_checks:
            try:
                results.append({"name": name, "ok": True, "detail": str(await run_in_threadpool(check))[:200]})
            except Exception as e:  # each check reports its own failure; the rest still run
                results.append({"name": name, "ok": False, "detail": f"{type(e).__name__}: {e}"[:200]})
        return JSONResponse({"ok": all(r["ok"] for r in results), "checks": results})

    @authed
    async def day(request: Request) -> Response:
        if day_view is None:
            return JSONResponse({"now": datetime.now().astimezone().isoformat(), "events": [], "activity": [], "hours": [], "stays": []})
        return JSONResponse(await call(day_view.today))

    @authed
    async def goal_days(request: Request) -> Response:
        return JSONResponse({"goals": {} if day_view is None else await call(day_view.goal_days)})

    @authed
    async def no_leases(request: Request) -> Response:
        return JSONResponse({"enabled": False, "active": []})  # the configured route's shape

    @authed
    async def no_permission_requests(request: Request) -> Response:
        return JSONResponse({"items": []})

    routes = [
        Route("/", index),
        Route("/sw.js", sw),
        Route("/manifest.webmanifest", manifest),
        Route("/healthz", health),
        Route("/api/smoke", smoke),
        Route("/privacy", privacy),
        Route("/api/pair", pair, methods=["POST"]),
        Route("/api/setup", setup_state),
        Route("/api/setup/models", setup_models, methods=["POST"]),
        Route("/api/setup/save", setup_save, methods=["POST"]),
        Route("/api/setup/pair", setup_pair, methods=["POST"]),
        Route("/api/me", me),
        Route("/api/settings", settings),
        Route("/api/settings/language", settings_language, methods=["POST"]),
        Route("/api/today", today),
        Route("/api/day", day),
        Route("/api/goal-days", goal_days),
        Route("/api/inbox", inbox),
        Route("/api/inbox/read", mark_read, methods=["POST"]),
        Route("/api/ideas/{n:int}/settle", settle_idea, methods=["POST"]),
        Route("/api/confirmations", confirmations),
        Route("/api/confirmations/{cid:int}", answer, methods=["POST"]),
        Route("/oauth/{provider}/callback", oauth_callback),
        Route("/api/connections", connections_status),
        Route("/api/connections/{provider}", connect, methods=["POST"]),
        Route("/api/prepared/{pid:int}/draft", save_draft, methods=["POST"]),
        Route("/api/schedule", replan, methods=["POST"]),
        Route("/api/mail/{account}/{mid}", mail_message),
        Route("/api/command", command, methods=["POST"]),
        Route("/api/uploads", upload, methods=["POST"]),
        Route("/api/uploads/{uid:str}", uploaded),
        Route("/api/command/act", command_act, methods=["POST"]),
        Route("/api/conversation", conversation_feed),
        Route("/api/changes/events", changes),
        Route("/api/topics", topics_today),
        Route("/api/waiting", waiting_list),
        Route("/api/tasks/{key:path}/close", close_task, methods=["POST"]),
        Route("/api/requests", feature_requests),
        Route("/api/handoffs/{hid:int}/cancel", cancel_handoff, methods=["POST"]),
        Route("/api/review", review_queue),
        Route("/api/review", review_decide, methods=["POST"]),
        Route("/api/review/domain", review_domain, methods=["POST"]),
        Route("/api/review/why", review_why, methods=["POST"]),
        Route("/api/home", home),
        Route("/api/items/{iid:int}", item),
        Route("/api/prepared/{pid:int}", prepared),
        Route("/api/prepared/{pid:int}/done", prepared_done, methods=["POST"]),
        Route("/api/prepared/{pid:int}/dismiss", prepared_dismiss, methods=["POST"]),
        Route("/api/prepared/{pid:int}/send", prepared_send, methods=["POST"]),
        Route("/api/actions/{aid:int}/undo", undo_action, methods=["POST"]),
        Route("/api/documents/{name}", document),
        Route("/api/attribution", decisions),
        Route("/api/attribution", set_decision, methods=["POST"]),
        Route("/api/input/note", note, methods=["POST"]),
        Route("/api/input/hours", hours, methods=["POST"]),
        Route("/api/tracker/status", status, methods=["POST"]),
        Route("/api/tracker/undo/{change:int}", undo, methods=["POST"]),
        Route("/api/deadlines/done", done, methods=["POST"]),
        Route("/api/push/key", vapid_key),
        Route("/api/push/subscribe", subscribe, methods=["POST"]),
        Route("/api/push/test", push_test, methods=["POST"]),
        Route("/api/push/ack", push_ack, methods=["POST"]),
        Route("/api/opened", opened, methods=["POST"]),
        Route("/api/client-error", client_error, methods=["POST"]),
        Route("/api/ingest", ingest, methods=["POST"]),
        Route("/api/setup/activity", activity_setup),
        Route("/collectors/{name}/{file}", collector_file),
        Route("/api/ingest/owntracks", ingest_location, methods=["POST"]),  # phones set up before apps were named in the address
        Route("/api/ingest/location/{app:str}", ingest_location, methods=["POST"]),
        Route("/api/ingest/health", ingest_health, methods=["POST"]),
        Route("/api/ingest/focus", ingest_focus, methods=["POST"]),
        Route("/api/setup/health", health_setup),
        Route("/api/places", name_place, methods=["POST"]),
        Route("/api/setup/location", location_setup),
        Route("/api/records", records_post, methods=["POST"]),
        Route("/api/records", records_get, methods=["GET"]),
        Route("/api/records/ack", records_ack, methods=["POST"]),
        Mount("/static", RevalidatedStatic(directory=STATIC)),
    ]
    if messages is not None:
        routes[-1:-1] = [
            Route("/api/messages", project_message_post, methods=["POST"]),
            Route("/api/project-questions", project_questions),
            Route("/api/project-questions/{mid:str}/answer", project_question_answer, methods=["POST"]),
            Route("/api/project-questions/{mid:str}/dismiss", project_question_dismiss, methods=["POST"]),
        ]
    if engine.leases is not None:
        routes[-1:-1] = [
            Route("/api/leases", lease_view),
            Route("/api/leases/attach", lease_attach, methods=["POST"]),
            Route("/api/leases/{action:str}", lease_action, methods=["POST"]),
            Route("/api/leases/{lease_id:str}/events", lease_events),
            Route("/api/leases/{lease_id:str}/files/{file_id:str}", lease_file),
        ]
    else:  # not set up: the app still asks, and gets nothing rather than a 404
        routes[-1:-1] = [Route("/api/leases", no_leases)]
    if permissions is not None:
        routes[-1:-1] = [
            Route("/api/permission-requests", permission_post, methods=["POST"]),
            Route("/api/permission-requests/pending", permission_list),
            Route("/api/permission-requests/{rid:str}", permission_wait),
            Route("/api/permission-requests/{rid:str}/decision", permission_decide, methods=["POST"]),
            Route("/api/permissions/auto-commands", auto_commands, methods=["GET", "POST"]),
        ]

    if permissions is None:
        routes[-1:-1] = [Route("/api/permission-requests/pending", no_permission_requests)]

    @contextlib.asynccontextmanager
    async def lifespan(app):
        screens.start()
        asyncio.get_running_loop().run_in_executor(None, check_live)  # git is asked outside the event loop
        if feed is not None:
            feed.start()
        try:
            yield
        finally:
            screens.stop()
            if feed is not None:
                feed.stop()

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.lease_feed = feed  # the conversation views' event fan-out, reachable for tests
    app.state.screens = screens  # what tells open screens a change, likewise
    app.state.screens = screens  # telling the open screens what changed, reachable for tests
    return app
