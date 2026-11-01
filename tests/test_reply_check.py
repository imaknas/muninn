"""A JSON reply held to its decision point's declared shape, and asked for once more when it does not keep to it."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import RecordingLLM

from muninn.adapters.llm.reply_check import ReplyCheck, schemas
from muninn.core.llm_router import CloudFirst, LLMRequest, LLMRouter
from muninn.core.models import Sensitivity
from muninn.package import CONTRACTS

CONTRACTS = CONTRACTS
OK = json.dumps({"actions": [], "reply": "好。"}, ensure_ascii=False)
WRONG = json.dumps({"actions": [], "reply": "好。", "topic": {"title": "牙醫", "state": "someday"}}, ensure_ascii=False)


def test_a_reply_is_checked_against_its_points_schema():
    check = ReplyCheck(schemas(CONTRACTS))
    assert check("route", OK) == [] and check("route", f"<think>{{maybe}}</think>\n{OK}") == []  # thinking set aside
    assert check("route", WRONG) and "topic/state" in check("route", WRONG)[0]
    assert check("route", "好的") == ["no JSON object in the reply"]
    assert check("no_such_point", "anything") == []


class Says(RecordingLLM):
    def __init__(self, *replies):
        super().__init__("claude-cli:haiku", False)
        self.replies = list(replies)

    def complete(self, system, prompt, *, json_mode=False):
        self.calls.append((system, prompt))
        return self.replies.pop(0)


def ask(model, json_mode=True):
    router = LLMRouter([model], [], CloudFirst(), cloud_for=lambda task: [model], check_reply=ReplyCheck(schemas(CONTRACTS)))
    return router.complete(LLMRequest("s", "明天看牙醫", Sensitivity.SENSITIVE, None, json_mode=json_mode, task="route"))


def test_a_reply_off_its_shape_is_asked_for_once_more_with_what_was_wrong():
    model = Says(WRONG, OK)
    assert ask(model) == OK
    assert len(model.calls) == 2 and "topic/state" in model.calls[1][1] and model.calls[1][1].startswith("明天看牙醫")


def test_when_the_second_reply_misses_too_the_first_goes_back_for_the_caller_to_read():
    model = Says(WRONG, "還是不對")
    assert ask(model) == WRONG and len(model.calls) == 2


def test_a_reply_in_shape_or_a_plain_text_request_is_asked_once():
    model = Says(OK)
    assert ask(model) == OK and len(model.calls) == 1
    model = Says("今天好累")
    assert ask(model, json_mode=False) == "今天好累" and len(model.calls) == 1


def test_every_declared_schema_loads():
    from ruamel.yaml import YAML

    points = YAML(typ="safe").load((CONTRACTS / "llm-points.yaml").read_text(encoding="utf-8"))["points"]
    assert set(schemas(CONTRACTS)) == {p for p, v in points.items() if v["output"].get("schema")}
