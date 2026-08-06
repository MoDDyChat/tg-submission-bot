"""Unit-tests for handlers/service_messages.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers import service_messages


def _make_service_message(
    *,
    chat_id: int,
    message_id: int = 777,
    service_attr: str = "forum_topic_edited",
):
    """Build a minimal fake Message with one service attribute set."""
    msg = SimpleNamespace(
        message_id=message_id,
        chat=SimpleNamespace(id=chat_id),
        from_user=None,
        message_thread_id=None,
        delete=AsyncMock(),
    )
    # Set the service attribute to a truthy value
    setattr(msg, service_attr, SimpleNamespace(name="topic"))
    return msg


async def test_suppress_forum_topic_edited_in_moderator_group(monkeypatch) -> None:
    """Service message in the moderator group must be deleted."""
    group_id = -1001234567890
    monkeypatch.setattr(service_messages.config, "moderator_group_id", group_id)

    msg = _make_service_message(chat_id=group_id, service_attr="forum_topic_edited")
    await service_messages._suppress_service_message(msg)

    msg.delete.assert_awaited_once()


async def test_suppress_forum_topic_created_in_moderator_group(monkeypatch) -> None:
    group_id = -1001234567890
    monkeypatch.setattr(service_messages.config, "moderator_group_id", group_id)

    msg = _make_service_message(chat_id=group_id, service_attr="forum_topic_created")
    await service_messages._suppress_service_message(msg)

    msg.delete.assert_awaited_once()


async def test_no_delete_outside_moderator_group(monkeypatch) -> None:
    """Service message in a different chat must NOT be deleted."""
    group_id = -1001234567890
    monkeypatch.setattr(service_messages.config, "moderator_group_id", group_id)

    msg = _make_service_message(chat_id=-999999, service_attr="forum_topic_edited")
    await service_messages._suppress_service_message(msg)

    msg.delete.assert_not_awaited()


async def test_suppress_ignores_telegram_api_error(monkeypatch) -> None:
    """TelegramAPIError during delete must be silently ignored."""
    from aiogram.exceptions import TelegramAPIError

    group_id = -1001234567890
    monkeypatch.setattr(service_messages.config, "moderator_group_id", group_id)

    msg = _make_service_message(chat_id=group_id)
    msg.delete = AsyncMock(side_effect=TelegramAPIError(method=None, message="Forbidden"))  # type: ignore[arg-type]

    # Should not raise
    await service_messages._suppress_service_message(msg)


async def test_human_topic_rename_is_queued_for_repair(monkeypatch) -> None:
    group_id = -1001234567890
    monkeypatch.setattr(service_messages.config, "moderator_group_id", group_id)
    mark_drifted = AsyncMock()
    monkeypatch.setattr(
        service_messages, "mark_topic_title_externally_drifted", mark_drifted
    )
    session = AsyncMock()
    msg = _make_service_message(chat_id=group_id)
    msg.from_user = SimpleNamespace(is_bot=False)
    msg.message_thread_id = 321

    await service_messages._handle_topic_edited(msg, session)

    mark_drifted.assert_awaited_once_with(session, 321)
    msg.delete.assert_awaited_once()


async def test_bot_topic_rename_does_not_requeue_itself(monkeypatch) -> None:
    group_id = -1001234567890
    monkeypatch.setattr(service_messages.config, "moderator_group_id", group_id)
    mark_drifted = AsyncMock()
    monkeypatch.setattr(
        service_messages, "mark_topic_title_externally_drifted", mark_drifted
    )
    msg = _make_service_message(chat_id=group_id)
    msg.from_user = SimpleNamespace(is_bot=True)
    msg.message_thread_id = 321

    await service_messages._handle_topic_edited(msg, AsyncMock())

    mark_drifted.assert_not_awaited()
    msg.delete.assert_awaited_once()
