"""Regression coverage for Slack channel-wide session scoping."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _ensure_slack_mock():
    """Install mock slack modules so SlackAdapter can be imported."""
    if "slack_bolt" in sys.modules and hasattr(sys.modules["slack_bolt"], "__file__"):
        return

    slack_bolt = MagicMock()
    slack_bolt.async_app.AsyncApp = MagicMock
    slack_bolt.adapter.socket_mode.async_handler.AsyncSocketModeHandler = MagicMock

    slack_sdk = MagicMock()
    slack_sdk.web.async_client.AsyncWebClient = MagicMock

    for name, mod in [
        ("slack_bolt", slack_bolt),
        ("slack_bolt.async_app", slack_bolt.async_app),
        ("slack_bolt.adapter", slack_bolt.adapter),
        ("slack_bolt.adapter.socket_mode", slack_bolt.adapter.socket_mode),
        ("slack_bolt.adapter.socket_mode.async_handler", slack_bolt.adapter.socket_mode.async_handler),
        ("slack_sdk", slack_sdk),
        ("slack_sdk.web", slack_sdk.web),
        ("slack_sdk.web.async_client", slack_sdk.web.async_client),
    ]:
        sys.modules.setdefault(name, mod)

    sys.modules.setdefault("aiohttp", MagicMock())


_ensure_slack_mock()

import gateway.platforms.slack as _slack_mod  # noqa: E402

_slack_mod.SLACK_AVAILABLE = True

from gateway.config import Platform, PlatformConfig, load_gateway_config  # noqa: E402
from gateway.platforms.slack import SlackAdapter  # noqa: E402
from gateway.session import SessionSource, build_session_key  # noqa: E402


@pytest.fixture()
def adapter():
    config = PlatformConfig(enabled=True, token="xoxb-fake-token")
    a = SlackAdapter(config)
    a._app = MagicMock()
    a._app.client = AsyncMock()
    a._bot_user_id = "U_BOT"
    a._running = True
    a.handle_message = AsyncMock()
    return a


def _channel_event(text: str, ts: str, thread_ts: str | None = None) -> dict:
    event = {
        "channel": "C_INCIDENT",
        "channel_type": "channel",
        "user": "U_USER",
        "text": text,
        "ts": ts,
    }
    if thread_ts is not None:
        event["thread_ts"] = thread_ts
    return event


async def _capture_message_event(adapter: SlackAdapter, event: dict):
    captured = []
    adapter.handle_message = AsyncMock(side_effect=lambda e: captured.append(e))
    with patch.object(
        adapter,
        "_resolve_user_name",
        new=AsyncMock(return_value="testuser"),
    ):
        await adapter._handle_slack_message(event)
    assert len(captured) == 1
    return captured[0]


class TestSlackChannelSessionScope:
    @pytest.mark.asyncio
    async def test_default_top_level_message_keeps_legacy_thread_session(self, adapter):
        event = _channel_event("<@U_BOT> hello", ts="1700000000.000001")

        msg_event = await _capture_message_event(adapter, event)

        assert msg_event.source.thread_id == "1700000000.000001"

    @pytest.mark.asyncio
    async def test_reply_in_thread_false_top_level_message_uses_channel_session(self, adapter):
        adapter.config.extra["reply_in_thread"] = False
        event = _channel_event("<@U_BOT> hello", ts="1700000000.000002")

        msg_event = await _capture_message_event(adapter, event)

        assert msg_event.source.thread_id is None
        assert msg_event.reply_to_message_id is None

    @pytest.mark.asyncio
    async def test_default_thread_reply_keeps_thread_session(self, adapter):
        event = _channel_event(
            "<@U_BOT> thread reply",
            ts="1700000000.000003",
            thread_ts="1700000000.000000",
        )

        msg_event = await _capture_message_event(adapter, event)

        assert msg_event.source.thread_id == "1700000000.000000"
        assert msg_event.reply_to_message_id == "1700000000.000000"

    @pytest.mark.asyncio
    async def test_channel_scope_top_level_message_uses_channel_session(self, adapter):
        adapter.config.extra["session_scope"] = "channel"
        event = _channel_event("<@U_BOT> hello", ts="1700000000.000004")

        msg_event = await _capture_message_event(adapter, event)

        assert msg_event.source.thread_id is None

    @pytest.mark.asyncio
    async def test_channel_scope_thread_reply_uses_channel_session(self, adapter):
        adapter.config.extra["session_scope"] = "channel"
        event = _channel_event(
            "<@U_BOT> thread reply",
            ts="1700000000.000005",
            thread_ts="1700000000.000000",
        )

        msg_event = await _capture_message_event(adapter, event)

        assert msg_event.source.thread_id is None
        assert msg_event.reply_to_message_id is None

    @pytest.mark.asyncio
    async def test_invalid_session_scope_fails_safe_to_thread_scope(self, adapter):
        adapter.config.extra["session_scope"] = "bad-value"
        event = _channel_event(
            "<@U_BOT> thread reply",
            ts="1700000000.000006",
            thread_ts="1700000000.000000",
        )

        msg_event = await _capture_message_event(adapter, event)

        assert msg_event.source.thread_id == "1700000000.000000"

    @pytest.mark.asyncio
    async def test_thread_ts_equal_ts_treated_as_top_level_when_not_threading(self, adapter):
        adapter.config.extra["reply_in_thread"] = False
        event = _channel_event(
            "<@U_BOT> root-ish payload",
            ts="1700000000.000007",
            thread_ts="1700000000.000007",
        )

        msg_event = await _capture_message_event(adapter, event)

        assert msg_event.source.thread_id is None


def test_channel_session_key_is_shared_only_when_group_user_isolation_disabled():
    first = SessionSource(
        platform=Platform.SLACK,
        chat_id="C_INCIDENT",
        chat_type="group",
        user_id="U_ONE",
    )
    second = SessionSource(
        platform=Platform.SLACK,
        chat_id="C_INCIDENT",
        chat_type="group",
        user_id="U_TWO",
    )

    assert build_session_key(first, group_sessions_per_user=False) == build_session_key(
        second,
        group_sessions_per_user=False,
    )
    assert build_session_key(first, group_sessions_per_user=True) != build_session_key(
        second,
        group_sessions_per_user=True,
    )


def test_slack_session_scope_config_bridge(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "slack:\n  session_scope: channel\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.delenv("SLACK_SESSION_SCOPE", raising=False)

    config = load_gateway_config()

    assert config.platforms[Platform.SLACK].extra["session_scope"] == "channel"
    assert os.environ["SLACK_SESSION_SCOPE"] == "channel"


def test_slack_channel_context_config_bridge(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "slack:\n"
        "  channel_context_on_mention: true\n"
        "  channel_context_allowed_channels:\n"
        "    - C_INCIDENT\n"
        "  channel_context_max_messages: 50\n"
        "  channel_context_max_threads: 5\n"
        "  channel_context_max_thread_messages: 20\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    config = load_gateway_config()
    slack_extra = config.platforms[Platform.SLACK].extra

    assert slack_extra["channel_context_on_mention"] is True
    assert slack_extra["channel_context_allowed_channels"] == ["C_INCIDENT"]
    assert slack_extra["channel_context_max_messages"] == 50
    assert slack_extra["channel_context_max_threads"] == 5
    assert slack_extra["channel_context_max_thread_messages"] == 20


@pytest.mark.asyncio
async def test_channel_context_on_mention_injects_history_and_threads(adapter):
    adapter.config.extra.update(
        {
            "session_scope": "channel",
            "strict_mention": True,
            "channel_context_on_mention": True,
            "channel_context_allowed_channels": ["C_INCIDENT"],
            "channel_context_max_messages": 10,
            "channel_context_max_threads": 2,
            "channel_context_max_thread_messages": 5,
        }
    )
    adapter._app.client.conversations_history = AsyncMock(
        return_value={
            "messages": [
                {"user": "U_TWO", "text": "mitigation deployed", "ts": "1700000002.000000"},
                {
                    "user": "U_ONE",
                    "text": "incident started",
                    "ts": "1700000001.000000",
                    "reply_count": 1,
                    "thread_ts": "1700000001.000000",
                },
            ]
        }
    )
    adapter._app.client.conversations_replies = AsyncMock(
        return_value={
            "messages": [
                {"user": "U_ONE", "text": "incident started", "ts": "1700000001.000000"},
                {"user": "U_THREE", "text": "root cause found", "ts": "1700000001.000100"},
            ]
        }
    )
    adapter._resolve_user_name = AsyncMock(side_effect=lambda user_id, chat_id="": user_id)

    event = _channel_event("<@U_BOT> incident resolved, write summary", ts="1700000003.000000")

    captured = []
    adapter.handle_message = AsyncMock(side_effect=lambda e: captured.append(e))
    await adapter._handle_slack_message(event)
    assert len(captured) == 1
    msg_event = captured[0]

    adapter._app.client.conversations_history.assert_awaited_once_with(
        channel="C_INCIDENT",
        latest="1700000003.000000",
        inclusive=False,
        limit=10,
    )
    adapter._app.client.conversations_replies.assert_awaited_once()
    assert msg_event.source.thread_id is None
    assert "[Slack channel context" in msg_event.text
    assert "1700000001.000000 U_ONE: incident started" in msg_event.text
    assert "↳ 1700000001.000100 U_THREE: root cause found" in msg_event.text
    assert "1700000002.000000 U_TWO: mitigation deployed" in msg_event.text
    assert msg_event.text.rstrip().endswith("incident resolved, write summary")


@pytest.mark.asyncio
async def test_channel_context_allowed_by_channel_id_without_name_lookup(adapter):
    adapter.config.extra["channel_context_allowed_channels"] = ["C_INCIDENT"]
    adapter._app.client.conversations_info = AsyncMock()

    assert await adapter._slack_channel_context_channel_allowed("C_INCIDENT", "T1") is True
    adapter._app.client.conversations_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_context_allowed_by_exact_channel_name(adapter):
    adapter.config.extra["channel_context_allowed_channels"] = ["#inc-prod"]
    adapter._app.client.conversations_info = AsyncMock(
        return_value={"channel": {"name": "inc-prod"}}
    )

    assert await adapter._slack_channel_context_channel_allowed("C123", "T1") is True


@pytest.mark.asyncio
async def test_channel_context_allowed_by_channel_name_regex(adapter):
    adapter.config.extra["channel_context_allowed_channels"] = ["regex:^inc-.*"]
    adapter._app.client.conversations_info = AsyncMock(
        return_value={"channel": {"name": "inc-prod"}}
    )

    assert await adapter._slack_channel_context_channel_allowed("C123", "T1") is True


@pytest.mark.asyncio
async def test_channel_context_regex_rejects_non_matching_name(adapter):
    adapter.config.extra["channel_context_allowed_channels"] = ["regex:^inc-.*"]
    adapter._app.client.conversations_info = AsyncMock(
        return_value={"channel": {"name": "prod-inc"}}
    )

    assert await adapter._slack_channel_context_channel_allowed("C123", "T1") is False


@pytest.mark.asyncio
async def test_channel_context_invalid_regex_fails_closed(adapter):
    adapter.config.extra["channel_context_allowed_channels"] = ["regex:["]
    adapter._app.client.conversations_info = AsyncMock(
        return_value={"channel": {"name": "inc-prod"}}
    )

    assert await adapter._slack_channel_context_channel_allowed("C123", "T1") is False


@pytest.mark.asyncio
async def test_channel_context_on_mention_accepts_regex_channel_name(adapter):
    adapter.config.extra.update(
        {
            "session_scope": "channel",
            "strict_mention": True,
            "channel_context_on_mention": True,
            "channel_context_allowed_channels": ["regex:^inc-.*"],
            "channel_context_max_messages": 10,
        }
    )
    adapter._app.client.conversations_info = AsyncMock(
        return_value={"channel": {"name": "inc-prod"}}
    )
    adapter._app.client.conversations_history = AsyncMock(
        return_value={
            "messages": [
                {"user": "U_ONE", "text": "incident started", "ts": "1700000001.000000"},
            ]
        }
    )
    adapter._app.client.conversations_replies = AsyncMock(return_value={"messages": []})
    adapter._resolve_user_name = AsyncMock(side_effect=lambda user_id, chat_id="": user_id)
    captured = []
    adapter.handle_message = AsyncMock(side_effect=lambda e: captured.append(e))

    await adapter._handle_slack_message(
        _channel_event("<@U_BOT> incident resolved", ts="1700000002.000000")
    )

    adapter._app.client.conversations_history.assert_awaited_once()
    assert len(captured) == 1
    assert "incident started" in captured[0].text


@pytest.mark.asyncio
async def test_channel_context_not_fetched_for_unmentioned_strict_message(adapter):
    adapter.config.extra.update(
        {
            "strict_mention": True,
            "channel_context_on_mention": True,
            "channel_context_allowed_channels": ["C_INCIDENT"],
        }
    )
    adapter._app.client.conversations_history = AsyncMock(return_value={"messages": []})

    await adapter._handle_slack_message(_channel_event("incident chatter", ts="1700000004.000000"))

    adapter._app.client.conversations_history.assert_not_awaited()
    adapter.handle_message.assert_not_awaited()
