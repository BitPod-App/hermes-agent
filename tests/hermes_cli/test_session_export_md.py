from pathlib import Path

import pytest

from hermes_cli.session_export_md import (
    append_manifest_entry,
    redact_session_data,
    render_session_markdown,
    safe_session_filename,
    verify_export_file,
    write_session_markdown,
)


def _session(**overrides):
    data = {
        "id": "20260706_123456_abcd1234",
        "title": "Export Test",
        "source": "telegram",
        "model": "gpt-5.5",
        "billing_provider": "openai-codex",
        "cwd": "/tmp/project",
        "started_at": 1783331696.0,
        "last_active": 1783331705.0,
        "ended_at": 1783331710.0,
        "message_count": 3,
        "tool_call_count": 1,
        "archived": 0,
        "messages": [
            {"role": "user", "content": "Hello", "created_at": 1783331697.0},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "terminal", "arguments": "{\"command\": \"pwd\"}"}}
                ],
                "created_at": 1783331698.0,
            },
            {"role": "tool", "name": "terminal", "content": "output", "created_at": 1783331699.0},
        ],
    }
    data.update(overrides)
    return data




def test_safe_session_filename_is_deterministic_and_path_safe():
    filename = safe_session_filename(
        _session(id="20260706_123456_abcd1234", title="Bad / title: * ?"), fmt="qmd"
    )

    assert filename.startswith("20260706_123456_abcd1234-")
    assert filename.endswith(".qmd")
    assert "/" not in filename
    assert ":" not in filename
    assert "*" not in filename
    assert "?" not in filename






def test_verify_export_file_checks_count_and_sha(tmp_path):
    session = _session()
    path = write_session_markdown(session, tmp_path)

    ok, reason = verify_export_file(path, session)
    assert ok is True
    assert reason == "ok"

    path.write_text(path.read_text(encoding="utf-8").replace("Hello", "Tampered"), encoding="utf-8")
    ok, reason = verify_export_file(path, session)
    assert ok is False
    assert "sha256" in reason




def test_redact_session_data_uses_structured_key_context_for_opaque_values():
    opaque = "A" * 31 + "_" + "B" * 32
    nested = {"output": {"HERMES_DASHBOARD_SESSION_TOKEN": opaque}}
    session = _session(messages=[{"role": "tool", "content": __import__("json").dumps(nested)}])

    redacted = redact_session_data(session)
    serialized = __import__("json").dumps(redacted)

    assert opaque not in serialized
    assert "[redacted]" in serialized


def test_write_session_markdown_redacts_by_default(tmp_path):
    opaque = "C" * 64
    session = _session(messages=[{"role": "tool", "content": f"HERMES_DASHBOARD_SESSION_TOKEN={opaque}"}])

    path = write_session_markdown(session, tmp_path)

    assert opaque not in path.read_text(encoding="utf-8")
    assert path.stat().st_mode & 0o777 == 0o600

    path.chmod(0o644)
    replaced = write_session_markdown(session, tmp_path, force=True)
    assert replaced.stat().st_mode & 0o777 == 0o600


def test_manifest_is_private_for_new_and_existing_file(tmp_path):
    session = _session()
    export = tmp_path / "export.md"
    export.write_text("safe", encoding="utf-8")

    manifest = append_manifest_entry(tmp_path, session, export, fmt="md")
    assert manifest.stat().st_mode & 0o777 == 0o600

    manifest.chmod(0o644)
    append_manifest_entry(tmp_path, session, export, fmt="md")
    assert manifest.stat().st_mode & 0o777 == 0o600
