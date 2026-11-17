"""T01 pin (BIT-1411): TUI/Desktop session.save writes the transcript 0600, like CLI and gateway /save."""
import os
import threading
import types
from datetime import datetime
from pathlib import Path


def test_tui_session_save_is_private_regardless_of_umask(monkeypatch, tmp_path):
    from tui_gateway import server

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    sid = "t01-private-save"
    agent = types.SimpleNamespace(model="hermes-test", session_id="20260101_120000_t01",
                                  session_start=datetime(2026, 1, 1, 12, 0, 0), _cached_system_prompt="")
    server._sessions[sid] = {"agent": agent, "session_key": "t01-key",
                             "history": [{"role": "user", "content": "hi"}],
                             "history_lock": threading.Lock(), "created_at": 1735732800.0}
    previous = os.umask(0o022)
    try:
        resp = server._methods["session.save"]("1", {"session_id": sid})
    finally:
        os.umask(previous)
        server._sessions.pop(sid, None)
    assert "result" in resp, resp
    saved = Path(resp["result"]["file"])
    assert saved.exists()
    assert saved.stat().st_mode & 0o777 == 0o600
