"""Unit tests for services/topics.py using FakeBot (no real DB required)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from services import topics
from tests.helpers import (
    FakeSessionFactory,
    make_bot,
    make_forum_topic,
    make_media,
    make_sent_message,
    make_submission,
    make_user,
)
from db.models import UserTopic


# ── _build_topic_title ─────────────────────────────────────────────

def test_build_topic_title_with_username() -> None:
    user = make_user(full_name="Artist Name", username="artist")
    title = topics._build_topic_title(user, "pending")
    # No @username in title — format is "[LABEL] Full Name"
    assert "Artist Name" in title
    assert "@artist" not in title
    assert title.startswith("[")


def test_build_topic_title_without_username() -> None:
    user = make_user(full_name="Artist Name", username=None)
    title = topics._build_topic_title(user, "pending")
    assert "Artist Name" in title
    assert title.startswith("[")


def test_build_topic_title_editing_status() -> None:
    user = make_user(full_name="Name", username=None)
    title = topics._build_topic_title(user, "editing")
    # Label comes from topic_status_config (e.g. "✏️ РЕД")
    assert title.startswith("[")
    assert "Name" in title


def test_build_topic_title_trimmed_to_max_length() -> None:
    user = make_user(full_name="A" * 200, username="x" * 50)
    title = topics._build_topic_title(user, "published")
    assert len(title) <= 128


def test_build_topic_title_scheduled() -> None:
    user = make_user(full_name="Bob", username=None)
    title = topics._build_topic_title(user, "scheduled")
    assert "Bob" in title
    assert title.startswith("[")


# ── ensure_user_topic — existing topic ────────────────────────────

async def test_ensure_user_topic_returns_existing_topic_id() -> None:
    """If a UserTopic row already exists, return its topic_id without API calls."""
    bot = make_bot()
    session = AsyncMock()
    user = make_user(user_id=1)

    existing = UserTopic()
    existing.user_id = 1
    existing.topic_id = 99
    existing.current_status_key = "pending"

    with patch.object(topics, "get_user_topic", AsyncMock(return_value=existing)):
        result = await topics.ensure_user_topic(bot, session, user)

    assert result == 99
    bot.create_forum_topic.assert_not_awaited()


# ── ensure_user_topic — new topic ─────────────────────────────────

async def test_ensure_user_topic_creates_new_topic() -> None:
    """When no UserTopic row exists, create a forum topic and insert the row."""
    bot = make_bot()
    bot.create_forum_topic.return_value = make_forum_topic(77)
    bot.send_message.return_value = make_sent_message(555)

    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = 77  # INSERT succeeded
    session = AsyncMock()
    session.execute.return_value = mock_execute_result

    user = make_user(user_id=5, telegram_id=500, username="newuser", full_name="New User")

    with patch.object(topics, "get_user_topic", AsyncMock(return_value=None)):
        result = await topics.ensure_user_topic(bot, session, user)

    assert result == 77
    bot.create_forum_topic.assert_awaited_once()
    call_kwargs = bot.create_forum_topic.await_args
    assert call_kwargs.kwargs.get("name") or call_kwargs.args  # title was passed
    session.execute.assert_awaited_once()  # INSERT DO NOTHING


async def test_ensure_user_topic_posts_welcome_message_on_creation() -> None:
    bot = make_bot()
    bot.create_forum_topic.return_value = make_forum_topic(88)
    bot.send_message.return_value = make_sent_message(556)
    user = make_user(user_id=6, username="art")

    session = AsyncMock()  # scalar_one_or_none() returns MagicMock (truthy) by default

    with patch.object(topics, "get_user_topic", AsyncMock(return_value=None)):
        await topics.ensure_user_topic(bot, session=session, user=user)

    bot.send_message.assert_awaited_once()
    call_args = bot.send_message.await_args
    assert call_args.kwargs.get("message_thread_id") == 88


# ── request_topic_title_sync ───────────────────────────────────────

async def test_request_topic_title_sync_enqueues_without_telegram_call() -> None:
    session = AsyncMock()
    enqueue = AsyncMock()
    bot = make_bot()

    with patch.object(topics, "enqueue_topic_title_sync", enqueue):
        await topics.request_topic_title_sync(session, 42)

    enqueue.assert_awaited_once_with(session, 42)
    bot.edit_forum_topic.assert_not_awaited()


# ── post_submission_card ───────────────────────────────────────────

async def test_post_submission_card_text_only_sends_text_message() -> None:
    bot = make_bot()
    bot.send_message.return_value = make_sent_message(201)
    session = AsyncMock()
    sub = make_submission(sub_id=10, media=[], caption="Hello")

    with (
        patch.object(topics, "ensure_user_topic", AsyncMock(return_value=5)),
        patch.object(topics, "get_publication_by_submission", AsyncMock(return_value=None)),
        patch.object(topics, "_resolve_card_lock_owner", AsyncMock(return_value=None)),
        patch.object(topics, "save_topic_card_ids", AsyncMock()) as mock_save,
        patch.object(topics, "mark_card_rendered", AsyncMock()),
    ):
        media_ids, card_id = await topics.post_submission_card(bot, session, sub)

    assert media_ids == []
    assert card_id == 201
    mock_save.assert_awaited_once_with(session, 10, [], 201)
    bot.send_message.assert_awaited_once()


async def test_post_submission_card_single_photo_sends_photo() -> None:
    bot = make_bot()
    bot.send_photo.return_value = make_sent_message(301)
    bot.send_message.return_value = make_sent_message(302)
    session = AsyncMock()
    sub = make_submission(sub_id=11, media=[make_media(file_id="photo-1", media_type="photo")])

    with (
        patch.object(topics, "ensure_user_topic", AsyncMock(return_value=5)),
        patch.object(topics, "get_publication_by_submission", AsyncMock(return_value=None)),
        patch.object(topics, "_resolve_card_lock_owner", AsyncMock(return_value=None)),
        patch.object(topics, "save_topic_card_ids", AsyncMock()) as mock_save,
        patch.object(topics, "mark_card_rendered", AsyncMock()),
    ):
        media_ids, card_id = await topics.post_submission_card(bot, session, sub)

    assert media_ids == [301]
    assert card_id == 302
    bot.send_photo.assert_awaited_once()
    mock_save.assert_awaited_once_with(session, 11, [301], 302)


async def test_post_submission_card_media_group() -> None:
    bot = make_bot()
    bot.send_media_group.return_value = [make_sent_message(401), make_sent_message(402)]
    bot.send_message.return_value = make_sent_message(403)
    session = AsyncMock()
    sub = make_submission(
        sub_id=12,
        media=[
            make_media(media_id=1, file_id="f1", media_type="photo"),
            make_media(media_id=2, file_id="f2", media_type="video", sort_order=1),
        ],
    )

    with (
        patch.object(topics, "ensure_user_topic", AsyncMock(return_value=5)),
        patch.object(topics, "get_publication_by_submission", AsyncMock(return_value=None)),
        patch.object(topics, "_resolve_card_lock_owner", AsyncMock(return_value=None)),
        patch.object(topics, "save_topic_card_ids", AsyncMock()) as mock_save,
        patch.object(topics, "mark_card_rendered", AsyncMock()),
    ):
        media_ids, card_id = await topics.post_submission_card(bot, session, sub)

    assert media_ids == [401, 402]
    assert card_id == 403
    mock_save.assert_awaited_once_with(session, 12, [401, 402], 403)


# ── update_submission_card ─────────────────────────────────────────

async def test_update_submission_card_no_card_id_is_noop() -> None:
    bot = make_bot()
    sub = make_submission(sub_id=20)
    sub.topic_card_message_id = None

    result = await topics.update_submission_card(bot, AsyncMock(), sub)

    assert result is True
    bot.edit_message_text.assert_not_awaited()


def _card_topic(user_id: int = 1, status_key: str = "pending") -> UserTopic:
    topic = UserTopic()
    topic.user_id = user_id
    topic.topic_id = 10
    topic.current_status_key = status_key
    return topic


def _patched_card_env(lock_owner=None):
    """Patch the DB lookups update_submission_card performs."""
    return (
        patch.object(topics, "get_user_topic", AsyncMock(return_value=_card_topic())),
        patch.object(topics, "get_publication_by_submission", AsyncMock(return_value=None)),
        patch.object(topics, "_resolve_card_lock_owner", AsyncMock(return_value=lock_owner)),
    )


async def test_update_submission_card_edits_message() -> None:
    bot = make_bot()
    sub = make_submission(sub_id=21)
    sub.topic_card_message_id = 500
    sub.user_id = 1

    a, b, c = _patched_card_env()
    with a, b, c:
        result = await topics.update_submission_card(bot, AsyncMock(), sub)

    assert result is True
    bot.edit_message_text.assert_awaited_once()


async def test_update_submission_card_reads_lock_owner_from_db() -> None:
    """The lock indicator comes from edit_locks, not from the caller."""
    bot = make_bot()
    sub = make_submission(sub_id=22, status="pending")
    sub.topic_card_message_id = 501
    sub.user_id = 1

    a, b, c = _patched_card_env(lock_owner=make_user(username="moduser"))
    with a, b, c:
        result = await topics.update_submission_card(bot, AsyncMock(), sub)

    assert result is True
    kb = bot.edit_message_text.await_args.kwargs.get("reply_markup")
    assert kb is not None
    buttons_flat = [btn for row in kb.inline_keyboard for btn in row]
    assert any("moduser" in (btn.text or "") for btn in buttons_flat)
    # A locked card must not offer the Edit deep link to another moderator
    assert not any(btn.url for btn in buttons_flat)


async def test_update_submission_card_lock_owner_without_username_stays_locked() -> None:
    """A moderator with no @username must not make the card look free."""
    bot = make_bot()
    sub = make_submission(sub_id=23, status="pending")
    sub.topic_card_message_id = 502
    sub.user_id = 1

    a, b, c = _patched_card_env(lock_owner=make_user(username=None))
    with a, b, c:
        await topics.update_submission_card(bot, AsyncMock(), sub)

    kb = bot.edit_message_text.await_args.kwargs.get("reply_markup")
    buttons_flat = [btn for row in kb.inline_keyboard for btn in row]
    assert not any(btn.url for btn in buttons_flat)


async def test_resolve_card_lock_owner_returns_none_for_terminal_status() -> None:
    sub = make_submission(sub_id=24, status="published")
    session = AsyncMock()

    assert await topics._resolve_card_lock_owner(session, sub) is None
    session.execute.assert_not_awaited()


async def test_update_submission_card_retries_after_flood_control() -> None:
    """A flood-control hit must not freeze the card in its previous state."""
    bot = make_bot()
    bot.edit_message_text = AsyncMock(
        side_effect=[TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=1), None]
    )
    sub = make_submission(sub_id=25)
    sub.topic_card_message_id = 503
    sub.user_id = 1

    a, b, c = _patched_card_env()
    with a, b, c, patch.object(topics.asyncio, "sleep", AsyncMock()):
        result = await topics.update_submission_card(bot, AsyncMock(), sub)

    assert result is True
    assert bot.edit_message_text.await_count == 2


async def test_finalize_submission_card_retries_after_flood_control() -> None:
    bot = make_bot()
    bot.edit_message_text = AsyncMock(
        side_effect=[TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=1), None]
    )
    sub = make_submission(sub_id=26, status="published")
    sub.topic_card_message_id = 504

    with (
        patch.object(topics, "get_publication_by_submission", AsyncMock(return_value=None)),
        patch.object(topics.asyncio, "sleep", AsyncMock()),
    ):
        result = await topics.finalize_submission_card(bot, AsyncMock(), sub)

    assert result is True
    assert bot.edit_message_text.await_count == 2


async def test_finalize_submission_card_treats_not_modified_as_success() -> None:
    bot = make_bot()
    bot.edit_message_text = AsyncMock(
        side_effect=TelegramBadRequest(
            method=MagicMock(),
            message="Bad Request: message is not modified: specified new message content",
        )
    )
    sub = make_submission(sub_id=27, status="cancelled")
    sub.topic_card_message_id = 505

    with patch.object(topics, "get_publication_by_submission", AsyncMock(return_value=None)):
        result = await topics.finalize_submission_card(bot, AsyncMock(), sub)

    assert result is True


# ── finalize_submission_card ───────────────────────────────────────

async def test_finalize_submission_card_removes_keyboard() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=30, status="published")
    sub.topic_card_message_id = 600

    with patch.object(topics, "get_publication_by_submission", AsyncMock(return_value=None)):
        result = await topics.finalize_submission_card(bot, session, sub)

    assert result is True
    bot.edit_message_text.assert_awaited_once()
    call_kwargs = bot.edit_message_text.await_args.kwargs
    assert call_kwargs.get("reply_markup") is None


async def test_finalize_submission_card_no_card_id_is_noop() -> None:
    bot = make_bot()
    sub = make_submission(sub_id=31)
    sub.topic_card_message_id = None

    result = await topics.finalize_submission_card(bot, AsyncMock(), sub)

    assert result is True
    bot.edit_message_text.assert_not_awaited()


# ── delete_submission_card ─────────────────────────────────────────

async def test_delete_submission_card_deletes_all_messages() -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=40)
    sub.topic_media_message_ids = [701, 702]
    sub.topic_card_message_id = 703

    with patch.object(topics, "clear_topic_card_ids", AsyncMock()) as mock_clear:
        result = await topics.delete_submission_card(bot, session, sub)

    assert result is True
    assert bot.delete_message.await_count == 3
    mock_clear.assert_awaited_once_with(session, 40)


async def test_delete_submission_card_old_message_treated_as_success() -> None:
    """Messages older than 48h ('message can't be deleted') are benign, not failures."""
    from aiogram.exceptions import TelegramBadRequest

    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=42)
    sub.topic_media_message_ids = [801, 802]
    sub.topic_card_message_id = 803
    bot.delete_message.side_effect = TelegramBadRequest(
        method=MagicMock(), message="message can't be deleted"
    )

    with patch.object(topics, "clear_topic_card_ids", AsyncMock()) as mock_clear:
        result = await topics.delete_submission_card(bot, session, sub)

    assert result is True
    mock_clear.assert_awaited_once_with(session, 42)
    # The stale card (803) is flagged as outdated with its keyboard stripped;
    # orphaned media (801, 802) are left untouched.
    bot.edit_message_text.assert_awaited_once()
    edit_kwargs = bot.edit_message_text.await_args.kwargs
    assert edit_kwargs["message_id"] == 803
    assert edit_kwargs["reply_markup"] is None


async def test_delete_submission_card_no_ids_is_noop() -> None:
    bot = make_bot()
    sub = make_submission(sub_id=41)
    sub.topic_media_message_ids = None
    sub.topic_card_message_id = None

    result = await topics.delete_submission_card(bot, AsyncMock(), sub)

    assert result is True
    bot.delete_message.assert_not_awaited()


# ── ensure_general_topic_nav ───────────────────────────────────────

async def test_ensure_general_topic_nav_creates_pin_when_no_record(monkeypatch) -> None:
    bot = make_bot()
    bot.send_message.return_value = make_sent_message(800)
    session = AsyncMock()

    monkeypatch.setattr(topics, "get_system_message", AsyncMock(return_value=None))
    upsert = AsyncMock()
    monkeypatch.setattr(topics, "upsert_system_message", upsert)

    await topics.ensure_general_topic_nav(bot, session)

    bot.send_message.assert_awaited_once()
    bot.pin_chat_message.assert_awaited_once()
    upsert.assert_awaited_once()


async def test_ensure_general_topic_nav_edits_when_record_exists(monkeypatch) -> None:
    bot = make_bot()
    session = AsyncMock()

    existing = SimpleNamespace(chat_id=-100333, message_id=800)
    monkeypatch.setattr(topics, "get_system_message", AsyncMock(return_value=existing))

    await topics.ensure_general_topic_nav(bot, session)

    bot.edit_message_text.assert_awaited_once()
    bot.send_message.assert_not_awaited()
    bot.pin_chat_message.assert_not_awaited()


# ── ensure_user_topic — passes icon kwargs ─────────────────────────

async def test_ensure_user_topic_passes_icon_color_and_emoji_to_create_forum_topic(monkeypatch) -> None:
    bot = make_bot()
    bot.create_forum_topic.return_value = make_forum_topic(90)
    bot.send_message.return_value = make_sent_message(901)

    session = AsyncMock()
    user = make_user(user_id=7, telegram_id=700, full_name="Icon User")

    from core.topic_status_config import TopicStatusStyle

    style = TopicStatusStyle(label="Ждёт", icon_color=7322096, icon_custom_emoji_id="emoji123")
    monkeypatch.setattr(topics, "_get_status_style", lambda _: style)

    with (
        patch.object(topics, "get_user_topic", AsyncMock(return_value=None)),
    ):
        await topics.ensure_user_topic(bot, session, user)

    create_kwargs = bot.create_forum_topic.await_args.kwargs
    assert create_kwargs.get("icon_color") == 7322096
    assert create_kwargs.get("icon_custom_emoji_id") == "emoji123"


async def test_ensure_user_topic_omits_icon_kwargs_when_style_has_none(monkeypatch) -> None:
    bot = make_bot()
    bot.create_forum_topic.return_value = make_forum_topic(91)
    bot.send_message.return_value = make_sent_message(911)

    session = AsyncMock()
    user = make_user(user_id=8, telegram_id=800, full_name="Plain User")

    from core.topic_status_config import TopicStatusStyle

    style = TopicStatusStyle(label="Ждёт", icon_color=None, icon_custom_emoji_id=None)
    monkeypatch.setattr(topics, "_get_status_style", lambda _: style)

    with (
        patch.object(topics, "get_user_topic", AsyncMock(return_value=None)),
    ):
        await topics.ensure_user_topic(bot, session, user)

    create_kwargs = bot.create_forum_topic.await_args.kwargs
    assert "icon_color" not in create_kwargs
    assert "icon_custom_emoji_id" not in create_kwargs


# ── ensure_general_topic_nav — not modified branch ────────────────

async def test_ensure_general_topic_nav_not_modified_is_noop(monkeypatch) -> None:
    """edit_message_text raising 'not modified' should not propagate — function returns cleanly."""
    from aiogram.exceptions import TelegramAPIError

    bot = make_bot()
    session = AsyncMock()
    existing = SimpleNamespace(chat_id=-100333, message_id=800)
    api_error = TelegramAPIError(method=MagicMock(), message="message is not modified")
    bot.edit_message_text.side_effect = api_error

    monkeypatch.setattr(topics, "get_system_message", AsyncMock(return_value=existing))

    # Should not raise
    await topics.ensure_general_topic_nav(bot, session)


# ── ensure_general_topic_nav — message deleted, recreates ─────────

async def test_ensure_general_topic_nav_recreates_when_message_deleted(monkeypatch) -> None:
    """When edit fails with 'not found', a new message is sent and upserted."""
    from aiogram.exceptions import TelegramAPIError

    bot = make_bot()
    bot.send_message.return_value = make_sent_message(900)
    session = AsyncMock()
    existing = SimpleNamespace(chat_id=-100333, message_id=800)
    api_error = TelegramAPIError(method=MagicMock(), message="message to edit not found")
    bot.edit_message_text.side_effect = api_error

    monkeypatch.setattr(topics, "get_system_message", AsyncMock(return_value=existing))
    upsert = AsyncMock()
    monkeypatch.setattr(topics, "upsert_system_message", upsert)

    await topics.ensure_general_topic_nav(bot, session)

    bot.send_message.assert_awaited_once()
    upsert.assert_awaited_once()


# ── repost_submission_card ──────────────────────────────────────────

async def test_repost_submission_card_deletes_and_reposts() -> None:
    """repost_submission_card calls delete then post, returns post result."""
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(
        sub_id=50,
        media=[make_media(file_id="f1")],
        topic_media_message_ids=[101, 102],
        topic_card_message_id=200,
    )

    expected_media_ids = [301]
    expected_card_id = 302

    with (
        patch.object(topics, "delete_submission_card", AsyncMock(return_value=True)) as mock_delete,
        patch.object(topics, "post_submission_card", AsyncMock(return_value=(expected_media_ids, expected_card_id))) as mock_post,
    ):
        media_ids, card_id = await topics.repost_submission_card(bot, session, sub)

    assert media_ids == expected_media_ids
    assert card_id == expected_card_id
    mock_delete.assert_awaited_once_with(bot, session, sub)
    mock_post.assert_awaited_once_with(bot, session, sub)


# ── compute_topic_status_key ─────────────────────────────────────────

async def test_compute_topic_status_key_pending_priority() -> None:
    """A pending submission returns 'pending'."""
    sub = make_submission(sub_id=1, status="pending")
    mock_active = MagicMock()
    mock_active.scalars.return_value.all.return_value = [sub]
    mock_lock = MagicMock()
    mock_lock.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.side_effect = [mock_active, mock_lock]

    result = await topics.compute_topic_status_key(session, user_id=1)

    assert result == "pending"


async def test_compute_topic_status_key_editing_over_scheduled() -> None:
    """Editing (locked pending) takes priority over scheduled."""
    sub_editing = make_submission(sub_id=2, status="pending")
    sub_scheduled = make_submission(sub_id=3, status="scheduled")
    mock_active = MagicMock()
    mock_active.scalars.return_value.all.return_value = [sub_editing, sub_scheduled]
    mock_lock = MagicMock()
    mock_lock.scalars.return_value.all.return_value = ["2"]
    session = AsyncMock()
    session.execute.side_effect = [mock_active, mock_lock]

    result = await topics.compute_topic_status_key(session, user_id=1)

    assert result == "editing"


async def test_compute_topic_status_key_editing_over_pending() -> None:
    """Editing (locked pending) takes priority over a coexisting free pending post."""
    sub_editing = make_submission(sub_id=11, status="pending")
    sub_pending = make_submission(sub_id=12, status="pending")
    mock_active = MagicMock()
    mock_active.scalars.return_value.all.return_value = [sub_editing, sub_pending]
    mock_lock = MagicMock()
    mock_lock.scalars.return_value.all.return_value = ["11"]
    session = AsyncMock()
    session.execute.side_effect = [mock_active, mock_lock]

    result = await topics.compute_topic_status_key(session, user_id=1)

    assert result == "editing"


async def test_compute_topic_status_key_scheduled_only() -> None:
    """Only a scheduled submission returns 'scheduled'."""
    sub = make_submission(sub_id=4, status="scheduled")
    mock_active = MagicMock()
    mock_active.scalars.return_value.all.return_value = [sub]
    session = AsyncMock()
    session.execute.return_value = mock_active

    result = await topics.compute_topic_status_key(session, user_id=1)

    assert result == "scheduled"


async def test_compute_topic_status_key_free_pending_returns_pending() -> None:
    """A pending submission without an active lock returns 'pending'."""
    sub = make_submission(sub_id=5, status="pending")
    mock_active = MagicMock()
    mock_active.scalars.return_value.all.return_value = [sub]
    mock_lock = MagicMock()
    mock_lock.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.side_effect = [mock_active, mock_lock]

    result = await topics.compute_topic_status_key(session, user_id=1)

    assert result == "pending"


async def test_compute_topic_status_key_published_over_rejected() -> None:
    """Published takes priority over other terminal statuses."""
    sub_published = make_submission(sub_id=6, status="published")
    sub_rejected = make_submission(sub_id=7, status="rejected")
    mock_active = MagicMock()
    mock_active.scalars.return_value.all.return_value = []
    mock_terminal = MagicMock()
    mock_terminal.scalars.return_value.all.return_value = [sub_rejected, sub_published]
    session = AsyncMock()
    session.execute.side_effect = [mock_active, mock_terminal]

    result = await topics.compute_topic_status_key(session, user_id=1)

    assert result == "published"


async def test_compute_topic_status_key_fallback_to_most_recent_terminal() -> None:
    """Without published, returns the first terminal status (most recent by updated_at)."""
    sub_rejected = make_submission(sub_id=8, status="rejected")
    sub_cancelled = make_submission(sub_id=9, status="cancelled")
    mock_active = MagicMock()
    mock_active.scalars.return_value.all.return_value = []
    mock_terminal = MagicMock()
    mock_terminal.scalars.return_value.all.return_value = [sub_rejected, sub_cancelled]
    session = AsyncMock()
    session.execute.side_effect = [mock_active, mock_terminal]

    result = await topics.compute_topic_status_key(session, user_id=1)

    assert result == "rejected"


async def test_compute_topic_status_key_new_user() -> None:
    """A user with no submissions at all returns 'pending'."""
    mock_active = MagicMock()
    mock_active.scalars.return_value.all.return_value = []
    mock_terminal = MagicMock()
    mock_terminal.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.side_effect = [mock_active, mock_terminal]

    result = await topics.compute_topic_status_key(session, user_id=1)

    assert result == "pending"


async def test_compute_topic_status_key_expired_lock_treated_as_pending() -> None:
    """A pending submission with only expired locks returns 'pending'."""
    sub = make_submission(sub_id=10, status="pending")
    mock_active = MagicMock()
    mock_active.scalars.return_value.all.return_value = [sub]
    mock_lock = MagicMock()
    mock_lock.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.side_effect = [mock_active, mock_lock]

    result = await topics.compute_topic_status_key(session, user_id=1)

    assert result == "pending"


# ── probe_submission_card ───────────────────────────────────────────

async def test_probe_submission_card_returns_true_when_card_exists() -> None:
    """edit_message_text succeeds → returns True."""
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=20, status="pending")
    sub.topic_card_message_id = 500

    with (
        patch.object(topics, "get_publication_by_submission", AsyncMock(return_value=None)),
        patch.object(topics, "_resolve_card_lock_owner", AsyncMock(return_value=None)),
    ):
        result = await topics.probe_submission_card(bot, session, sub)

    assert result is True
    bot.edit_message_text.assert_awaited_once()


async def test_probe_submission_card_returns_false_when_card_gone() -> None:
    """TelegramBadRequest with 'message to edit not found' → returns False."""
    from aiogram.exceptions import TelegramBadRequest

    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=21, status="pending")
    sub.topic_card_message_id = 500
    bot.edit_message_text.side_effect = TelegramBadRequest(
        method=MagicMock(), message="message to edit not found"
    )

    with (
        patch.object(topics, "get_publication_by_submission", AsyncMock(return_value=None)),
        patch.object(topics, "_resolve_card_lock_owner", AsyncMock(return_value=None)),
    ):
        result = await topics.probe_submission_card(bot, session, sub)

    assert result is False


async def test_probe_submission_card_raises_retry_after() -> None:
    """TelegramRetryAfter propagates without being caught."""
    from aiogram.exceptions import TelegramRetryAfter

    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=22, status="pending")
    sub.topic_card_message_id = 500
    bot.edit_message_text.side_effect = TelegramRetryAfter(
        method=MagicMock(), message="Too Many Requests", retry_after=30
    )

    with (
        patch.object(topics, "get_publication_by_submission", AsyncMock(return_value=None)),
        patch.object(topics, "_resolve_card_lock_owner", AsyncMock(return_value=None)),
        pytest.raises(TelegramRetryAfter),
    ):
        await topics.probe_submission_card(bot, session, sub)


# ── _edit_forum_topic_once — TOPIC_NOT_MODIFIED / backoff ───────────

@pytest.fixture(autouse=True)
def _reset_title_edit_backoff():
    """Keep the module-level title-edit deadline from leaking across tests."""
    topics._title_edit_backoff_until = 0.0
    yield
    topics._title_edit_backoff_until = 0.0


async def test_edit_forum_topic_once_topic_not_modified_is_success() -> None:
    """TelegramBadRequest with TOPIC_NOT_MODIFIED counts as applied."""
    from aiogram.exceptions import TelegramBadRequest

    bot = make_bot()
    bot.edit_forum_topic.side_effect = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: TOPIC_NOT_MODIFIED"
    )

    assert await topics._edit_forum_topic_once(
        bot, topic_id=10, name="[New Title]", icon_kwargs={}
    ) is True


async def test_edit_forum_topic_once_other_bad_request_raises() -> None:
    """TelegramBadRequest without TOPIC_NOT_MODIFIED is re-raised."""
    from aiogram.exceptions import TelegramBadRequest

    bot = make_bot()
    bot.edit_forum_topic.side_effect = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: TOPIC_CLOSED"
    )

    with pytest.raises(TelegramBadRequest):
        await topics._edit_forum_topic_once(
            bot, topic_id=10, name="[New Title]", icon_kwargs={}
        )


async def test_edit_forum_topic_once_flood_defers_without_sleeping() -> None:
    """Flood control must set a deadline, not sleep inside the tick."""
    from aiogram.exceptions import TelegramRetryAfter

    bot = make_bot()
    bot.edit_forum_topic.side_effect = TelegramRetryAfter(
        method=MagicMock(), message="Too Many Requests", retry_after=300
    )
    sleep_mock = AsyncMock()

    with patch.object(topics.asyncio, "sleep", sleep_mock):
        applied = await topics._edit_forum_topic_once(
            bot, topic_id=10, name="[New Title]", icon_kwargs={}
        )

    assert applied is False
    sleep_mock.assert_not_awaited()
    assert topics._title_edit_backoff_remaining() > 290


# ── durable topic-title sync worker ────────────────────────────────

def _pending_topic(*, version: int = 1, status: str = "pending") -> UserTopic:
    topic = UserTopic()
    topic.user_id = 1
    topic.topic_id = 10
    topic.current_status_key = status
    topic.title_sync_version = version
    topic.title_applied_version = version - 1
    topic.title_force_sync_version = 0
    return topic


async def test_process_next_topic_title_sync_applies_captured_revision() -> None:
    bot = make_bot()
    claim_session = AsyncMock()
    record_session = AsyncMock()
    user = make_user(user_id=1, full_name="Alice")
    topic = _pending_topic(version=3)
    pending_result = MagicMock()
    pending_result.first.return_value = (user, topic)
    claim_session.execute.return_value = pending_result
    factory = FakeSessionFactory(claim_session, record_session)
    mark_applied = AsyncMock()

    with (
        patch.object(
            topics,
            "compute_topic_status_key",
            AsyncMock(return_value="scheduled"),
        ),
        patch.object(
            topics, "mark_topic_title_sync_applied", mark_applied
        ),
    ):
        result = await topics.process_next_topic_title_sync(bot, factory)

    assert result is True
    bot.edit_forum_topic.assert_awaited_once()
    # Recorded in a second, separate transaction — never the one that claimed it
    mark_applied.assert_awaited_once_with(record_session, 1, 3, "scheduled")
    claim_session.commit.assert_awaited_once()
    record_session.commit.assert_awaited_once()


async def test_process_next_topic_title_sync_commits_claim_before_api_call() -> None:
    """The claim transaction must be closed before Telegram is touched."""
    bot = make_bot()
    claim_session = AsyncMock()
    record_session = AsyncMock()
    user = make_user(user_id=1)
    topic = _pending_topic(version=2)
    pending_result = MagicMock()
    pending_result.first.return_value = (user, topic)
    claim_session.execute.return_value = pending_result
    factory = FakeSessionFactory(claim_session, record_session)

    calls: list[str] = []
    claim_session.commit.side_effect = lambda: calls.append("claim_commit")
    bot.edit_forum_topic.side_effect = lambda **kw: calls.append("edit")

    with (
        patch.object(
            topics, "compute_topic_status_key", AsyncMock(return_value="scheduled")
        ),
        patch.object(topics, "mark_topic_title_sync_applied", AsyncMock()),
    ):
        await topics.process_next_topic_title_sync(bot, factory)

    assert calls == ["claim_commit", "edit"]


async def test_process_next_topic_title_sync_drains_noop_revision() -> None:
    bot = make_bot()
    session = AsyncMock()
    user = make_user(user_id=1)
    topic = _pending_topic(status="pending")
    pending_result = MagicMock()
    pending_result.first.return_value = (user, topic)
    empty_result = MagicMock()
    empty_result.first.return_value = None
    session.execute.side_effect = [pending_result, empty_result]
    factory = FakeSessionFactory(session)
    mark_applied = AsyncMock()

    with (
        patch.object(
            topics,
            "compute_topic_status_key",
            AsyncMock(return_value="pending"),
        ),
        patch.object(
            topics, "mark_topic_title_sync_applied", mark_applied
        ),
    ):
        result = await topics.process_next_topic_title_sync(bot, factory)

    assert result is False
    bot.edit_forum_topic.assert_not_awaited()
    # No-op revisions are drained inside the claim transaction itself
    mark_applied.assert_awaited_once_with(session, 1, 1, "pending")
    session.commit.assert_awaited_once()


async def test_process_next_topic_title_sync_applies_forced_matching_revision() -> None:
    bot = make_bot()
    session = AsyncMock()
    user = make_user(user_id=1)
    topic = _pending_topic(status="pending")
    topic.title_force_sync_version = 1
    pending_result = MagicMock()
    pending_result.first.return_value = (user, topic)
    session.execute.return_value = pending_result
    record_session = AsyncMock()
    factory = FakeSessionFactory(session, record_session)
    mark_applied = AsyncMock()

    with (
        patch.object(
            topics,
            "compute_topic_status_key",
            AsyncMock(return_value="pending"),
        ),
        patch.object(
            topics, "mark_topic_title_sync_applied", mark_applied
        ),
    ):
        result = await topics.process_next_topic_title_sync(bot, factory)

    assert result is True
    bot.edit_forum_topic.assert_awaited_once()
    mark_applied.assert_awaited_once_with(record_session, 1, 1, "pending")


async def test_process_next_topic_title_sync_keeps_failed_revision_pending() -> None:
    bot = make_bot()
    bot.edit_forum_topic.side_effect = RuntimeError("Telegram error")
    session = AsyncMock()
    user = make_user(user_id=1)
    topic = _pending_topic()
    pending_result = MagicMock()
    pending_result.first.return_value = (user, topic)
    session.execute.return_value = pending_result
    factory = FakeSessionFactory(session)
    mark_applied = AsyncMock()

    with (
        patch.object(
            topics,
            "compute_topic_status_key",
            AsyncMock(return_value="editing"),
        ),
        patch.object(
            topics, "mark_topic_title_sync_applied", mark_applied
        ),
    ):
        result = await topics.process_next_topic_title_sync(bot, factory)

    assert result is False
    mark_applied.assert_not_awaited()
    # Ошибка сдвигает дедлайн, чтобы следующий тик не долбил Telegram сразу
    assert topics._title_edit_backoff_remaining() > 0


async def test_process_next_topic_title_sync_skips_tick_while_deferred() -> None:
    """While the deadline holds, a tick returns at once and claims nothing."""
    bot = make_bot()
    session = AsyncMock()
    factory = FakeSessionFactory(session)
    topics._defer_title_edits(300)

    result = await topics.process_next_topic_title_sync(bot, factory)

    assert result is False
    session.execute.assert_not_awaited()
    bot.edit_forum_topic.assert_not_awaited()


async def test_reconcile_topic_titles_only_queues_detectable_drift() -> None:
    session = AsyncMock()
    users = [make_user(user_id=1), make_user(user_id=2)]
    users_result = MagicMock()
    users_result.scalars.return_value.all.return_value = users
    session.execute.return_value = users_result
    topic1 = _pending_topic(status="pending")
    topic2 = _pending_topic(status="published")
    topic2.user_id = 2
    ensure_pending = AsyncMock()

    with (
        patch.object(
            topics,
            "get_user_topic",
            AsyncMock(side_effect=[topic1, topic2]),
        ),
        patch.object(
            topics,
            "compute_topic_status_key",
            AsyncMock(side_effect=["editing", "published"]),
        ),
        patch.object(
            topics, "ensure_topic_title_sync_pending", ensure_pending
        ),
    ):
        result = await topics.reconcile_topic_titles(session)

    assert result == 1
    ensure_pending.assert_awaited_once_with(session, 1)


# ── reconcile_submission_cards ──────────────────────────────────────

def _reconcile_env(candidates, *, mark=None, clear=None):
    """Patch the DB access reconcile_submission_cards performs."""
    return (
        patch.object(topics, "list_cards_for_reconcile", AsyncMock(return_value=candidates)),
        patch.object(topics, "get_publication_by_submission", AsyncMock(return_value=None)),
        patch.object(topics, "_resolve_card_lock_owner", AsyncMock(return_value=None)),
        patch.object(
            topics,
            "mark_card_rendered_if_unchanged",
            mark or AsyncMock(return_value=True),
        ),
        patch.object(topics, "clear_topic_card_ids", clear or AsyncMock()),
    )


async def _drifted_sub(bot, sub_id: int = 40, status: str = "pending"):
    """A submission whose stored hash does not match its current render."""
    sub = make_submission(sub_id=sub_id, status=status)
    sub.topic_card_message_id = 700 + sub_id
    sub.card_rendered_hash = "stale-digest"
    return sub


async def test_reconcile_repaints_only_drifted_cards() -> None:
    bot = make_bot()
    drifted = await _drifted_sub(bot, sub_id=40)
    in_sync = make_submission(sub_id=41)
    in_sync.topic_card_message_id = 741

    # Compute the in-sync card's true digest so reconcile must skip it
    with (
        patch.object(topics, "get_publication_by_submission", AsyncMock(return_value=None)),
        patch.object(topics, "_resolve_card_lock_owner", AsyncMock(return_value=None)),
    ):
        text, kb = await topics._render_submission_card(bot, AsyncMock(), in_sync)
    in_sync.card_rendered_hash = topics._card_render_hash(text, kb)

    mark = AsyncMock()
    a, b, c, d, e = _reconcile_env([drifted, in_sync], mark=mark)
    with a, b, c, d, e:
        repaired = await topics.reconcile_submission_cards(bot, FakeSessionFactory(AsyncMock()))

    assert repaired == 1
    bot.edit_message_text.assert_awaited_once()
    assert bot.edit_message_text.await_args.kwargs["message_id"] == 740
    mark.assert_awaited_once()


async def test_reconcile_repaints_card_never_confirmed() -> None:
    """A NULL hash (pre-migration row or failed first edit) counts as drift."""
    bot = make_bot()
    sub = make_submission(sub_id=42)
    sub.topic_card_message_id = 742
    sub.card_rendered_hash = None

    a, b, c, d, e = _reconcile_env([sub])
    with a, b, c, d, e:
        repaired = await topics.reconcile_submission_cards(bot, FakeSessionFactory(AsyncMock()))

    assert repaired == 1


async def test_reconcile_honours_max_repairs() -> None:
    bot = make_bot()
    subs = [await _drifted_sub(bot, sub_id=50 + i) for i in range(4)]

    a, b, c, d, e = _reconcile_env(subs)
    with a, b, c, d, e, patch.object(topics.asyncio, "sleep", AsyncMock()):
        repaired = await topics.reconcile_submission_cards(bot, FakeSessionFactory(AsyncMock()), max_repairs=2)

    assert repaired == 2
    assert bot.edit_message_text.await_count == 2


async def test_reconcile_does_not_mark_hash_when_edit_fails() -> None:
    """A failed repair must stay pending for the next pass."""
    bot = make_bot()
    bot.edit_message_text = AsyncMock(
        side_effect=TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=600)
    )
    sub = await _drifted_sub(bot, sub_id=60)

    mark = AsyncMock()
    a, b, c, d, e = _reconcile_env([sub], mark=mark)
    with a, b, c, d, e:
        repaired = await topics.reconcile_submission_cards(bot, FakeSessionFactory(AsyncMock()))

    assert repaired == 0
    mark.assert_not_awaited()


async def test_reconcile_clears_ids_when_card_is_gone() -> None:
    bot = make_bot()
    bot.edit_message_text = AsyncMock(
        side_effect=TelegramBadRequest(
            method=MagicMock(), message="Bad Request: message to edit not found"
        )
    )
    sub = await _drifted_sub(bot, sub_id=61)

    clear = AsyncMock()
    a, b, c, d, e = _reconcile_env([sub], clear=clear)
    with a, b, c, d, e:
        repaired = await topics.reconcile_submission_cards(bot, FakeSessionFactory(AsyncMock()))

    assert repaired == 0
    clear.assert_awaited_once_with(ANY, 61)


async def test_reconcile_terminal_card_matches_finalize_hash() -> None:
    """finalize writes hash(text, None); reconcile must compute the same one."""
    bot = make_bot()
    sub = make_submission(sub_id=62, status="published")
    sub.topic_card_message_id = 762

    with patch.object(topics, "get_publication_by_submission", AsyncMock(return_value=None)):
        await topics.finalize_submission_card(bot, AsyncMock(), sub)
    finalize_text = bot.edit_message_text.await_args.kwargs["text"]
    sub.card_rendered_hash = topics._card_render_hash(finalize_text, None)

    bot.edit_message_text.reset_mock()
    a, b, c, d, e = _reconcile_env([sub])
    with a, b, c, d, e:
        repaired = await topics.reconcile_submission_cards(bot, FakeSessionFactory(AsyncMock()))

    assert repaired == 0
    bot.edit_message_text.assert_not_awaited()
