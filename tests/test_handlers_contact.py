"""Unit tests for handlers/contact.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

import core.messages as msg
from handlers import contact
from states.contact import ContactViewer
from tests.helpers import FakeState, make_callback, make_message, make_submission, make_user


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
