"""Tests for services/topics_queue.py — queue board rendering logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


from services import topics_queue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_system_message(key: str, chat_id: int, message_id: int, payload: dict | None = None):
    """Build a minimal fake system_message row."""
    row = MagicMock()
    row.key = key
    row.chat_id = chat_id
    row.message_id = message_id
    row.payload = payload or {}
    return row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _queue_line unit tests
# ---------------------------------------------------------------------------


def test_queue_line_pending_starts_with_hourglass() -> None:
    line = topics_queue._queue_line(1, "15.06.2026", "Иван", 42, "тема", "pending")
    assert line.startswith("⏳ ")


def test_queue_line_scheduled_starts_with_calendar() -> None:
    line = topics_queue._queue_line(1, "15.06.2026", "Иван", 42, "тема", "scheduled")
    assert line.startswith("📅 ")


def test_queue_line_contains_all_fields() -> None:
    line = topics_queue._queue_line(1, "15.06.2026", "Иван", 42, "тема", "pending")
    assert "<b>1</b>" in line
    assert "15.06.2026" in line
    assert "Иван" in line
    assert "(#42)" in line
    assert "тема" in line


# ---------------------------------------------------------------------------
# _topic_link unit tests
# ---------------------------------------------------------------------------


def _make_sub(media_ids=None, card_id=None):
    sub = MagicMock()
    sub.topic_media_message_ids = media_ids
    sub.topic_card_message_id = card_id
    return sub


def test_topic_link_points_to_first_media_message() -> None:
    link = topics_queue._topic_link("123456", 77, _make_sub([501, 502], 503))
    assert "https://t.me/c/123456/77/501" in link


def test_topic_link_falls_back_to_card_message() -> None:
    link = topics_queue._topic_link("123456", 77, _make_sub(None, 503))
    assert "https://t.me/c/123456/77/503" in link


def test_topic_link_falls_back_to_topic_root() -> None:
    link = topics_queue._topic_link("123456", 77, _make_sub(None, None))
    assert "https://t.me/c/123456/77'" in link


def test_topic_link_without_topic_is_dash() -> None:
    assert topics_queue._topic_link("123456", None, _make_sub([501], 503)) == "—"


async def test_build_queue_lines_escapes_author_html(monkeypatch) -> None:
    """Author display names with HTML-special chars are escaped, not fed raw to Telegram."""
    import datetime

    sub = MagicMock()
    sub.id = 42
    sub.status = "pending"
    sub.user_id = 7
    sub.user.full_name = "<)_,"  # name that broke Telegram HTML parsing
    sub.created_at = datetime.datetime(2026, 6, 15, tzinfo=datetime.timezone.utc)

    subs_result = MagicMock()
    subs_result.scalars.return_value.all.return_value = [sub]
    topics_result = []  # no user topics → link is "—"

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[subs_result, topics_result])
    monkeypatch.setattr(topics_queue.config, "moderator_group_id", -1001234567890)

    lines = await topics_queue._build_queue_lines(session)

    assert len(lines) == 1
    assert "&lt;)_," in lines[0]
    assert "<)_," not in lines[0]


async def test_empty_queue_creates_one_chunk(monkeypatch) -> None:
    """Empty queue → single 'Очередь пуста.' chunk is created."""
    bot = AsyncMock()
    session = AsyncMock()
    sent_msg = MagicMock()
    sent_msg.message_id = 42
    bot.send_message.return_value = sent_msg

    monkeypatch.setattr(topics_queue, "_build_queue_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(topics_queue, "list_system_messages_by_prefix", AsyncMock(return_value=[]))
    upsert = AsyncMock()
    monkeypatch.setattr(topics_queue, "upsert_system_message", upsert)
    monkeypatch.setattr(topics_queue, "delete_system_message", AsyncMock())
    monkeypatch.setattr(topics_queue.config, "moderator_group_id", -1001234567890)

    await topics_queue._render_queue_inner(bot, session)

    bot.send_message.assert_awaited_once()
    args = bot.send_message.call_args
    assert "Очередь пуста." in args.args[1]
    bot.pin_chat_message.assert_awaited_once()
    upsert.assert_awaited_once()


async def test_surplus_chunks_deleted_on_shrink(monkeypatch) -> None:
    """When queue shrinks to 1 chunk, extra tracked messages are deleted."""
    bot = AsyncMock()
    session = AsyncMock()

    chunk_text = "<b>Очередь постов в предложке:</b>\n<b>1</b>. line"
    cs = topics_queue._checksum(chunk_text)

    # Two existing chunks, but current content only yields one chunk
    existing = [
        _make_system_message("general:queue:00", -100999, 10, {"checksum": cs}),
        _make_system_message("general:queue:01", -100999, 11, {"checksum": "old"}),
    ]

    monkeypatch.setattr(topics_queue, "_build_queue_lines", AsyncMock(return_value=["<b>1</b>. line"]))
    monkeypatch.setattr(topics_queue, "list_system_messages_by_prefix", AsyncMock(return_value=existing))
    monkeypatch.setattr(topics_queue, "upsert_system_message", AsyncMock())
    delete_msg = AsyncMock()
    monkeypatch.setattr(topics_queue, "delete_system_message", delete_msg)
    monkeypatch.setattr(topics_queue.config, "moderator_group_id", -100999)

    await topics_queue._render_queue_inner(bot, session)

    # The surplus chunk (idx 1) should be deleted from Telegram and DB
    bot.delete_message.assert_awaited_once_with(-100999, 11)
    delete_msg.assert_awaited_once_with(session, "general:queue:01")


async def test_event_driven_path_skips_unchanged_checksum(monkeypatch) -> None:
    """Event-driven (force_reconcile=False): same checksum → no Telegram API call."""
    bot = AsyncMock()
    session = AsyncMock()

    chunk_text = "Очередь пуста."
    cs = topics_queue._checksum(chunk_text)
    existing = [
        _make_system_message("general:queue:00", -100999, 7, {"checksum": cs}),
    ]

    monkeypatch.setattr(topics_queue, "_build_queue_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(topics_queue, "list_system_messages_by_prefix", AsyncMock(return_value=existing))
    monkeypatch.setattr(topics_queue, "upsert_system_message", AsyncMock())
    monkeypatch.setattr(topics_queue, "delete_system_message", AsyncMock())
    monkeypatch.setattr(topics_queue.config, "moderator_group_id", -100999)

    await topics_queue._render_queue_inner(bot, session, force_reconcile=False)

    bot.edit_message_text.assert_not_awaited()
    bot.send_message.assert_not_awaited()


async def test_self_heal_recreates_deleted_chunk_despite_same_checksum(monkeypatch) -> None:
    """Self-heal (force_reconcile=True): missing Telegram message is recreated even if checksum matches."""
    bot = AsyncMock()
    session = AsyncMock()

    chunk_text = "Очередь пуста."
    cs = topics_queue._checksum(chunk_text)
    existing = [
        _make_system_message("general:queue:00", -100999, 99, {"checksum": cs}),
    ]

    # edit_message_text raises "message to edit not found"
    from aiogram.exceptions import TelegramAPIError
    api_error = TelegramAPIError(method=MagicMock(), message="message to edit not found")
    bot.edit_message_text.side_effect = api_error

    sent_msg = MagicMock()
    sent_msg.message_id = 200
    bot.send_message.return_value = sent_msg

    monkeypatch.setattr(topics_queue, "_build_queue_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(topics_queue, "list_system_messages_by_prefix", AsyncMock(return_value=existing))
    upsert = AsyncMock()
    monkeypatch.setattr(topics_queue, "upsert_system_message", upsert)
    monkeypatch.setattr(topics_queue, "delete_system_message", AsyncMock())
    monkeypatch.setattr(topics_queue.config, "moderator_group_id", -100999)

    await topics_queue._render_queue_inner(bot, session, force_reconcile=True)

    # Should attempt to edit (which fails), then create a new message
    bot.edit_message_text.assert_awaited_once()
    bot.send_message.assert_awaited_once()
    bot.pin_chat_message.assert_awaited_once_with(-100999, 200, disable_notification=True)
    upsert.assert_awaited_once()


# ---------------------------------------------------------------------------
# Schedule tests
# ---------------------------------------------------------------------------


async def test_empty_schedule_creates_message(monkeypatch) -> None:
    """Empty schedule → single 'В данный момент ничего не запланировано.' message is created."""
    bot = AsyncMock()
    session = AsyncMock()
    sent_msg = MagicMock()
    sent_msg.message_id = 42
    bot.send_message.return_value = sent_msg

    monkeypatch.setattr(topics_queue, "_build_schedule_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(topics_queue, "get_system_message", AsyncMock(return_value=None))
    upsert = AsyncMock()
    monkeypatch.setattr(topics_queue, "upsert_system_message", upsert)
    monkeypatch.setattr(topics_queue.config, "moderator_group_id", -1001234567890)

    await topics_queue._render_schedule_inner(bot, session)

    bot.send_message.assert_awaited_once()
    args = bot.send_message.call_args
    assert "В данный момент ничего не запланировано." in args.args[1]
    bot.pin_chat_message.assert_awaited_once()
    upsert.assert_awaited_once()


async def test_schedule_skips_unchanged_checksum(monkeypatch) -> None:
    """Same checksum → no Telegram API call."""
    bot = AsyncMock()
    session = AsyncMock()

    cs = topics_queue._checksum(topics_queue._build_schedule_text([]))
    existing = _make_system_message("general:schedule", -100999, 7, {"checksum": cs})

    monkeypatch.setattr(topics_queue, "_build_schedule_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(topics_queue, "get_system_message", AsyncMock(return_value=existing))
    monkeypatch.setattr(topics_queue, "upsert_system_message", AsyncMock())
    monkeypatch.setattr(topics_queue.config, "moderator_group_id", -100999)

    await topics_queue._render_schedule_inner(bot, session, force_reconcile=False)

    bot.edit_message_text.assert_not_awaited()
    bot.send_message.assert_not_awaited()


async def test_schedule_updates_existing_message_on_checksum_mismatch(monkeypatch) -> None:
    """Checksum mismatch → edit_message_text is called, message updated."""
    bot = AsyncMock()
    session = AsyncMock()

    existing = _make_system_message("general:schedule", -100999, 7, {"checksum": "old_cs"})

    monkeypatch.setattr(topics_queue, "_build_schedule_lines", AsyncMock(return_value=["1. line"]))
    monkeypatch.setattr(topics_queue, "get_system_message", AsyncMock(return_value=existing))
    upsert = AsyncMock()
    monkeypatch.setattr(topics_queue, "upsert_system_message", upsert)
    monkeypatch.setattr(topics_queue.config, "moderator_group_id", -100999)

    await topics_queue._render_schedule_inner(bot, session)

    bot.edit_message_text.assert_awaited_once()
    bot.send_message.assert_not_awaited()
    upsert.assert_awaited_once()


async def test_schedule_recreates_deleted_message(monkeypatch) -> None:
    """edit_message_text fails with 'message to edit not found' → message recreated."""
    bot = AsyncMock()
    session = AsyncMock()

    existing = _make_system_message("general:schedule", -100999, 99, {"checksum": "old_cs"})

    from aiogram.exceptions import TelegramAPIError
    api_error = TelegramAPIError(method=MagicMock(), message="message to edit not found")
    bot.edit_message_text.side_effect = api_error

    sent_msg = MagicMock()
    sent_msg.message_id = 200
    bot.send_message.return_value = sent_msg

    monkeypatch.setattr(topics_queue, "_build_schedule_lines", AsyncMock(return_value=["1. line"]))
    monkeypatch.setattr(topics_queue, "get_system_message", AsyncMock(return_value=existing))
    upsert = AsyncMock()
    monkeypatch.setattr(topics_queue, "upsert_system_message", upsert)
    monkeypatch.setattr(topics_queue.config, "moderator_group_id", -100999)

    await topics_queue._render_schedule_inner(bot, session, force_reconcile=True)

    bot.edit_message_text.assert_awaited_once()
    bot.send_message.assert_awaited_once()
    bot.pin_chat_message.assert_awaited_once_with(-100999, 200, disable_notification=True)
    upsert.assert_awaited_once()


def test_day_header_today_and_tomorrow() -> None:
    from datetime import date

    today = date(2026, 6, 15)
    assert "Сегодня, 15 июня" in topics_queue._day_header(today, today)
    assert "Завтра, 16 июня" in topics_queue._day_header(date(2026, 6, 16), today)


def test_day_header_far_date_has_weekday() -> None:
    from datetime import date

    header = topics_queue._day_header(date(2026, 6, 22), date(2026, 6, 15))
    assert "22 июня, пн" in header


def test_day_header_past_date_marked_overdue() -> None:
    from datetime import date

    header = topics_queue._day_header(date(2026, 6, 14), date(2026, 6, 15))
    assert "Просрочено, 14 июня" in header


async def test_build_schedule_text_format() -> None:
    """_build_schedule_text formatting."""
    result_with_lines = topics_queue._build_schedule_text(["1. line"])
    assert "<b>Запланированные посты:</b>" in result_with_lines
    assert "1. line" in result_with_lines

    result_empty = topics_queue._build_schedule_text([])
    assert "<b>Запланированные посты:</b>" in result_empty
    assert "В данный момент ничего не запланировано." in result_empty
