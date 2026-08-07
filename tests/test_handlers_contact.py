"""Unit tests for handlers/contact.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramAPIError

import core.messages as msg
from handlers import contact
from states.contact import ContactViewer
from tests.helpers import FakeState, make_callback, make_message, make_submission, make_user


def _mark_from_bot(replied, bot) -> None:
    """Make ``replied`` look like it was sent by ``bot`` (see _is_reply_to_bot)."""
    replied.from_user = SimpleNamespace(id=bot.id, is_bot=True)


def _mark_from_viewer(replied, viewer_id: int = 20) -> None:
    """Make ``replied`` look like it was sent by a regular (non-bot) user."""
    replied.from_user = SimpleNamespace(id=viewer_id, is_bot=False)


# ── handle_contact_start ──────────────────────────────────────────────

async def test_contact_start_sets_state(monkeypatch) -> None:
    state = FakeState()
    callback = make_callback()

    await contact.handle_contact_start(callback, AsyncMock(sub_id=3), state)

    assert state.state == ContactViewer.writing_message
    assert state.data["contact_sub_id"] == 3
    callback.answer.assert_awaited()


# ── handle_moderator_message ──────────────────────────────────────────

async def test_moderator_message_sends_to_viewer(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"contact_sub_id": 3})
    db_user = make_user()
    user = make_user(telegram_id=202)
    sub = make_submission(sub_id=3, user=user)
    message = make_message(text="Hello, artist!")
    create_msg = AsyncMock()

    monkeypatch.setattr(contact, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(contact, "create_message", create_msg)
    monkeypatch.setattr(contact.topic_notifications, "notify_contact_from_moderator", AsyncMock())

    await contact.handle_moderator_message(message, session, state, db_user)

    # Message saved to DB
    create_msg.assert_awaited_once()
    # Viewer notified via bot.send_message
    message.bot.send_message.assert_awaited_once_with(
        sub.user.telegram_id,
        msg.MODERATOR_MESSAGE_TO_VIEWER.format(sub_id=3, text="Hello, artist!"),
        parse_mode="HTML",
    )
    # State cleared after sending
    assert state.cleared is True


async def test_moderator_message_stores_html_in_db(monkeypatch) -> None:
    """Message saved to DB should be HTML content (per critical architecture rule)."""
    session = AsyncMock()
    state = FakeState({"contact_sub_id": 3})
    db_user = make_user()
    user = make_user(telegram_id=202)
    sub = make_submission(sub_id=3, user=user)
    message = make_message(text="Plain text")
    create_msg = AsyncMock()

    monkeypatch.setattr(contact, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(contact, "create_message", create_msg)
    monkeypatch.setattr(contact.topic_notifications, "notify_contact_from_moderator", AsyncMock())

    await contact.handle_moderator_message(message, session, state, db_user)

    # create_message(session, sub_id, from_user_id, text_html)
    call_args = create_msg.await_args
    assert call_args.args[1] == 3  # sub_id
    # The message text (HTML) is the 4th arg
    assert "Plain text" in call_args.args[3]


async def test_moderator_message_handles_missing_sub_id(monkeypatch) -> None:
    """When contact_sub_id is missing, state is cleared without sending."""
    session = AsyncMock()
    state = FakeState({})  # no contact_sub_id
    db_user = make_user()
    message = make_message(text="Hello")
    create_msg = AsyncMock()

    monkeypatch.setattr(contact, "create_message", create_msg)

    await contact.handle_moderator_message(message, session, state, db_user)

    create_msg.assert_not_awaited()
    assert state.cleared is True


async def test_moderator_message_handles_missing_submission(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"contact_sub_id": 99})
    db_user = make_user()
    message = make_message(text="Hello")
    create_msg = AsyncMock()

    monkeypatch.setattr(contact, "get_submission_with_user", AsyncMock(return_value=None))
    monkeypatch.setattr(contact, "create_message", create_msg)

    await contact.handle_moderator_message(message, session, state, db_user)

    create_msg.assert_not_awaited()
    assert state.cleared is True


async def test_moderator_message_handles_send_failure_gracefully(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"contact_sub_id": 3})
    db_user = make_user()
    user = make_user(telegram_id=202)
    sub = make_submission(sub_id=3, user=user)
    message = make_message(text="Test")
    create_msg = AsyncMock()

    monkeypatch.setattr(contact, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(contact, "create_message", create_msg)
    monkeypatch.setattr(contact.topic_notifications, "notify_contact_from_moderator", AsyncMock())
    message.bot.send_message.side_effect = Exception("Telegram down")

    # Should not raise
    await contact.handle_moderator_message(message, session, state, db_user)

    message.answer.assert_awaited_once_with(msg.MESSAGE_FAILED)
    assert state.cleared is True


# ── Filter isolation: submission-thread reply vs direct-channel reply ──

async def test_is_moderator_reply_ignores_direct_message_text() -> None:
    replied = make_message(text=msg.MODERATOR_DIRECT_MESSAGE_TO_VIEWER.format(text="hi"))
    message = make_message()
    message.reply_to_message = replied
    _mark_from_bot(replied, message.bot)

    assert await contact.IsModeratorReply()(message) is False


async def test_is_direct_moderator_reply_ignores_submission_thread_text() -> None:
    replied = make_message(text=msg.MODERATOR_MESSAGE_TO_VIEWER.format(sub_id=7, text="hi"))
    message = make_message()
    message.reply_to_message = replied
    _mark_from_bot(replied, message.bot)

    assert await contact.IsDirectModeratorReply()(message) is False


async def test_direct_reply_with_embedded_sub_number_only_matches_direct_filter() -> None:
    """A moderator's direct {text} may itself contain "поста #NN" — the direct
    channel takes priority, so only IsDirectModeratorReply should catch it."""
    replied = make_message(
        text=msg.MODERATOR_DIRECT_MESSAGE_TO_VIEWER.format(text="Ответьте по поводу поста #42")
    )
    message = make_message()
    message.reply_to_message = replied
    _mark_from_bot(replied, message.bot)

    assert await contact.IsModeratorReply()(message) is False
    assert await contact.IsDirectModeratorReply()(message) is True


async def test_moderator_reply_filters_reject_reply_to_non_bot_message() -> None:
    """A viewer can write a message matching the moderator-reply pattern
    themselves and reply to it — that must not look like a moderator reply,
    since the replied-to message was never sent by the bot."""
    replied = make_message(text=msg.MODERATOR_MESSAGE_TO_VIEWER.format(sub_id=7, text="hi"))
    message = make_message()
    message.reply_to_message = replied
    _mark_from_viewer(replied)

    assert await contact.IsModeratorReply()(message) is False
    assert await contact.IsDirectModeratorReply()(message) is False


async def test_direct_reply_filter_rejects_reply_to_non_bot_message() -> None:
    replied = make_message(
        text=msg.MODERATOR_DIRECT_MESSAGE_TO_VIEWER.format(text="hi")
    )
    message = make_message()
    message.reply_to_message = replied
    _mark_from_viewer(replied)

    assert await contact.IsModeratorReply()(message) is False
    assert await contact.IsDirectModeratorReply()(message) is False


# ── deliver_direct_message ─────────────────────────────────────────

async def test_deliver_direct_message_success_writes_message_row(monkeypatch) -> None:
    session = AsyncMock()
    target = make_user(user_id=5, telegram_id=555)
    moderator = make_user(user_id=1)
    message = make_message()
    create_msg = AsyncMock()

    monkeypatch.setattr(contact, "create_message", create_msg)
    monkeypatch.setattr(contact.topic_notifications, "notify_direct_from_moderator", AsyncMock())

    result = await contact.deliver_direct_message(
        message.bot, session, target=target, moderator=moderator, text_html="Hello"
    )

    assert result is True
    message.bot.send_message.assert_awaited_once_with(
        target.telegram_id,
        msg.MODERATOR_DIRECT_MESSAGE_TO_VIEWER.format(text="Hello"),
        parse_mode="HTML",
    )
    create_msg.assert_awaited_once_with(session, None, moderator.telegram_id, "Hello", target_user_id=target.id)


async def test_deliver_direct_message_failure_returns_false(monkeypatch) -> None:
    session = AsyncMock()
    target = make_user(user_id=5, telegram_id=555)
    moderator = make_user(user_id=1)
    message = make_message()
    message.bot.send_message.side_effect = TelegramAPIError(method=None, message="Forbidden")  # type: ignore[arg-type]
    create_msg = AsyncMock()

    monkeypatch.setattr(contact, "create_message", create_msg)
    monkeypatch.setattr(contact.topic_notifications, "notify_direct_from_moderator", AsyncMock())

    result = await contact.deliver_direct_message(
        message.bot, session, target=target, moderator=moderator, text_html="Hello"
    )

    assert result is False
    create_msg.assert_not_awaited()


# ── handle_viewer_direct_reply ──────────────────────────────────────

async def test_viewer_direct_reply_posts_to_topic(monkeypatch) -> None:
    session = AsyncMock()
    db_user = make_user(user_id=5)
    replied = make_message(text=msg.MODERATOR_DIRECT_MESSAGE_TO_VIEWER.format(text="hi"))
    message = make_message(text="Thanks!")
    message.reply_to_message = replied
    create_msg = AsyncMock()
    notify = AsyncMock()

    monkeypatch.setattr(contact, "create_message", create_msg)
    monkeypatch.setattr(contact.topic_notifications, "notify_direct_from_viewer", notify)

    await contact.handle_viewer_direct_reply(message, session, db_user)

    create_msg.assert_awaited_once_with(session, None, message.from_user.id, "Thanks!", target_user_id=db_user.id)
    notify.assert_awaited_once_with(message.bot, session, db_user, "Thanks!")
    message.answer.assert_awaited_once_with(msg.DIRECT_REPLY_SENT)
