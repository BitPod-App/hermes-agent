import sys

from hermes_state import SessionDB


def test_sessions_export_md_writes_single_session(monkeypatch, tmp_path, capsys):
    import hermes_cli.main as main_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    sid = "20260706_123456_abcd1234"
    db.create_session(sid, "cli")
    db.set_session_title(sid, "Export CLI Test")
    db.append_message(sid, "user", "hello")
    db.close()
    before = path.read_bytes()
    monkeypatch.setattr(sys, "argv", [
        "hermes", "sessions", "export", "--format", "md",
        "--session-id", "20260706_123456", str(tmp_path / "exports"),
    ])

    main_mod.main()

    output = capsys.readouterr().out
    files = list((tmp_path / "exports").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "# Export CLI Test" in text
    assert "hello" in text
    assert path.read_bytes() == before
    assert "Exported 1 session" in output
    assert "1 message" in output
    assert str(files[0]) in output


def test_sessions_export_verified_delete_keeps_file(monkeypatch, tmp_path, capsys):
    import hermes_cli.main as main_mod
    from hermes_cli.session_export_md import verify_export_file

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    db.create_session("delete-me", "cli")
    db.append_message("delete-me", "user", "retain this exported payload")
    original = db.export_session("delete-me")
    assert original is not None
    db.close()
    monkeypatch.setattr(sys, "argv", [
        "hermes", "sessions", "export", "--format", "md",
        "--session-id", "delete-me", "--delete-after-verified", "--yes",
        str(tmp_path / "exports"),
    ])

    main_mod.main()

    files = list((tmp_path / "exports").glob("*.md"))
    assert len(files) == 1
    assert verify_export_file(files[0], original)[0]
    db = SessionDB(db_path=path, read_only=True)
    try:
        assert db.get_session("delete-me") is None
    finally:
        db.close()
    assert "Deleted exported session" in capsys.readouterr().out


def test_sessions_export_scrubs_secrets_by_default(monkeypatch, tmp_path):
    """Every exported artifact runs through force-mode secret redaction."""
    import hermes_cli.main as main_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    secret = "sk-proj-Zz12345678901234567890123456789012345678"
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    db.create_session("s1", "cli")
    db.append_message("s1", "tool", f"api key: {secret}", tool_name="terminal")
    db.close()
    before = path.read_bytes()
    monkeypatch.setattr(sys, "argv", [
        "hermes", "sessions", "export", "--format", "md",
        "--session-id", "s1", str(tmp_path / "exports"),
    ])

    main_mod.main()

    files = list((tmp_path / "exports").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert secret not in text
    assert "api key:" in text
    assert path.read_bytes() == before


def test_sessions_export_jsonl_scrubs_secrets_by_default(monkeypatch, tmp_path):
    import hermes_cli.main as main_mod
    import hermes_state

    opaque = "E" * 64

    class FakeDB:
        def resolve_session_id(self, session_id):
            return "s1"

        def assert_exports_safe(self, session_ids, max_messages=None):
            pass

        def export_session(self, session_id, **kwargs):
            return {
                "id": "s1",
                "messages": [
                    {"role": "tool", "content": f"HERMES_DASHBOARD_SESSION_TOKEN={opaque}"}
                ],
            }

        def close(self):
            pass

    output = tmp_path / "session.jsonl"
    monkeypatch.setattr(hermes_state, "SessionDB", lambda *args, **kwargs: FakeDB())
    monkeypatch.setattr(
        sys,
        "argv",
        ["hermes", "sessions", "export", "--session-id", "s1", str(output)],
    )

    main_mod.main()

    assert opaque not in output.read_text(encoding="utf-8")
    assert output.stat().st_mode & 0o777 == 0o600
