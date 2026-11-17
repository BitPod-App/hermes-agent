"""Malformed tool-call JSON stays recoverable, but cannot loop forever (BIT-1334)."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermes_cli import oneshot
from hermes_state import SessionDB
from run_agent import AIAgent


def _tool_call(arguments: str, index: int):
    return SimpleNamespace(
        id=f"call-{index}",
        type="function",
        function=SimpleNamespace(name="web_search", arguments=arguments),
    )


def _response(*, arguments: str | None = None, content: str = ""):
    tool_calls = None if arguments is None else [_tool_call(arguments, id(arguments))]
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=message,
            finish_reason="stop" if tool_calls is None else "tool_calls",
        )],
        model="test/model",
        usage=None,
    )


def _agent(*responses, session_db=None):
    tool_defs = [{
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "search",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
    with (
        patch("model_tools.get_tool_definitions", return_value=tool_defs),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI"),
    ):
        agent = AIAgent(
            api_key="placeholder",
            base_url="https://example.invalid/v1",
            max_iterations=20,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            session_db=session_db,
        )
    agent.client = MagicMock()
    agent.client.chat.completions.create.side_effect = responses
    if session_db is None:
        agent._persist_session = MagicMock()
    agent._save_trajectory = MagicMock()
    agent._cleanup_task_resources = MagicMock()
    return agent


def test_repeated_malformed_arguments_stop_after_five_model_calls_as_failure():
    malformed = '{"query": nope}'
    agent = _agent(*[_response(arguments=malformed) for _ in range(8)])

    result = agent.run_conversation("search")

    assert result["api_calls"] == 5
    assert result["completed"] is False
    assert result["failed"] is True
    assert result["partial"] is True
    assert result["turn_exit_reason"] == "invalid_tool_json_exhausted"
    assert "invalid JSON" in result["final_response"]
    assert agent.client.chat.completions.create.call_count == 5
    assert agent._persist_session.call_count >= 1


def test_varied_malformed_arguments_still_stop_after_five_model_calls():
    agent = _agent(*[
        _response(arguments=f'{{"query": malformed_{index}}}')
        for index in range(8)
    ])

    result = agent.run_conversation("search")

    assert result["api_calls"] == 5
    assert result["completed"] is False
    assert result["failed"] is True
    assert result["failure_reason"] == "invalid_tool_json"
    assert result["turn_exit_reason"] == "invalid_tool_json_exhausted"
    assert agent.client.chat.completions.create.call_count == 5


def test_distinct_malformed_responses_can_recover_to_valid_tool_work():
    agent = _agent(
        _response(arguments='{"query": nope}'),
        _response(arguments='{"query": different}'),
        _response(arguments='{"query":"fixed"}'),
        _response(content="done"),
    )

    with patch("model_tools.handle_function_call", return_value='{"results":[]}') as dispatch:
        result = agent.run_conversation("search")

    assert result["completed"] is True
    assert result["failed"] is False
    assert result["final_response"] == "done"
    assert result["api_calls"] == 4
    dispatch.assert_called_once()


def test_oneshot_persists_one_failed_session_and_returns_nonzero(tmp_path):
    # Upstream 936f3465f8 removed the in-process one-shot runner (`hermes -z` runs through the
    # gateway). Drive the agent natively and judge the result with the surviving one-shot
    # helpers: the exit-code mapping and the --usage-file ledger.
    db_path = tmp_path / "state.db"
    usage_path = tmp_path / "usage.json"
    session_db = SessionDB(db_path=db_path)
    malformed = '{"query": nope}'
    agent = _agent(
        *[_response(arguments=malformed) for _ in range(8)],
        session_db=session_db,
    )
    try:
        result = agent.run_conversation("search")
    finally:
        agent.close()
        session_db.close()

    assert oneshot._oneshot_exit_code(result["final_response"], result) == 2
    assert "invalid JSON" in (result["final_response"] or "")
    oneshot._write_usage_file(str(usage_path), result)
    report = json.loads(usage_path.read_text())
    assert report["failed"] is True
    assert report["completed"] is False
    assert report["failure_reason"] == "invalid_tool_json"
    assert report["turn_exit_reason"] == "invalid_tool_json_exhausted"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
