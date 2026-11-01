"""OwnTracks (iOS/Android), HTTP mode: one location report per post, answered with the friends' locations (none)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from muninn.core.language import Language
from muninn.core.location import AppSetup, LocationPoint


class OwnTracks:
    name = "owntracks"
    label = "OwnTracks"

    def points(self, body: Any) -> list[LocationPoint]:
        """A '_type: location' message is one point; other message types (waypoints, transitions, status) are none."""
        if not isinstance(body, dict) or body.get("_type") != "location" or not {"lat", "lon", "tst"} <= body.keys():
            return []
        acc = body.get("acc")
        return [LocationPoint(datetime.fromtimestamp(int(body["tst"]), timezone.utc), float(body["lat"]), float(body["lon"]),
                              str(body.get("tid") or body.get("topic") or "phone")[:40],
                              float(acc) if isinstance(acc, (int, float)) and not isinstance(acc, bool) else None)]

    def reply(self) -> Any:
        return []  # it expects a JSON array (friends' locations)

    def setup(self, url: str, token: str, words: Language) -> AppSetup:
        """A link that configures the app on the phone (HTTP mode, this server, the ingest token as password), so
        nobody types the token; and the same by hand."""
        cfg = {"_type": "configuration", "mode": 3, "url": url, "auth": True, "username": "muninn", "password": token, "tid": "me",
               "monitoring": 1,  # significant changes: low battery; switch to Move in the app for denser tracks
               "locatorDisplacement": 100, "locatorInterval": 300, "allowRemoteLocation": False}
        inline = quote(base64.b64encode(json.dumps(cfg).encode()).decode(), safe="")  # a raw '+' would arrive as a space
        return AppSetup(f"owntracks:///config?inline={inline}", [("URL", url), ("UserID", "muninn"), ("Password", token)],
                        words("owntracks.setup_note"))
