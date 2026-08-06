"""Unit tests for services/topic_notifications.py."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from services import topic_notifications
from tests.helpers import make_bot, make_submission, make_user
from db.models import UserTopic


# ── Helpers ────────────────────────────────────────────────────────

def _make_topic(user_id: int = 1, topic_id: int = 42) -> UserTopic:
    t = UserTopic()
    t.user_id = user_id
    t.topic_id = topic_id
    t.current_status_key = "pending"
    return t


# ── _mod_display ────────────────────────────────────────────────────

def test_mod_display_with_username() -> None:
    user = make_user(username="alice")
    assert topic_notifications._mod_display(user) == "@alice"


def test_mod_display_without_username() -> None:
    user = make_user(username=None, full_name="Alice B")
    assert topic_notifications._mod_display(user) == "Alice B"


def test_mod_display_escapes_html() -> None:
    user = make_user(username=None, full_name="<Alice>")
    assert "&lt;Alice&gt;" in topic_notifications._mod_display(user)


# ── _get_topic_id — no topic row ─────────────────────────────────

async def test_get_topic_id_returns_none_when_no_row() -> None:
    sub = make_submission(sub_id=5)
    session = AsyncMock()
    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=None)):
        result = await topic_notifications._get_topic_id(session, sub)
    assert result is None


# ── _get_topic_id — existing topic row ──────────────────────────

async def test_get_topic_id_returns_topic_id() -> None:
    sub = make_submission(sub_id=5)
    session = AsyncMock()
    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=77))):
        result = await topic_notifications._get_topic_id(session, sub)
    assert result == 77


# ── notify_caption_changed — diff content ────────────────────────

async def test_notify_caption_changed_includes_diff() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=7)
    mod = make_user(username="editor")

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=5))):
        await topic_notifications.notify_caption_changed(
            bot, session, sub, mod,
            old_caption="старое описание",
            new_caption="новое описание",
        )

    text = bot.send_message.call_args.kwargs["text"]
    assert "Было" in text
    assert "Стало" in text
    assert "старое описание" in text
    assert "новое описание" in text


# ── notify_tags_changed — diff content ───────────────────────────

async def test_notify_tags_changed_shows_added_and_removed() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=3)
    mod = make_user(username="tagger")

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=8))):
        await topic_notifications.notify_tags_changed(
            bot, session, sub, mod,
            old_tags=["OldTag"],
            new_tags=["NewTag"],
        )

    text = bot.send_message.call_args.kwargs["text"]
    assert "+#NewTag" in text
    assert "−#OldTag" in text


# ── notify_rejected — silent vs with reason ──────────────────────

async def test_notify_rejected_with_reason() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=4)
    mod = make_user(username="judge")

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=9))):
        await topic_notifications.notify_rejected(
            bot, session, sub, mod, reason="NSFW", silent=False
        )

    text = bot.send_message.call_args.kwargs["text"]
    assert "NSFW" in text
    assert "тихо" not in text


async def test_notify_rejected_silent() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=4)
    mod = make_user(username="judge")

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=9))):
        await topic_notifications.notify_rejected(
            bot, session, sub, mod, reason=None, silent=True
        )

    text = bot.send_message.call_args.kwargs["text"]
    assert "тихо" in text


# ── notify_published — by moderator vs scheduled ─────────────────

async def test_notify_published_by_moderator() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=6)
    mod = make_user(username="publisher")

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=11))):
        await topic_notifications.notify_published(bot, session, sub, by_moderator=mod)

    text = bot.send_message.call_args.kwargs["text"]
    assert "@publisher" in text
    assert "вручную" in text


async def test_notify_published_scheduled() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=6)

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=11))):
        await topic_notifications.notify_published(bot, session, sub, by_moderator=None)

    text = bot.send_message.call_args.kwargs["text"]
    assert "расписанию" in text


# ── notify_viewer_cancelled ───────────────────────────────────────

async def test_notify_viewer_cancelled_sends_message() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=20)

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=55))):
        await topic_notifications.notify_viewer_cancelled(bot, session, sub)

    bot.send_message.assert_awaited_once()
    text = bot.send_message.call_args.kwargs["text"]
    assert "20" in text


# ── notify_contact_from_moderator ────────────────────────────────

async def test_notify_contact_from_moderator_escapes_html() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=1)
    mod = make_user(username="mod")

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=3))):
        await topic_notifications.notify_contact_from_moderator(
            bot, session, sub, mod, text="<b>hello</b>"
        )

    text = bot.send_message.call_args.kwargs["text"]
    assert "&lt;b&gt;" in text


# ── notify_contact_from_viewer ───────────────────────────────────

async def test_notify_contact_from_viewer_sends_message() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=2)

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=4))):
        await topic_notifications.notify_contact_from_viewer(bot, session, sub, text="reply text")

    text = bot.send_message.call_args.kwargs["text"]
    assert "reply text" in text


# ── Telegram error is swallowed ───────────────────────────────────

async def test_telegram_error_is_swallowed() -> None:
    """A TelegramAPIError in _send should not propagate."""
    from aiogram.exceptions import TelegramAPIError

    bot = make_bot()
    bot.send_message = AsyncMock(side_effect=TelegramAPIError(method=None, message="err"))  # type: ignore[arg-type]
    session = AsyncMock()
    sub = make_submission(sub_id=1)

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=1))):
        # Should not raise
        await topic_notifications.notify_viewer_cancelled(bot, session, sub)

    bot.send_message.assert_awaited_once()


# ── notify_media_changed ─────────────────────────────────────────

async def test_notify_media_changed_sends_correct_text() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=7)
    moderator = make_user(username="alice")

    with (
        patch.object(topic_notifications, "_get_topic_id", AsyncMock(return_value=42)),
        patch.object(topic_notifications, "_send", AsyncMock()) as mock_send,
    ):
        await topic_notifications.notify_media_changed(bot, session, sub, moderator)

    mock_send.assert_called_once()
    sent_text = mock_send.call_args.args[2]
    assert "@alice" in sent_text
    assert "7" in sent_text


# ── notify_scheduled time formatting ─────────────────────────────

async def test_notify_scheduled_includes_time() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=8)
    mod = make_user(username="scheduler")
    pub_at = datetime(2026, 6, 15, 10, 30, tzinfo=timezone.utc)

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=20))):
        await topic_notifications.notify_scheduled(bot, session, sub, mod, pub_at)

    text = bot.send_message.call_args.kwargs["text"]
    assert "15.06.2026" in text


# ── notify_rescheduled ────────────────────────────────────────────

async def test_notify_rescheduled_includes_time() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=9)
    mod = make_user(username="rescheduler")
    pub_at = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=25))):
        await topic_notifications.notify_rescheduled(bot, session, sub, mod, pub_at)

    text = bot.send_message.call_args.kwargs["text"]
    # date portion is stable (time portion depends on configured timezone)
    assert "20.07.2026" in text
    assert "@rescheduler" in text
    assert "#9" in text


# ── notify_unscheduled ────────────────────────────────────────────

async def test_notify_unscheduled_sends_message() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=10)
    mod = make_user(username="unscheduler")

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=30))):
        await topic_notifications.notify_unscheduled(bot, session, sub, mod)

    text = bot.send_message.call_args.kwargs["text"]
    assert "@unscheduler" in text
    assert "#10" in text
    assert "снял" in text
    assert "с расписания" in text


# ── notify_banned ─────────────────────────────────────────────────

async def test_notify_banned_includes_reason() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=11)
    mod = make_user(username="banhammer")

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=35))):
        await topic_notifications.notify_banned(bot, session, sub, mod, reason="Spam")

    text = bot.send_message.call_args.kwargs["text"]
    assert "@banhammer" in text
    assert "#11" in text
    assert "Spam" in text
    assert "заблокировал" in text


# ── notify_unbanned ───────────────────────────────────────────────

async def test_notify_unbanned_sends_message() -> None:
    bot = make_bot()
    session = AsyncMock()
    unbanned_user = make_user(username="unbanned_user", user_id=20)
    mod = make_user(username="pardner")

    with patch.object(topic_notifications, "get_user_topic", AsyncMock(return_value=_make_topic(topic_id=40))):
        await topic_notifications.notify_unbanned(bot, session, unbanned_user, mod)

    text = bot.send_message.call_args.kwargs["text"]
    assert "@pardner" in text
    assert "@unbanned_user" in text
    assert "разблокировал" in text
