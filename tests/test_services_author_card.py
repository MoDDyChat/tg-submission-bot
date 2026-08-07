"""Tests for services/author_card.py — author card rendering logic."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from keyboards.moderator import author_card_kb
from services import author_card
from tests.helpers import make_bot, make_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_system_message(key: str, chat_id: int, message_id: int, payload: dict | None = None):
    row = MagicMock()
    row.key = key
    row.chat_id = chat_id
    row.message_id = message_id
    row.payload = payload or {}
    return row


def _make_topic(topic_id: int = 42):
    row = MagicMock()
    row.topic_id = topic_id
    return row


def _make_stats(
    *,
    total: int = 3,
    published: int = 1,
    rejected: int = 1,
    pending: int = 1,
    scheduled: int = 0,
    first_seen: datetime | None = None,
):
    stats = MagicMock()
    stats.total = total
    stats.published = published
    stats.rejected = rejected
    stats.pending = pending
    stats.scheduled = scheduled
    stats.first_seen = first_seen
    return stats


# ---------------------------------------------------------------------------
# build_author_card
# ---------------------------------------------------------------------------


async def test_build_author_card_contains_name_id_and_counts(monkeypatch) -> None:
    user = make_user(user_id=1, telegram_id=555, full_name="Artist Name", username="artist")
    stats = _make_stats(
        total=5, published=2, rejected=1, pending=1, scheduled=1,
        first_seen=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(author_card, "get_author_stats", AsyncMock(return_value=stats))
    monkeypatch.setattr(author_card.config, "timezone", "UTC")

    session = AsyncMock()
    text = await author_card.build_author_card(session, user)

    assert "Artist Name" in text
    assert "555" in text
    assert "5" in text  # total
    assert "05.01.2026" in text


async def test_build_author_card_ban_block_present_when_banned(monkeypatch) -> None:
    user = make_user(user_id=1, is_banned=True, ban_reason="Спам")
    stats = _make_stats()
    monkeypatch.setattr(author_card, "get_author_stats", AsyncMock(return_value=stats))
    monkeypatch.setattr(author_card.config, "timezone", "UTC")

    session = AsyncMock()
    text = await author_card.build_author_card(session, user)

    assert "Заблокирован" in text
    assert "Спам" in text


async def test_build_author_card_no_ban_block_when_not_banned(monkeypatch) -> None:
    user = make_user(user_id=1, is_banned=False)
    stats = _make_stats()
    monkeypatch.setattr(author_card, "get_author_stats", AsyncMock(return_value=stats))
    monkeypatch.setattr(author_card.config, "timezone", "UTC")

    session = AsyncMock()
    text = await author_card.build_author_card(session, user)

    assert "Заблокирован" not in text


async def test_build_author_card_note_block_present_when_note_set(monkeypatch) -> None:
    user = make_user(user_id=1)
    user.moderator_note = "Хороший автор"
    stats = _make_stats()
    monkeypatch.setattr(author_card, "get_author_stats", AsyncMock(return_value=stats))
    monkeypatch.setattr(author_card.config, "timezone", "UTC")

    session = AsyncMock()
    text = await author_card.build_author_card(session, user)

    assert "Хороший автор" in text


async def test_build_author_card_escapes_note_exactly_once(monkeypatch) -> None:
    """The note is stored as plain text (handlers/moderator/author_card.py) and
    escaped exactly once here — no double-escaping like ``&amp;amp;``."""
    user = make_user(user_id=1)
    user.moderator_note = "<b>note</b> & stuff"
    stats = _make_stats()
    monkeypatch.setattr(author_card, "get_author_stats", AsyncMock(return_value=stats))
    monkeypatch.setattr(author_card.config, "timezone", "UTC")

    session = AsyncMock()
    text = await author_card.build_author_card(session, user)

    assert "&lt;b&gt;note&lt;/b&gt; &amp; stuff" in text
    assert "<b>note</b>" not in text
    assert "&amp;amp;" not in text
    assert "&amp;lt;" not in text


async def test_build_author_card_escapes_name(monkeypatch) -> None:
    user = make_user(user_id=1, full_name="<b>hack</b>", username=None)
    stats = _make_stats()
    monkeypatch.setattr(author_card, "get_author_stats", AsyncMock(return_value=stats))
    monkeypatch.setattr(author_card.config, "timezone", "UTC")

    session = AsyncMock()
    text = await author_card.build_author_card(session, user)

    assert "&lt;b&gt;hack&lt;/b&gt;" in text
    assert "<b>hack</b>" not in text


async def test_build_author_card_does_not_escape_ban_reason_html(monkeypatch) -> None:
    """ban_reason may already contain HTML markup (moderator can format it) — passed as-is."""
    user = make_user(user_id=1, is_banned=True, ban_reason="<i>ban reason</i>")
    stats = _make_stats()
    monkeypatch.setattr(author_card, "get_author_stats", AsyncMock(return_value=stats))
    monkeypatch.setattr(author_card.config, "timezone", "UTC")

    session = AsyncMock()
    text = await author_card.build_author_card(session, user)

    assert "<i>ban reason</i>" in text
    assert "&lt;i&gt;" not in text


# ---------------------------------------------------------------------------
# render_author_card
# ---------------------------------------------------------------------------


async def test_render_author_card_no_user_or_topic_is_noop(monkeypatch) -> None:
    bot = make_bot()
    session = AsyncMock()
    monkeypatch.setattr(author_card, "get_user_by_id", AsyncMock(return_value=None))
    monkeypatch.setattr(author_card, "get_user_topic", AsyncMock(return_value=_make_topic()))

    await author_card.render_author_card(bot, session, user_id=1)

    bot.send_message.assert_not_awaited()
    bot.edit_message_text.assert_not_awaited()


async def test_render_author_card_without_record_sends_nothing(monkeypatch) -> None:
    """No registered card = nothing to edit: the render path must never post."""
    bot = make_bot()
    session = AsyncMock()
    user = make_user(user_id=1)
    topic = _make_topic(topic_id=42)

    monkeypatch.setattr(author_card, "get_user_by_id", AsyncMock(return_value=user))
    monkeypatch.setattr(author_card, "get_user_topic", AsyncMock(return_value=topic))
    monkeypatch.setattr(author_card, "get_system_message", AsyncMock(return_value=None))
    upsert = AsyncMock()
    monkeypatch.setattr(author_card, "upsert_system_message", upsert)

    await author_card.render_author_card(bot, session, user_id=1)

    bot.send_message.assert_not_awaited()
    bot.edit_message_text.assert_not_awaited()
    upsert.assert_not_awaited()


async def test_render_author_card_same_checksum_no_telegram_calls(monkeypatch) -> None:
    bot = make_bot()
    session = AsyncMock()
    user = make_user(user_id=1)
    topic = _make_topic(topic_id=42)
    text = "card text"
    cs = author_card._checksum(text, author_card_kb(user))
    existing = _make_system_message("user:1:card", -100999, 7, {"checksum": cs})

    monkeypatch.setattr(author_card, "get_user_by_id", AsyncMock(return_value=user))
    monkeypatch.setattr(author_card, "get_user_topic", AsyncMock(return_value=topic))
    monkeypatch.setattr(author_card, "get_system_message", AsyncMock(return_value=existing))
    monkeypatch.setattr(author_card, "build_author_card", AsyncMock(return_value=text))

    await author_card.render_author_card(bot, session, user_id=1)

    bot.edit_message_text.assert_not_awaited()
    bot.send_message.assert_not_awaited()


async def test_render_author_card_message_not_modified_still_writes_checksum(monkeypatch) -> None:
    bot = make_bot()
    session = AsyncMock()
    user = make_user(user_id=1)
    topic = _make_topic(topic_id=42)
    text = "card text"
    existing = _make_system_message("user:1:card", -100999, 7, {"checksum": "old"})

    from aiogram.exceptions import TelegramAPIError

    api_error = TelegramAPIError(method=MagicMock(), message="message is not modified")
    bot.edit_message_text.side_effect = api_error

    monkeypatch.setattr(author_card, "get_user_by_id", AsyncMock(return_value=user))
    monkeypatch.setattr(author_card, "get_user_topic", AsyncMock(return_value=topic))
    monkeypatch.setattr(author_card, "get_system_message", AsyncMock(return_value=existing))
    monkeypatch.setattr(author_card, "build_author_card", AsyncMock(return_value=text))
    upsert = AsyncMock()
    monkeypatch.setattr(author_card, "upsert_system_message", upsert)

    await author_card.render_author_card(bot, session, user_id=1)

    upsert.assert_awaited_once()
    cs = author_card._checksum(text, author_card_kb(user))
    assert upsert.call_args.kwargs["payload"] == {"checksum": cs}


async def test_render_author_card_drops_record_on_message_not_found(monkeypatch) -> None:
    """A lost card cannot be re-sent (it was the topic's first message) — drop the row."""
    bot = make_bot()
    session = AsyncMock()
    user = make_user(user_id=1)
    topic = _make_topic(topic_id=42)
    text = "card text"
    existing = _make_system_message("user:1:card", -100999, 99, {"checksum": "old"})

    from aiogram.exceptions import TelegramAPIError

    api_error = TelegramAPIError(method=MagicMock(), message="message to edit not found")
    bot.edit_message_text.side_effect = api_error

    monkeypatch.setattr(author_card, "get_user_by_id", AsyncMock(return_value=user))
    monkeypatch.setattr(author_card, "get_user_topic", AsyncMock(return_value=topic))
    monkeypatch.setattr(author_card, "get_system_message", AsyncMock(return_value=existing))
    monkeypatch.setattr(author_card, "build_author_card", AsyncMock(return_value=text))
    upsert = AsyncMock()
    monkeypatch.setattr(author_card, "upsert_system_message", upsert)
    delete = AsyncMock()
    monkeypatch.setattr(author_card, "delete_system_message", delete)

    await author_card.render_author_card(bot, session, user_id=1)

    bot.edit_message_text.assert_awaited_once()
    bot.send_message.assert_not_awaited()
    delete.assert_awaited_once_with(session, "user:1:card")
    upsert.assert_not_awaited()


async def test_render_author_card_keeps_opening_flag(monkeypatch) -> None:
    """The ``opening`` marker must survive a re-render, or the backfill would redo the topic."""
    bot = make_bot()
    session = AsyncMock()
    user = make_user(user_id=1)
    topic = _make_topic(topic_id=42)
    text = "card text"
    existing = _make_system_message(
        "user:1:card", -100999, 7, {"checksum": "old", "opening": True}
    )

    monkeypatch.setattr(author_card, "get_user_by_id", AsyncMock(return_value=user))
    monkeypatch.setattr(author_card, "get_user_topic", AsyncMock(return_value=topic))
    monkeypatch.setattr(author_card, "get_system_message", AsyncMock(return_value=existing))
    monkeypatch.setattr(author_card, "build_author_card", AsyncMock(return_value=text))
    upsert = AsyncMock()
    monkeypatch.setattr(author_card, "upsert_system_message", upsert)

    await author_card.render_author_card(bot, session, user_id=1)

    assert upsert.call_args.kwargs["payload"]["opening"] is True


# ---------------------------------------------------------------------------
# create_author_card_message
# ---------------------------------------------------------------------------


async def test_create_author_card_message_sends_pins_and_registers(monkeypatch) -> None:
    bot = make_bot()
    session = AsyncMock()
    user = make_user(user_id=1)

    monkeypatch.setattr(author_card, "build_author_card", AsyncMock(return_value="card text"))
    upsert = AsyncMock()
    monkeypatch.setattr(author_card, "upsert_system_message", upsert)
    monkeypatch.setattr(author_card.config, "moderator_group_id", -100999)

    message_id = await author_card.create_author_card_message(bot, session, user, topic_id=42)

    assert message_id == bot.send_message.return_value.message_id
    assert bot.send_message.call_args.kwargs["message_thread_id"] == 42
    bot.pin_chat_message.assert_awaited_once()
    assert upsert.call_args.kwargs["payload"]["opening"] is True


async def test_create_author_card_message_survives_pin_failure(monkeypatch) -> None:
    """A group without pin rights still gets its card — only the pin is lost."""
    bot = make_bot()
    session = AsyncMock()
    user = make_user(user_id=1)

    from aiogram.exceptions import TelegramAPIError

    bot.pin_chat_message.side_effect = TelegramAPIError(
        method=MagicMock(), message="not enough rights"
    )
    monkeypatch.setattr(author_card, "build_author_card", AsyncMock(return_value="card text"))
    upsert = AsyncMock()
    monkeypatch.setattr(author_card, "upsert_system_message", upsert)
    monkeypatch.setattr(author_card.config, "moderator_group_id", -100999)

    await author_card.create_author_card_message(bot, session, user, topic_id=42)

    upsert.assert_awaited_once()


# ---------------------------------------------------------------------------
# request_author_card / author_card_render_job / reconcile job
# ---------------------------------------------------------------------------


def test_request_author_card_no_io() -> None:
    author_card._dirty_user_ids.clear()
    author_card.request_author_card(7)
    assert 7 in author_card._dirty_user_ids
    author_card._dirty_user_ids.clear()


async def test_render_job_caps_batch_and_clears_taken(monkeypatch) -> None:
    author_card._dirty_user_ids.clear()
    for uid in range(1, 15):
        author_card._dirty_user_ids.add(uid)

    bot = make_bot()
    render_calls: list[int] = []

    async def _fake_render(bot_, session, user_id, **kwargs):
        render_calls.append(user_id)

    monkeypatch.setattr(author_card, "render_author_card", _fake_render)
    monkeypatch.setattr(author_card, "_RENDER_DELAY", 0)

    class _Ctx:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            return False

    session_factory = MagicMock(side_effect=lambda: _Ctx())

    await author_card.author_card_render_job(bot, session_factory)

    assert len(render_calls) == author_card._MAX_CARDS_PER_PASS
    assert render_calls == list(range(1, author_card._MAX_CARDS_PER_PASS + 1))
    # Remaining dirty ids stay for the next tick
    assert author_card._dirty_user_ids == set(
        range(author_card._MAX_CARDS_PER_PASS + 1, 15)
    )
    author_card._dirty_user_ids.clear()
