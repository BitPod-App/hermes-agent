"""``agent.stop_hook_without_edits`` (BitPod BIT-1768): ``pre_verify`` may also hold a text-ending
turn that edited nothing, and receives this turn's rows as ``turn_messages``."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import turn_stop_gates, verify_hooks


def test_config_defaults_off_and_reads_truthy():
    assert verify_hooks.stop_hook_without_edits({}) is False
    assert verify_hooks.stop_hook_without_edits({"agent": {"stop_hook_without_edits": True}}) is True
    assert verify_hooks.stop_hook_without_edits({"agent": {"stop_hook_without_edits": "false"}}) is False


def test_turn_messages_start_at_last_real_user_row():
    rows = [
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "/qa-review new"},
        {"role": "assistant", "content": "prose"},
        {"role": "user", "content": "nudge", "_pre_verify_synthetic": True},
        {"role": "assistant", "content": "still prose"},
    ]
    assert turn_stop_gates._turn_messages(rows) == rows[2:]


@pytest.fixture
def hooks(monkeypatch):
    calls = []

    def fake_continue(**kwargs):
        calls.append(kwargs)
        return "publish a verdict"

    monkeypatch.setattr("hermes_cli.lifecycle.has_hook", lambda name: name == "pre_verify")
    monkeypatch.setattr("hermes_cli.plugins.get_pre_verify_continue_message", fake_continue)
    monkeypatch.setattr("agent.verify_hooks.max_verify_nudges", lambda config=None: 1)
    return calls


def _agent(edited=()):
    return SimpleNamespace(_turn_file_mutation_paths=set(edited), _resolved_is_coding=False,
                           session_id="s1", platform="buzz", model="m")


def test_no_edits_and_option_off_does_not_fire(monkeypatch, hooks):
    monkeypatch.setattr("agent.verify_hooks.stop_hook_without_edits", lambda config=None: False)
    assert turn_stop_gates._pre_verify_nudge(_agent(), "prose", 0, []) is None
    assert hooks == []


def test_no_edits_and_option_on_fires_with_turn_messages(monkeypatch, hooks):
    monkeypatch.setattr("agent.verify_hooks.stop_hook_without_edits", lambda config=None: True)
    rows = [{"role": "user", "content": "/qa-review x"}, {"role": "assistant", "content": "prose"}]
    assert turn_stop_gates._pre_verify_nudge(_agent(), "prose", 0, rows) == "publish a verdict"
    assert hooks[0]["turn_messages"] == rows and hooks[0]["changed_paths"] == []


def test_edits_still_fire_with_option_off(monkeypatch, hooks):
    monkeypatch.setattr("agent.verify_hooks.stop_hook_without_edits", lambda config=None: False)
    assert turn_stop_gates._pre_verify_nudge(_agent(["a.py"]), "done", 0, []) == "publish a verdict"


def test_budget_still_bounds(monkeypatch, hooks):
    monkeypatch.setattr("agent.verify_hooks.stop_hook_without_edits", lambda config=None: True)
    assert turn_stop_gates._pre_verify_nudge(_agent(), "prose", 1, []) is None
