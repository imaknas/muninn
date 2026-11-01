"""Codex sessions read beside Claude Code's: the user's own time and conversation, never another program's."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from muninn.adapters.sources.agent_activity import AgentActivitySource
from muninn.adapters.sources.conversations import TranscriptTextSource

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def ts(minutes: int) -> str:
    return (NOW - timedelta(hours=1) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def meta(sid, cwd, source="cli", originator="codex_cli_rs", **more):
    return {"timestamp": ts(0), "type": "session_meta", "ordinal": 0,
            "payload": {"id": sid, "session_id": sid, "timestamp": ts(0), "cwd": cwd, "originator": originator,
                        "cli_version": "0.165.0", "source": source, **more}}


def said(minute, role, text, kinds=None, phase=None, n=0):
    content_type = "input_text" if role == "user" else "output_text"
    payload = {"type": "message", "id": f"m{minute}-{n}", "role": role, "content": [{"type": content_type, "text": text}]}
    if kinds:
        payload["internal_chat_message_metadata_passthrough"] = {"content_item_kinds": kinds}
    if phase:
        payload["phase"] = phase
    return {"timestamp": ts(minute), "type": "response_item", "ordinal": minute, "payload": payload}


def tool(minute):
    return {"timestamp": ts(minute), "type": "response_item", "ordinal": minute,
            "payload": {"type": "custom_tool_call", "id": f"t{minute}", "call_id": "c", "name": "shell", "input": "ls"}}


def moved(minute, cwd):
    return {"timestamp": ts(minute), "type": "turn_context", "ordinal": minute, "payload": {"cwd": cwd, "model": "m"}}


def write(path, lines, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode) as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def setup(tmp_path):
    ws = tmp_path / "work"
    for repo in ("garden", "kitchen"):
        (ws / repo).mkdir(parents=True)
    return ws, tmp_path / "codex" / "sessions" / "2026" / "10" / "22"


def test_a_session_the_user_typed_into_counts_as_their_time_and_codexs_own_context_does_not(tmp_path):
    ws, day = setup(tmp_path)
    write(day / "rollout-a.jsonl", [
        meta("s-own", str(ws / "garden")),
        said(1, "user", "<environment_context><cwd>/x</cwd></environment_context>", ["environments.environment_context"]),
        said(2, "user", "make the planter list sortable", ["user.text"]),
        tool(3), said(4, "assistant", "Done: sorted by size.", phase="final_answer"),
        tool(40),  # long after the last prompt: the agent on its own
    ])
    obs, _ = AgentActivitySource([tmp_path / "codex"], [ws]).collect(None, NOW)
    assert {o.kind for o in obs} == {"agent_activity"}
    assert sum(o.payload["events"] for o in obs) == 3 and sum(o.payload["human_turns"] for o in obs) == 1
    assert {(o.payload["repo"], o.payload["agent"]) for o in obs} == {("garden", "codex")}
    assert all(o.key.startswith("codex:s-own:") for o in obs)


def test_the_interactive_command_line_counts_as_codex_0_165_records_it(tmp_path):
    ws, day = setup(tmp_path)  # what a real session in the terminal said of itself: source vscode, originator codex-tui
    write(day / "rollout-h.jsonl", [meta("s-tui", str(ws / "garden"), source="vscode", originator="codex-tui", thread_source="user"),
                                    said(1, "user", "hello?", ["user.text"]), said(2, "assistant", "hello", ["unknown"], phase="final_answer")])
    obs, _ = AgentActivitySource([tmp_path / "codex"], [ws]).collect(None, NOW)
    assert sum(o.payload["human_turns"] for o in obs) == 1


def test_sessions_another_program_or_a_sub_agent_started_are_not_the_users(tmp_path):
    ws, day = setup(tmp_path)
    write(day / "rollout-b.jsonl", [meta("s-hub", str(ws / "garden"), source="vscode", originator="agent-hub"),
                                    said(2, "user", "run the nightly check", ["user.text"]), tool(3)])
    write(day / "rollout-c.jsonl", [meta("s-exec", str(ws / "garden"), source="exec"), said(2, "user", "go", ["user.text"]), tool(3)])
    write(day / "rollout-d.jsonl", [meta("s-sub", str(ws / "garden"), source={"subagent": "review"}, thread_source="subagent"),
                                    said(2, "user", "review this", ["user.text"]), tool(3)])
    obs, _ = AgentActivitySource([tmp_path / "codex"], [ws]).collect(None, NOW)
    assert obs == []
    turns, _ = TranscriptTextSource([tmp_path / "codex"], [ws]).collect(None, NOW)
    assert turns == []


def test_a_session_read_in_two_goes_keeps_its_session_and_the_folder_it_moved_to(tmp_path):
    ws, day = setup(tmp_path)
    f = day / "rollout-e.jsonl"
    write(f, [meta("s-moves", str(ws / "garden")), said(1, "user", "start here", ["user.text"]), moved(2, str(ws / "kitchen"))])
    src = AgentActivitySource([tmp_path / "codex"], [ws])
    _, cursor = src.collect(None, NOW)
    write(f, [said(3, "user", "now the oven timer", ["user.text"]), tool(4)], mode="a")
    obs, _ = src.collect(cursor, NOW)
    assert {o.payload["repo"] for o in obs} == {"kitchen"} and {o.payload["session"] for o in obs} == {"s-moves"}


def test_the_conversation_keeps_what_the_user_typed_and_the_answers_not_the_narration(tmp_path):
    ws, day = setup(tmp_path)
    write(day / "rollout-f.jsonl", [
        meta("s-talk", str(ws / "garden")),
        said(1, "user", "<user_instructions>be brief</user_instructions>"),  # an older Codex: no item kinds, a tag
        said(2, "user", "which pots drain best?", ["user.text"]),
        said(3, "assistant", "Let me check the notes first.", phase="commentary"),
        tool(4),
        said(5, "assistant", "Terracotta drains best.", phase="final_answer"),
    ])
    turns, _ = TranscriptTextSource([tmp_path / "codex"], [ws]).collect(None, NOW)
    assert [(t.role, t.text, t.project) for t in turns] == [("user", "which pots drain best?", "garden"),
                                                            ("assistant", "Terracotta drains best.", "garden")]


def test_claude_code_and_codex_sessions_are_read_side_by_side(tmp_path):
    ws, day = setup(tmp_path)
    write(day / "rollout-g.jsonl", [meta("s-codex", str(ws / "garden")), said(2, "user", "hi", ["user.text"])])
    write(tmp_path / "claude" / "p" / "s.jsonl", [{"type": "user", "sessionId": "s-claude", "cwd": str(ws / "kitchen"),
                                                   "timestamp": ts(2), "turnOrigin": "human", "uuid": "u1",
                                                   "message": {"content": "hello"}}])
    obs, _ = AgentActivitySource([tmp_path / "codex", tmp_path / "claude"], [ws]).collect(None, NOW)
    assert {(o.kind, o.payload["repo"]) for o in obs} == {("agent_activity", "garden"), ("claude_activity", "kitchen")}
