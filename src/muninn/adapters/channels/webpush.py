"""Web Push (RFC 8291 payload encryption, VAPID auth) to the PWA's subscriptions.

The payload carries only "<domain>: N new reminders"; the content itself stays
in the PWA, which reads it from this machine over the tailnet."""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Callable, Sequence

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush

from muninn.core.ports import PushMessage, SubscriptionStore

log = logging.getLogger(__name__)


class VapidKeys:
    def __init__(self, private_pem: Path):
        self.path = Path(private_pem)

    def ensure(self) -> bool:
        """Creates the key pair once (`muninn init` only: a new pair makes every phone's
        subscription stop receiving). Returns True if it was created now."""
        if self.path.exists():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        v = Vapid02()
        v.generate_keys()
        v.save_key(str(self.path))
        os.chmod(self.path, 0o600)
        return True

    def present(self) -> bool:
        return self.path.is_file()

    def vapid(self) -> Vapid02:
        return Vapid02.from_file(str(self.path))

    def application_server_key(self) -> str:
        raw = self.vapid().public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class WebPushChannel:
    def __init__(self, store: SubscriptionStore, keys: VapidKeys, subject: str, ttl: int = 6 * 3600,
                 taken: Callable[[], None] = lambda: None):
        """`taken()`: the push service took a notification (which says nothing yet of a phone showing it)."""
        self._store = store
        self._keys = keys
        self._subject = subject
        self._ttl = ttl
        self._taken = taken

    def send(self, messages: Sequence[PushMessage]) -> int:
        subs = self._store.subscriptions()
        if not subs or not messages:
            return 0
        vapid = self._keys.vapid()
        delivered = 0
        for m in messages:
            payload = json.dumps({"title": "Muninn", "body": m.text[:180], "domain": m.domain, "count": m.count}, ensure_ascii=False)
            ok = False
            for sub in subs:
                try:
                    webpush(
                        subscription_info=sub,
                        data=payload,
                        vapid_private_key=vapid,
                        vapid_claims={"sub": self._subject},
                        ttl=self._ttl,
                        # No Topic header: Apple's push service rejects it
                        # (BadWebPushTopic). The service worker collapses
                        # notifications by tag instead.
                        headers={"Urgency": "normal"},
                        timeout=15,
                    )
                    ok = True
                except WebPushException as e:
                    status = getattr(e.response, "status_code", None)
                    if status in (404, 410):
                        log.info("removing expired push subscription")
                        self._store.remove_subscription(sub["endpoint"])
                    else:
                        log.warning("push failed (%s): %s", status, e)
                except Exception as e:
                    log.warning("push failed: %s", e)
            delivered += ok
        if delivered:
            self._taken()
        return delivered


class ConsoleChannel:
    """Prints what would be pushed. For dry runs and development."""

    def send(self, messages: Sequence[PushMessage]) -> int:
        for m in messages:
            print(f"[push] {m.text}")
        return len(messages)
