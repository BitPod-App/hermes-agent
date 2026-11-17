"""A held chat turn whose session row already ended is wedged (BitPod BIT-1768).

On 2026-10-24 six webhook PM turns ended ``webhook_complete`` in state.db yet stayed in the
gateway's running map for ~5 days and held the update drain: their activity clocks never crossed
``agent.gateway_timeout`` as read, so the restart wait counted them as live work.
"""

from unittest.mock import MagicMock

from tests.gateway.restart_test_helpers import make_restart_runner


def _agent(session_id, idle):
    agent = MagicMock()
    agent.session_id = session_id
    if idle is None:
        agent.get_activity_summary = MagicMock(side_effect=RuntimeError("no clock"))
    else:
        agent.get_activity_summary = MagicMock(return_value={"seconds_since_activity": idle})
    return agent


class _Db:
    def __init__(self, ended):
        self.ended = ended

    def get_session_list_row(self, session_id):
        return {"id": session_id, "ended_at": 1.0 if session_id in self.ended else None}


def _runner(monkeypatch, ended=()):
    monkeypatch.delenv("HERMES_AGENT_TIMEOUT", raising=False)
    runner, _adapter = make_restart_runner()
    runner._session_db = _Db(set(ended))
    return runner


def test_ended_session_with_no_activity_clock_is_wedged(monkeypatch):
    runner = _runner(monkeypatch, ended={"s-done"})
    runner._running_agents["agent:main:webhook:pm"] = _agent("s-done", None)
    assert runner._wedged_agent_count() == 1


def test_ended_session_idle_past_floor_is_wedged(monkeypatch):
    runner = _runner(monkeypatch, ended={"s-done"})
    runner._running_agents["k"] = _agent("s-done", runner._ENDED_SESSION_IDLE_FLOOR_S + 1)
    assert runner._wedged_agent_count() == 1


def test_ended_session_still_active_is_not_wedged(monkeypatch):
    """A turn that just rotated its session (compression) is still working."""
    runner = _runner(monkeypatch, ended={"s-rotated"})
    runner._running_agents["k"] = _agent("s-rotated", 5.0)
    assert runner._wedged_agent_count() == 0


def test_open_session_is_judged_by_gateway_timeout_only(monkeypatch):
    runner = _runner(monkeypatch)
    runner._running_agents.update({"quiet": _agent("s-open", None), "busy": _agent("s-open2", 600.0)})
    assert runner._wedged_agent_count() == 0


def test_ended_sessions_apply_even_with_timeout_disabled(monkeypatch):
    runner = _runner(monkeypatch, ended={"s-done"})
    monkeypatch.setenv("HERMES_AGENT_TIMEOUT", "0")
    runner._running_agents.update({"done": _agent("s-done", None), "open": _agent("s-open", 10**6)})
    assert runner._wedged_agent_count() == 1


def test_unreadable_session_db_means_not_wedged(monkeypatch):
    runner = _runner(monkeypatch)
    runner._session_db = None
    runner._running_agents["k"] = _agent("s-done", None)
    assert runner._wedged_agent_count() == 0
