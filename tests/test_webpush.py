from __future__ import annotations

import base64
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from muninn.adapters.channels.webpush import VapidKeys, WebPushChannel
from muninn.adapters.store_sqlite import SqliteStore
from muninn.core.ports import PushMessage


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def serve(status: int):
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.append((self.path, dict(self.headers), body))
            self.send_response(status)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, got


def subscription(port: int) -> dict:
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"endpoint": f"http://127.0.0.1:{port}/push/abc", "keys": {"p256dh": b64(pub), "auth": b64(os.urandom(16))}}


def test_encrypted_push_with_vapid(tmp_path):
    srv, got = serve(201)
    store = SqliteStore(":memory:")
    from datetime import datetime, timezone

    store.add_subscription(subscription(srv.server_port), datetime.now(timezone.utc))
    keys = VapidKeys(tmp_path / "v.pem")
    keys.ensure()
    n = WebPushChannel(store, keys, "https://muninn.example").send([PushMessage("jobs", 2)])
    srv.shutdown()
    assert n == 1
    path, headers, body = got[0]
    headers = {k.lower(): v for k, v in headers.items()}
    assert headers["content-encoding"] == "aes128gcm"
    assert headers["authorization"].startswith("vapid t=")
    assert b"jobs" not in body  # encrypted payload
    assert "topic" not in headers  # Apple rejects it


def test_gone_subscription_is_removed(tmp_path):
    srv, _ = serve(410)
    store = SqliteStore(":memory:")
    from datetime import datetime, timezone

    store.add_subscription(subscription(srv.server_port), datetime.now(timezone.utc))
    keys = VapidKeys(tmp_path / "v.pem")
    keys.ensure()
    assert WebPushChannel(store, keys, "https://muninn.example").send([PushMessage("jobs", 1)]) == 0
    srv.shutdown()
    assert store.subscriptions() == []


def test_vapid_subject_from_public_url_drops_port(tmp_path, monkeypatch):
    from py_vapid import _check_sub

    from muninn.config import Config

    monkeypatch.setenv("MUNINN_PUBLIC_URL", "https://muninn.example-tailnet.ts.net:8444/")
    subject = Config.load(tmp_path).vapid_subject
    assert subject == "https://muninn.example-tailnet.ts.net"
    assert _check_sub(subject)
