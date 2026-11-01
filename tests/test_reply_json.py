"""Reading the JSON object out of a model's reply, the one way every caller and the shape check read it."""

import pytest

from muninn.core.reply_json import json_object, read_object


def test_the_object_is_read_out_of_prose_around_it():
    assert json_object('Here you go:\n```json\n{"a": {"b": 1}}\n```\nDone.') == {"a": {"b": 1}}


def test_what_a_reasoning_model_thinks_first_is_left_out_even_with_braces_in_it():
    assert json_object('<think>maybe {"a": 0}? no</think>{"a": 1}') == {"a": 1}


@pytest.mark.parametrize("reply", [None, "", "no braces here", "[1, 2]", '{"a": 1', "{not json}", '} backwards {'])
def test_no_object_to_read_is_none(reply):
    assert json_object(reply) is None


@pytest.mark.parametrize("reply, why", [("nothing", "no JSON object"), ("{oops}", "not valid JSON")])
def test_the_check_is_told_what_is_wrong(reply, why):
    with pytest.raises(ValueError, match=why):
        read_object(reply)
