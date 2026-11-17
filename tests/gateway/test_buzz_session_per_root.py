"""Opt-in per-root Buzz sessions (platforms.buzz.extra.session_per_root, BitPod BIT-1768).

Every top-level channel mention used to key on ``group:<channel>:<author>``, so two gate requests
from one author shared one session and the second interrupted the first. With the option on, a
top-level message roots its own session and replies in its thread join it.
"""

import importlib.util
import sys
from pathlib import Path

import pytest
from unittest.mock import AsyncMock

from gateway.session import build_session_key

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_buzz_module():
    path = REPO_ROOT / "plugins" / "platforms" / "buzz" / "adapter.py"
    spec = importlib.util.spec_from_file_location("plugin_adapter_buzz_per_root", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_buzz_mod = _load_buzz_module()
BuzzAdapter = _buzz_mod.BuzzAdapter

CHANNEL = "ccc2bc1a-7a82-5a8f-8c4e-57a070cbe7cd"
SELF_PUBKEY = "9fd5c7ba6d3ef224da78f541e0fcb9c50f72cc63edb19aae76ac6a0474dfa860"
AUTHOR = "b" * 64
ROOT_A = "a" * 64
ROOT_B = "d" * 64


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch, tmp_path):
    for var in ("BUZZ_RELAY_URL", "BUZZ_CHANNELS", "BUZZ_HOME_CHANNEL", "BUZZ_ALLOWED_USERS",
                "BUZZ_REQUIRE_MENTION", "BUZZ_REPLY_TO_MODE", "BUZZ_TRANSPORT", "BUZZ_SESSION_PER_ROOT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_buzz_mod, "_DEFAULT_CREDENTIALS_DIR", tmp_path / "no-creds")


def _make_adapter(extra=None, chat_type="group"):
    from gateway.config import PlatformConfig

    adapter = BuzzAdapter(PlatformConfig(enabled=True, extra={"relay_url": "https://test.relay", **(extra or {})}))
    adapter._self_pubkey = SELF_PUBKEY
    adapter._self_npub = _buzz_mod.hex_to_npub(SELF_PUBKEY)
    adapter._display_name = "Chip"
    adapter._private_key = "nsec1test"
    adapter._channel_state[CHANNEL] = {"chat_type": chat_type, "last_ts": 0, "seen": {}}
    adapter._message_handler = AsyncMock()
    adapter.send_reaction = AsyncMock(return_value=True)

    async def _stub_cli(args, *, input_text=None):
        return 0, "[]", ""

    adapter._run_cli = _stub_cli
    dispatched = []

    async def capture(event):
        dispatched.append(event)

    adapter.handle_message = capture
    return adapter, dispatched


def _top_level(event_id, content="@Chip /qa-review"):
    return {"id": event_id, "pubkey": AUTHOR, "content": content, "created_at": 1000, "kind": 9,
            "tags": [["h", CHANNEL]]}


def _reply(event_id, root, content="@Chip follow-up"):
    return {"id": event_id, "pubkey": AUTHOR, "content": content, "created_at": 1001, "kind": 9,
            "tags": [["h", CHANNEL], ["e", root, "", "root"], ["e", root, "", "reply"]]}


async def _feed(adapter, *events):
    for event in events:
        await adapter._handle_event(CHANNEL, adapter._channel_state[CHANNEL], event)


@pytest.mark.asyncio
async def test_default_off_keeps_author_scoped_top_level_session():
    adapter, dispatched = _make_adapter()
    await _feed(adapter, _top_level(ROOT_A), _top_level(ROOT_B))
    assert [e.source.thread_id for e in dispatched] == [None, None]
    assert build_session_key(dispatched[0].source) == build_session_key(dispatched[1].source)


@pytest.mark.asyncio
async def test_on_each_top_level_message_roots_its_own_session():
    adapter, dispatched = _make_adapter({"session_per_root": True})
    await _feed(adapter, _top_level(ROOT_A), _top_level(ROOT_B))
    assert [e.source.thread_id for e in dispatched] == [ROOT_A, ROOT_B]
    assert build_session_key(dispatched[0].source) != build_session_key(dispatched[1].source)


@pytest.mark.asyncio
async def test_on_thread_reply_joins_its_roots_session():
    adapter, dispatched = _make_adapter({"session_per_root": "true"})
    await _feed(adapter, _top_level(ROOT_A), _reply("e" * 64, ROOT_A))
    keys = {build_session_key(e.source) for e in dispatched}
    assert len(keys) == 1 and dispatched[1].source.thread_id == ROOT_A


@pytest.mark.asyncio
async def test_on_reply_to_top_level_trigger_still_anchors_on_it():
    adapter, _ = _make_adapter({"session_per_root": True})
    await _feed(adapter, _top_level(ROOT_A))
    calls = []

    async def capture_cli(args, *, input_text=None):
        calls.append(list(args))
        return 0, '{"accepted": true, "event_id": "evt-out"}', ""

    adapter._run_cli = capture_cli
    await adapter.send(CHANNEL, "verdict", reply_to=ROOT_A, metadata={"thread_id": ROOT_A})
    assert calls[0][calls[0].index("--reply-to") + 1] == ROOT_A


@pytest.mark.asyncio
async def test_on_dm_is_unchanged():
    adapter, dispatched = _make_adapter({"session_per_root": True}, chat_type="dm")
    await _feed(adapter, _top_level(ROOT_A, content="hi"))
    assert dispatched and dispatched[0].source.thread_id is None
