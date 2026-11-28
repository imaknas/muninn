"""Principal Handoff Transport 0.1's HTTP mapping (section 8): `POST records`, `GET records`, `POST records/ack` under
the base URL `/ph/`, reached over HTTPS (`tailscale serve`, whose Host is the public address) or, on this computer, a
Unix socket. Plain HTTP to the loopback port asks for another Host and is refused: it authenticates no server."""

from __future__ import annotations

import asyncio
import hmac
import logging
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from muninn.core import ph
from muninn.core.transport import Credential, Transport, TransportError

log = logging.getLogger(__name__)

BASE = "/ph/"
SOCKET_HOST = "localhost"  # the Host a client on the Unix socket names (its base URL: http://localhost/ph/)
MAX_BODY = 4 * 1024 * 1024
MAX_WAIT = 60
POLL_S = 0.5
FETCH = {"project", "after", "as", "limit", "wait"}


def via_socket(request: Request) -> bool:
    """Over the Unix socket: a connection with no peer or local address (on TCP there is always one)."""
    return request.client is None and request.scope.get("server") is None


def transport_routes(transport: Transport, credentials: Mapping[str, Credential], hosts: Sequence[str],
                     call: Callable[..., Awaitable[Any]]) -> list[Route]:
    """`credentials`: by token, what each speaks for. `hosts`: the Hosts the HTTPS base URL names (no CORS headers are
    sent, and a page elsewhere cannot name them)."""
    allowed = {h.lower() for h in hosts}

    def credential(request: Request) -> Credential:
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth[:7].lower() == "bearer " else ""
        for known, cred in credentials.items():  # every one compared, in constant time
            if token and hmac.compare_digest(token.encode(), known.encode()):
                return cred
        raise TransportError(401, "unauthenticated", "no credential, or one the server does not know")

    def checked(handler: Callable[[Request, Credential], Awaitable[Response]]):
        async def wrapper(request: Request) -> Response:
            try:
                host = (urlsplit("//" + request.headers.get("host", "")).netloc or "").lower()
                if not (via_socket(request) and host.split(":")[0] == SOCKET_HOST) and host not in allowed:
                    raise TransportError(421, "misdirected", "the transport is reached at its HTTPS base URL, or the Unix socket")
                return await handler(request, credential(request))
            except TransportError as e:
                if e.code == "no_role":  # so that a missing configuration can be found
                    log.warning("transport: %s %s refused (%s)", request.method, request.url.path, e.message)
                return JSONResponse(e.body(), status_code=e.status)

        return wrapper

    async def json_body(request: Request) -> Any:
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise TransportError(400, "malformed", "the body is application/json")
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise TransportError(413, "too_large", f"a body is at most {MAX_BODY} bytes")
        try:
            data = ph.loads(raw)
        except ValueError as e:
            raise TransportError(400, "malformed", f"the body is not JSON ({str(e)[:120]})") from e
        if not isinstance(data, dict) or ph.TWICE in data:
            raise TransportError(400, "malformed", "the body is a JSON object, each member once")
        return data

    def params(request: Request, allowed_names: set[str]) -> dict[str, str]:
        pairs = parse_qsl(request.url.query.replace("+", "%2B"), keep_blank_values=True)  # "+" stands for itself (8.1)
        names = [k for k, _ in pairs]
        for k in names:
            if k not in allowed_names or names.count(k) > 1:
                raise TransportError(400, "bad_parameter", f"{k} is unknown or given twice")
        return dict(pairs)

    def integer(value: str | None, name: str, low: int, high: int | None = None) -> int | None:
        if value is None:
            return None
        if not value.isdigit() or int(value) < low or (high is not None and int(value) > high):
            raise TransportError(400, "bad_parameter", f"{name} is an integer from {low}" + (f" to {high}" if high else ""))
        return int(value)

    @checked
    async def send(request: Request, cred: Credential) -> Response:
        data = await json_body(request)
        records = data.get("records")
        if not isinstance(records, list) or not records or set(data) - {"records"}:
            raise TransportError(400, "malformed", "the body is {\"records\": [...]}, with at least one record")
        return JSONResponse({"results": await call(transport.send, cred, records)})

    @checked
    async def fetch(request: Request, cred: Credential) -> Response:
        q = params(request, FETCH)
        if "project" not in q:
            raise TransportError(400, "bad_parameter", "project is required", "/project")
        limit, wait = integer(q.get("limit"), "limit", 1), integer(q.get("wait"), "wait", 0, MAX_WAIT) or 0
        got = await call(transport.fetch, cred, q["project"], q.get("after"), q.get("as"), limit)
        deadline = asyncio.get_running_loop().time() + wait
        while not got["records"] and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(POLL_S)
            cred = credential(request)  # checked again before a held answer
            got = await call(transport.fetch, cred, q["project"], q.get("after"), q.get("as"), limit)
        return JSONResponse(got)

    @checked
    async def ack(request: Request, cred: Credential) -> Response:
        data = await json_body(request)
        if set(data) - {"project", "through", "as"} or not isinstance(data.get("project"), str) \
                or not isinstance(data.get("through"), str) or ("as" in data and not isinstance(data["as"], str)):
            raise TransportError(400, "bad_parameter", "the body is {\"project\", \"through\": <cursor>, \"as\"?}")
        return JSONResponse({"acked": await call(transport.ack, cred, data["project"], data["through"], data.get("as"))})

    return [
        Route(f"{BASE}records", send, methods=["POST"]),
        Route(f"{BASE}records", fetch, methods=["GET"]),
        Route(f"{BASE}records/ack", ack, methods=["POST"]),
    ]
