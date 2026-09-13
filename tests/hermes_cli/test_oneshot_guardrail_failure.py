"""One-shot failure propagation for controlled tool-loop termination (BIT-1334)."""

from __future__ import annotations

import json

from hermes_cli import oneshot


def test_oneshot_returns_nonzero_and_persists_guardrail_failure(
    monkeypatch, capsys, tmp_path
):
    response = (
        "I stopped retrying tool_call because it hit the tool-call guardrail "
        "(repeated_exact_failure_block)."
    )
    result = {
        "final_response": response,
        "failed": True,
        "partial": False,
        "completed": False,
        "turn_exit_reason": "guardrail_halt",
        "failure_reason": "tool_guardrail:repeated_exact_failure_block",
        "guardrail": {"code": "repeated_exact_failure_block", "count": 5},
    }
    monkeypatch.setattr(
        oneshot, "_run_agent", lambda *args, **kwargs: (response, result)
    )
    usage_path = tmp_path / "usage.json"

    rc = oneshot.run_oneshot("review the source", usage_file=str(usage_path))

    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == response + "\n"
    report = json.loads(usage_path.read_text())
    assert report["failed"] is True
    assert report["completed"] is False
    assert report["turn_exit_reason"] == "guardrail_halt"
    assert report["failure_reason"] == "tool_guardrail:repeated_exact_failure_block"
    assert report["guardrail"]["code"] == "repeated_exact_failure_block"
