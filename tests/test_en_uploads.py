"""What an English Muninn says about the files and the setups it takes in: uploads refused (adapters/uploads.py),
the Mac collector (collectors/) and OwnTracks (adapters/sources/location/owntracks.py) on the settings page."""

from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from conftest import at, make_harness
from muninn.adapters.channels.webpush import VapidKeys
from muninn.adapters.uploads import FileUploads
from muninn.adapters.web.app import create_app
from muninn.adapters.web.devices import DeviceRegistry
from muninn.languages import language
from muninn.main import LOCATION_APPS

HAN = re.compile(r"[一-鿿]")


def test_a_file_not_taken_is_refused_in_english(tmp_path):
    up = FileUploads(tmp_path / "uploads", words=language("en"))
    for media_type, data, says in (("application/zip", b"PK", "Only photos (JPEG, PNG, GIF, WebP), PDFs and plain text files"),
                                   ("text/plain", b"", "The file is empty"),
                                   ("text/plain", b"x" * (10 * 1024 * 1024 + 1), "The file is too big (10 MB at most)")):
        with pytest.raises(ValueError) as e:
            up.save("a", media_type, data)
        assert str(e.value) == says


def test_the_collector_and_the_tracking_app_are_set_up_in_english(tmp_path):
    h = make_harness(at(2026, 11, 3, 10), lang="en")
    keys = VapidKeys(tmp_path / "secrets" / "vapid.pem")
    keys.ensure()
    devices = DeviceRegistry(tmp_path / "dev.db")
    client = TestClient(create_app(h.engine, keys, "tok", devices, location_apps=LOCATION_APPS), base_url="https://testserver")
    client.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "phone"})
    [mac] = client.get("/api/setup/activity").json()["collectors"]
    assert mac["label"] == "Mac (ActivityWatch)" and mac["note"].startswith("The window in front") and HAN.search(mac["note"]) is None
    [ot] = client.get("/api/setup/location").json()["apps"]
    assert ot["note"].startswith("OwnTracks → Settings: set Mode to HTTP") and HAN.search(ot["note"]) is None
