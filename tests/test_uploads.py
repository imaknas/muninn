"""Files the user sends with what they say: kept here at once, shown again, seen by the model that answers
(only a backend that reads files gets the request), and handed to a deep talk's session as they are."""

from __future__ import annotations

import json

import pytest

from muninn.adapters.uploads import FileUploads
from muninn.core.llm_router import CloudFirst, LLMRequest, LLMRouter
from muninn.core.models import Attachment, Sensitivity
from muninn.core.ports import Capabilities

from conftest import ZH, RecordingLLM, at
from test_lease import FakeRunner

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # stands in for a photo


def test_a_file_is_kept_and_read_back_and_what_is_not_taken_is_refused(tmp_path):
    up = FileUploads(tmp_path / "uploads", words=ZH)
    meta = up.save("../../客廳 1.png", "image/png", PNG)
    assert meta["media_type"] == "image/png" and meta["size"] == len(PNG) and "/" not in meta["name"]
    got = up.get(meta["id"])
    assert got == Attachment(meta["name"], "image/png", PNG, meta["id"]) and got.is_image
    for name, kind, data, why in (("a.exe", "application/x-msdownload", b"MZ", "只收"), ("e.png", "image/png", b"", "空的"),
                                  ("big.png", "image/png", b"x" * (10 * 1024 * 1024 + 1), "太大")):
        with pytest.raises(ValueError, match=why):
            up.save(name, kind, data)
    assert up.get("../etc/passwd") is None and up.get("0" * 16) is None


class Reads(RecordingLLM):
    def complete(self, system, prompt, *, json_mode=False, attachments=()):
        self.seen = [a.name for a in attachments]
        return super().complete(system, prompt, json_mode=json_mode)


def test_only_a_model_that_reads_files_answers_a_request_with_them():
    blind, reads = RecordingLLM("claude-cli:haiku", False), Reads("claude-cli:sonnet", False, capabilities=Capabilities(images=True, pdf=True))
    photo = (Attachment("room.jpg", "image/jpeg", b"jpg"),)
    router = LLMRouter([blind, reads], [], CloudFirst(), cloud_for=lambda task: [blind, reads])
    c = router.complete_case(LLMRequest("s", "c", Sensitivity.SENSITIVE, None, task="route", attachments=photo))
    assert c.backend == "claude-cli:sonnet" and reads.seen == ["room.jpg"] and blind.calls == []
    assert router.complete_case(LLMRequest("s", "c", Sensitivity.SENSITIVE, None, task="route")).backend == "claude-cli:haiku"


@pytest.fixture
def web(tmp_path):
    from starlette.testclient import TestClient

    from muninn.adapters.channels.webpush import VapidKeys
    from muninn.adapters.web.app import create_app
    from muninn.adapters.web.devices import DeviceRegistry
    from muninn.core.lease import ChannelLease
    from conftest import make_harness

    h = make_harness(at(2026, 10, 25, 10))
    runner = FakeRunner()
    h.engine.leases = ChannelLease(h.store, h.clock, runner, h.channel, lambda now: False,
                                   lambda tid: h.engine.conversation().thread(tid), lambda tid, change: h.engine.conversation().update_thread(tid, change), words=ZH)
    keys = VapidKeys(tmp_path / "secrets" / "vapid.pem")
    keys.ensure()
    devices = DeviceRegistry(tmp_path / "dev.db")
    client = TestClient(create_app(h.engine, keys, "tok", devices, lease_runner=runner, uploads=FileUploads(tmp_path / "up", words=h.engine.words)),
                        base_url="https://testserver")
    assert client.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "iPhone"}).status_code == 200
    return h, runner, client


def put(client, name="客廳.png", kind="image/png", data=PNG, header=True):
    return client.post(f"/api/uploads?name={name}", content=data,
                       headers={"Content-Type": kind, **({"X-Muninn-Upload": "1"} if header else {})})


def test_a_photo_goes_up_at_once_and_comes_back_to_be_shown(web):
    _, _, client = web
    assert put(client, header=False).status_code == 400  # only this page's own uploads
    meta = put(client).json()
    got = client.get(f"/api/uploads/{meta['id']}")
    assert got.status_code == 200 and got.content == PNG and got.headers["content-type"] == "image/png"
    assert put(client, "a.exe", "application/octet-stream", b"MZ").status_code == 400


def test_a_photo_sent_to_muninn_is_seen_by_the_model_and_kept_on_the_turn(web):
    h, _, client = web
    seen = []

    def complete(system, prompt, *, json_mode=False):
        seen.append(prompt)
        return json.dumps({"actions": [], "reply": "看到了,是客廳。"}, ensure_ascii=False)

    h.local.complete = complete
    meta = put(client).json()
    r = client.post("/api/command", json={"text": "這是哪裡?", "attachments": [meta["id"]]}).json()
    # the harness's local model reads no photos: nothing answers rather than guess past the photo, and it says why
    assert "讀不了照片" in r["reply"] and seen == []
    t = h.engine.conversation().thread(r["thread"])
    assert t["turns"][0]["files"] == [{"id": meta["id"], "name": meta["name"], "media_type": "image/png"}]
    feed = client.get("/api/conversation").json()["items"]
    assert feed[-1]["turns"][0]["files"][0]["id"] == meta["id"]
    assert client.post("/api/command", json={"text": "再看", "attachments": ["0" * 16]}).status_code == 400


def test_a_photo_sent_in_a_deep_talk_goes_to_the_session_with_what_was_said(web):
    h, runner, client = web
    got = client.post("/api/command", json={"text": "/attach garden"}).json()
    meta = put(client).json()
    h.local.reply = '{"for_muninn": null}'
    client.post("/api/command", json={"text": "", "thread": got["thread"], "attachments": [meta["id"]]})
    assert runner.calls[-1] == ("send", f"(傳了 {meta['name']})") and runner.sent_files == [meta["name"]]
    client.post("/api/command", json={"text": "這張的牆長多少?", "thread": got["thread"], "attachments": [meta["id"]]})
    assert runner.calls[-1] == ("send", "這張的牆長多少?")
    turns = [(x["text"], [f["id"] for f in x.get("files", [])]) for x in h.store.lease_turns(got["lease_id"]) if x["role"] == "user"]
    assert turns == [("", [meta["id"]]), ("這張的牆長多少?", [meta["id"]])]  # his words as he said them, the photo itself kept
    view = client.get(f"/api/leases?thread={got['thread']}").json()["lease"]["turns"]
    assert [t["files"][0]["id"] for t in view if t["role"] == "user"] == [meta["id"], meta["id"]]  # shown again after a reload


def test_handing_a_conversation_over_takes_the_photos_just_sent(web):
    h, runner, client = web
    runner.sandboxes = ["idea-1"]
    photo = Attachment("room.jpg", "image/jpeg", b"jpg", "a" * 16)
    h.store.save_thread("t1", {"about": None, "ts": "x", "turns": [{"role": "user", "text": "這是房間照片", "ts": "x", "cloud_ok": True}]})
    got = h.engine.leases.lend("t1", "garden", [photo])
    assert got["opened"] and runner.calls[-1] == ("send", "這是房間照片") and runner.sent_files == ["room.jpg"]
    h.store.save_thread("t2", {"about": None, "ts": "x", "turns": [{"role": "user", "text": "公司的照片", "ts": "x"}]})
    h.engine.leases.lend("t2", "garden", [photo])
    assert runner.calls[-1] == ("send", "他傳了這些檔案給你。")  # words not cleared stay here; the files go as sent


def test_where_the_session_kept_the_photos_is_said_beside_the_talk(web):
    from test_lease import ev

    h, _, client = web
    got = client.post("/api/command", json={"text": "/attach garden"}).json()
    h.engine.leases.on_event(ev(got["lease_id"], 1, "attachments", paths=["attachments/20261025-101500-room.jpg"]))
    last = h.store.lease_turns(got["lease_id"])[-1]
    assert (last["text"], last["detail"]) == ("存進 garden(1 個檔)", "attachments/20261025-101500-room.jpg")  # the paths fold away


def test_photos_sent_to_muninn_with_words_naming_a_project_go_on_to_that_session(web):
    h, runner, client = web
    h.local.capabilities = Capabilities(images=True)  # a model that sees photos, as the cloud one does
    h.local.complete = lambda system, prompt, *, json_mode=False, attachments=(): json.dumps(
        {"actions": [{"tool": "talk_to_project", "args": {"project": "garden"}}], "reply": "交給 garden。"}, ensure_ascii=False)
    h.engine.leases._share_uncleared = True  # everything goes to the cloud, as routing "cloud" has it
    a, b = put(client, "前院.png").json(), put(client, "後院.png").json()
    r = client.post("/api/command", json={"text": "這兩張給 garden 看,討論排水坡度", "attachments": [a["id"], b["id"]]}).json()
    assert r["lent"]["project"] == "garden"
    assert runner.calls[-1] == ("send", "這兩張給 garden 看,討論排水坡度") and runner.sent_files == ["前院.png", "後院.png"]


def test_each_kind_of_file_goes_only_to_a_model_that_reads_that_kind():
    sees = RecordingLLM("claude-cli:haiku", False, capabilities=Capabilities(images=True))
    router = LLMRouter([sees], [], CloudFirst(), cloud_for=lambda task: [sees])
    pdf = (Attachment("lease.pdf", "application/pdf", b"%PDF"),)
    c = router.complete_case(LLMRequest("s", "c", Sensitivity.SENSITIVE, None, task="route", attachments=pdf))
    assert c.refused == "capability" and c.case is None and sees.calls == []  # photos yes, PDFs no: not asked


def test_a_text_file_is_read_as_text_by_any_model():
    blind = RecordingLLM("ollama:qwen3:8b", True)
    router = LLMRouter([blind], [])
    notes = (Attachment("notes.txt", "text/plain", "排水管要挖 30 公分".encode()),)
    c = router.complete_case(LLMRequest("s", "這是什麼?", Sensitivity.SENSITIVE, None, task="route", attachments=notes))
    assert c.backend == "ollama:qwen3:8b" and blind.calls == [("s", "--- notes.txt ---\n排水管要挖 30 公分\n\n這是什麼?")]


def test_a_photo_only_the_local_model_may_see_and_cannot_is_said_to_be_the_privacy_settings_doing():
    local = RecordingLLM("ollama:qwen3:8b", True)
    cloud = RecordingLLM("claude-cli:sonnet", False, capabilities=Capabilities(images=True, pdf=True))
    router = LLMRouter([local, cloud], [])  # privacy first: sensitive content stays local
    photo = (Attachment("room.jpg", "image/jpeg", b"jpg"),)
    c = router.complete_case(LLMRequest("s", "c", Sensitivity.SENSITIVE, None, task="route", attachments=photo))
    assert c.refused == "routing" and local.calls == [] and cloud.calls == []


def test_the_local_model_check_says_what_is_missing(monkeypatch):
    import httpx

    from muninn.adapters.llm.ollama import OllamaBackend

    b = OllamaBackend("qwen3:8b", words=ZH)
    monkeypatch.setattr(httpx, "get", lambda url, timeout: httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]}))
    assert b.check() == "reachable"
    monkeypatch.setattr(httpx, "get", lambda url, timeout: httpx.Response(200, json={"models": []}))
    with pytest.raises(RuntimeError, match="ollama pull qwen3:8b"):
        b.check()

    def down(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", down)
    with pytest.raises(RuntimeError, match="not reachable"):
        b.check()


def test_a_credential_pasted_in_is_masked_before_a_cloud_model_reads_it_and_the_local_one_sees_it_as_it_is():
    cloud, local = RecordingLLM("claude-cli:haiku", False), RecordingLLM("ollama:qwen3:8b", True)
    said = "把這個 key 存起來 ghp_" + "a1B2" * 9 + " 還有訂單 A1234567890 跟驗證碼 482913"
    LLMRouter([cloud], [], CloudFirst(), cloud_for=lambda task: [cloud]).complete(
        LLMRequest("s", said, Sensitivity.SENSITIVE, None, task="route"))
    assert cloud.calls[0][1] == "把這個 key 存起來 [masked GitHub token] 還有訂單 A1234567890 跟驗證碼 482913"
    LLMRouter([local], []).complete(LLMRequest("s", said, Sensitivity.SENSITIVE, None, task="route"))
    assert local.calls[0][1] == said
