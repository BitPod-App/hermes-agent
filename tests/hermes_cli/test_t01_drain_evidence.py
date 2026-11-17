"""T01 BIT-1901: the update drain waits on evidence of in-flight work, never on a timer.

On 2026-11-08 the updater mapped every launchd gateway to the sticky active profile's control
socket, two idle gateways were never asked to drain, and the update waited out its whole
~34 min budget for them.
"""
from __future__ import annotations

import json
import plistlib
import threading
import time

import pytest

from hermes_cli import update_cmd_posix_pause as m

BUDGET = 3.0


@pytest.fixture
def harness(tmp_path, monkeypatch):
    stopped: dict[int, float] = {}
    alive: set[int] = set()
    acks: dict[int, dict | None] = {}
    asks: dict[int, int] = {}

    def entry(pid: int, ack: dict | None, *, state: dict | None = None) -> dict:
        home = tmp_path / f"p{pid}"
        home.mkdir()
        if state is not None:
            set_state(pid, state)
        alive.add(pid)
        acks[pid] = ack
        return {"kind": "launchd", "label": f"ai.hermes.gateway-p{pid}", "pid": pid, "home": str(home)}

    def set_state(pid: int, state: dict) -> None:
        (tmp_path / f"p{pid}" / "gateway_state.json").write_text(json.dumps({"pid": pid, **state}))

    def ask(home):
        pid = int(home.name[1:])
        asks[pid] = asks.get(pid, 0) + 1
        return acks[pid]

    started = time.monotonic()

    def stop_at_once(e: dict) -> None:
        stopped[e["pid"]] = time.monotonic() - started
        alive.discard(e["pid"])

    import gateway.control_socket as cs

    monkeypatch.setattr(cs, "pause_gateway_for_update", ask)
    monkeypatch.setattr(m, "_stop_at_once", stop_at_once)
    monkeypatch.setattr(m, "_disarm", lambda e: None)
    monkeypatch.setattr(m, "_alive", lambda pid, ct: pid in alive)
    monkeypatch.setattr(m, "_drain_budget", lambda: BUDGET)
    monkeypatch.setattr("hermes_cli.update_pause_record.mark_stop_sent", lambda token, pid: None)
    monkeypatch.setattr("hermes_cli.update_cmd_drain_report.drain_progress_reporter", lambda *a, **k: (lambda: None))
    return {"entry": entry, "stopped": stopped, "acks": acks, "asks": asks, "set_state": set_state}


def _later(seconds: float, fn) -> threading.Timer:
    t = threading.Timer(seconds, fn)
    t.start()
    return t


def test_an_idle_gateway_is_stopped_at_once_not_waited_for(harness):
    e = harness["entry"](101, {"pausing": True, "already_stopping": False, "not_draining": False, "awaitable_work": 0})
    m._stop_gateways({}, [e])
    assert harness["stopped"][101] < 0.5


def test_a_false_already_stopping_ack_from_an_idle_gateway_does_not_hold_the_update(harness):
    """A pre-patch gateway answers already_stopping (no count) but never drains."""
    e = harness["entry"](102, {"pausing": False, "already_stopping": True}, state={"active_agents": 0, "active_work": None})
    m._stop_gateways({}, [e])
    assert harness["stopped"][102] < 0.5


def test_a_not_draining_answer_with_idle_state_is_stopped_at_once(harness):
    e = harness["entry"](103, {"pausing": False, "already_stopping": False, "not_draining": True, "awaitable_work": None},
                         state={"active_agents": 0})
    m._stop_gateways({}, [e])
    assert harness["stopped"][103] < 0.5


def test_a_live_turn_is_waited_for_and_stopped_as_soon_as_the_gateway_says_it_ended(harness):
    e = harness["entry"](104, {"pausing": True, "not_draining": False, "awaitable_work": 1},
                         state={"active_agents": 1, "active_work": [{"kind": "chat", "session": "s"}]})
    t = _later(1.2, lambda: harness["acks"].__setitem__(104, {"already_stopping": True, "awaitable_work": 0}))
    try:
        m._stop_gateways({}, [e])
    finally:
        t.cancel()
    assert 1.2 <= harness["stopped"][104] < BUDGET


def test_a_stale_active_work_list_after_the_turn_ended_does_not_hold_the_drain(harness):
    """Vera's QA probe: the drain-time list outlives the turn; active_agents is the count."""
    e = harness["entry"](105, None, state={"active_agents": 0, "active_work": [{"kind": "chat", "session": "s"}]})
    e["ack"] = {"pausing": True, "awaitable_work": 1}
    assert m._holds_work(e) is True
    e["ack"] = None
    assert m._holds_work(e) is False


def test_a_busy_ack_is_not_overruled_by_an_idle_state_file(harness):
    """Vera's QA probe: ACK awaitable_work 1 + idle state file must keep waiting."""
    e = harness["entry"](106, {"pausing": True, "not_draining": False, "awaitable_work": 1},
                         state={"active_agents": 0, "active_work": None})
    t = _later(1.2, lambda: harness["acks"].__setitem__(106, {"already_stopping": True, "awaitable_work": 0}))
    try:
        m._stop_gateways({}, [e])
    finally:
        t.cancel()
    assert 1.2 <= harness["stopped"][106] < BUDGET


def test_when_the_socket_is_gone_the_published_agent_count_decides(harness):
    e = harness["entry"](107, {"pausing": True, "not_draining": False, "awaitable_work": 1},
                         state={"active_agents": 1, "active_work": [{"kind": "chat"}]})

    def turn_ends():
        harness["acks"][107] = None  # gateway entered stop(): socket closed
        harness["set_state"](107, {"active_agents": 0, "active_work": [{"kind": "chat"}]})

    t = _later(1.2, turn_ends)
    try:
        m._stop_gateways({}, [e])
    finally:
        t.cancel()
    assert 1.2 <= harness["stopped"][107] < BUDGET


def test_a_not_draining_gateway_is_asked_again_until_it_drains(harness):
    """2026-11-08: Vera answered but never drained, and took a new QA turn mid-update."""
    e = harness["entry"](108, {"pausing": False, "already_stopping": False, "not_draining": True, "awaitable_work": 1},
                         state={"active_agents": 1})
    t = _later(1.5, lambda: harness["acks"].__setitem__(108, {"pausing": True, "awaitable_work": 0}))
    try:
        m._stop_gateways({}, [e])
    finally:
        t.cancel()
    assert harness["asks"][108] >= 2 and harness["stopped"][108] < BUDGET


def test_unknown_state_counts_as_busy(harness):
    e = harness["entry"](109, {"pausing": True, "already_stopping": False})  # no count, no state file
    e["ack"] = e.get("ack")
    assert m._holds_work(e) is True
    m._stop_gateways({}, [e])  # waits the outer net, then the supervisor stop
    assert harness["stopped"][109] >= BUDGET


def test_no_answer_keeps_the_existing_supervised_stop(harness):
    e = harness["entry"](110, None, state={"active_agents": 0})
    m._stop_gateways({}, [e])
    assert harness["stopped"][110] < 0.5


def test_a_launchd_job_takes_its_home_from_its_plist_not_the_launcher_pid(tmp_path, monkeypatch):
    """2026-11-08: osascript launcher PIDs resolved to the sticky active profile (taylor01)."""
    vera = tmp_path / "profiles" / "vera"
    vera.mkdir(parents=True)
    plist = tmp_path / "ai.hermes.gateway-vera.plist"
    plist.write_bytes(plistlib.dumps({"Label": "ai.hermes.gateway-vera",
                                      "EnvironmentVariables": {"HERMES_HOME": str(vera)}}))
    monkeypatch.setattr(m, "_home_of", lambda pid: str(tmp_path / "profiles" / "taylor01"))
    assert m._plist_home(plist) == str(vera)
    assert m._plist_home(tmp_path / "missing.plist") is None
