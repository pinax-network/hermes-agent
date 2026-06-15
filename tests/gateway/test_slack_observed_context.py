"""Tests for Slack passive channel observation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.slack import SlackAdapter


class _FakeObservedStore:
    def __init__(self):
        self._messages = {}

    def get_or_create_session(self, source):
        session_id = f"{source.chat_id}:{source.thread_id or ''}"
        self._messages.setdefault(session_id, [])
        return SimpleNamespace(session_id=session_id, session_key=session_id)

    def append_to_transcript(self, session_id, entry):
        self._messages.setdefault(session_id, []).append(dict(entry))

    def load_transcript(self, session_id):
        return list(self._messages.get(session_id, []))

    def prune_observed_transcript(self, session_id, max_messages):
        messages = self._messages.setdefault(session_id, [])
        observed_indexes = [
            idx for idx, msg in enumerate(messages) if msg.get("observed")
        ]
        if max_messages <= 0:
            self._messages[session_id] = [
                msg for msg in messages if not msg.get("observed")
            ]
            return len(observed_indexes)
        remove = set(observed_indexes[: max(0, len(observed_indexes) - max_messages)])
        self._messages[session_id] = [
            msg for idx, msg in enumerate(messages) if idx not in remove
        ]
        return len(remove)


@pytest.fixture()
def adapter():
    config = PlatformConfig(enabled=True, token="xoxb-fake")
    config.extra["observe_unaddressed_channel_messages"] = True
    a = SlackAdapter(config)
    a._app = MagicMock()
    a._app.client = AsyncMock()
    a._bot_user_id = "U_BOT"
    a._team_bot_user_ids = {"T1": "U_BOT"}
    a._session_store = _FakeObservedStore()
    a.handle_message = AsyncMock()
    return a


def _event(text: str, ts: str, *, thread_ts: str | None = None) -> dict:
    event = {
        "channel": "C_INCIDENT",
        "channel_type": "channel",
        "team": "T1",
        "user": "U_ALICE",
        "text": text,
        "ts": ts,
    }
    if thread_ts is not None:
        event["thread_ts"] = thread_ts
    return event


@pytest.mark.asyncio
async def test_unaddressed_channel_message_is_observed_not_dispatched(adapter):
    adapter._resolve_user_name = AsyncMock(return_value="Alice")

    await adapter._handle_slack_message(_event("investigating api errors", "1.0"))

    adapter.handle_message.assert_not_awaited()
    messages = adapter._session_store.load_transcript("C_INCIDENT:")
    assert len(messages) == 1
    assert messages[0]["observed"] is True
    assert messages[0]["content"] == "[Alice|U_ALICE]\ninvestigating api errors"


@pytest.mark.asyncio
async def test_addressed_message_gets_observed_context(adapter):
    adapter._resolve_user_name = AsyncMock(return_value="Alice")

    await adapter._handle_slack_message(_event("database latency started", "1.0"))
    await adapter._handle_slack_message(_event("<@U_BOT> summarize", "2.0"))

    adapter.handle_message.assert_awaited_once()
    dispatched = adapter.handle_message.await_args.args[0]
    assert dispatched.text == "summarize"
    assert dispatched.channel_context.startswith("[Observed Slack channel context]")
    assert "database latency started" in dispatched.channel_context
    assert "observed Slack channel context" in dispatched.channel_prompt


@pytest.mark.asyncio
async def test_observed_messages_are_pruned_and_truncated(adapter):
    adapter.config.extra["observed_persist_max_messages"] = 2
    adapter.config.extra["observed_message_max_chars"] = 12
    adapter._resolve_user_name = AsyncMock(return_value="Alice")

    await adapter._handle_slack_message(_event("first message should prune", "1.0"))
    await adapter._handle_slack_message(_event("second message", "2.0"))
    await adapter._handle_slack_message(_event("third message", "3.0"))

    messages = adapter._session_store.load_transcript("C_INCIDENT:")
    assert len(messages) == 2
    assert "first" not in "\n".join(msg["content"] for msg in messages)
    assert messages[-1]["content"].endswith("third messag")


@pytest.mark.asyncio
async def test_observation_can_be_disabled(adapter):
    adapter.config.extra["observe_unaddressed_channel_messages"] = False
    adapter._resolve_user_name = AsyncMock(return_value="Alice")

    await adapter._handle_slack_message(_event("silent", "1.0"))

    adapter.handle_message.assert_not_awaited()
    assert adapter._session_store.load_transcript("C_INCIDENT:") == []
