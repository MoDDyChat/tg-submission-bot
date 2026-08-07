"""Unit tests for handlers/moderator/author_card.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

import core.messages as msg
from core.exceptions import CannotBanModeratorError
from handlers.moderator import author_card
from keyboards.callbacks import AuthorCardCB
from keyboards.moderator import author_card_kb
from states.moderator import AuthorCard
from tests.helpers import FakeState, make_callback, make_message, make_user


# ── author_card_kb ───────────────────────────────────────────────────

def test_author_card_kb_shows_ban_button_for_regular_user() -> None:
    user = make_user(user_id=1)
    kb = author_card_kb(user)
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert msg.AUTHOR_CARD_BTN_BAN in texts


def test_author_card_kb_omits_ban_button_for_moderator() -> None:
    user = make_user(user_id=1)
    user.is_moderator = True
    kb = author_card_kb(user)
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert msg.AUTHOR_CARD_BTN_BAN not in texts
    assert msg.AUTHOR_CARD_BTN_UNBAN not in texts


def test_author_card_kb_omits_ban_button_for_admin() -> None:
    user = make_user(user_id=1)
    user.is_admin = True
    kb = author_card_kb(user)
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert msg.AUTHOR_CARD_BTN_BAN not in texts


def test_author_card_kb_shows_unban_for_banned_user() -> None:
    user = make_user(user_id=1, is_banned=True, ban_reason="spam")
    kb = author_card_kb(user)
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert msg.AUTHOR_CARD_BTN_UNBAN in texts
    assert msg.AUTHOR_CARD_BTN_BAN not in texts


# ── ban from card ────────────────────────────────────────────────────

async def test_handle_ban_start_rejects_moderator_target(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    target = make_user(user_id=9)
    target.is_moderator = True
    callback = make_callback()

    monkeypatch.setattr(author_card, "get_user_by_id", AsyncMock(return_value=target))

    await author_card.handle_ban_start(callback, AuthorCardCB(action="ban", user_id=9), session, state)

    callback.answer.assert_awaited_once_with(msg.AUTHOR_CARD_CANNOT_BAN_MODERATOR, show_alert=True)
    assert state.state is None


async def test_handle_ban_start_rejects_admin_target(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    target = make_user(user_id=9)
    target.is_admin = True
    callback = make_callback()

    monkeypatch.setattr(author_card, "get_user_by_id", AsyncMock(return_value=target))

    await author_card.handle_ban_start(callback, AuthorCardCB(action="ban", user_id=9), session, state)

    callback.answer.assert_awaited_once_with(msg.AUTHOR_CARD_CANNOT_BAN_MODERATOR, show_alert=True)
    assert state.state is None


async def test_handle_ban_start_regular_user_proceeds(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    target = make_user(user_id=9)
    callback = make_callback()

    monkeypatch.setattr(author_card, "get_user_by_id", AsyncMock(return_value=target))

    await author_card.handle_ban_start(callback, AuthorCardCB(action="ban", user_id=9), session, state)

    assert state.state == AuthorCard.entering_ban_reason
    assert state.data["author_card_user_id"] == 9
    callback.answer.assert_awaited()


async def test_handle_ban_start_already_banned(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    target = make_user(user_id=9, is_banned=True)
    callback = make_callback()

    monkeypatch.setattr(author_card, "get_user_by_id", AsyncMock(return_value=target))

    await author_card.handle_ban_start(callback, AuthorCardCB(action="ban", user_id=9), session, state)

    callback.answer.assert_awaited_once_with(msg.USER_ALREADY_BANNED, show_alert=True)
    assert state.state is None


async def test_handle_ban_reason_bans_and_notifies(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"author_card_user_id": 9})
    db_user = make_user(user_id=2)
    message = make_message(text="Spam")
    target = make_user(user_id=9)

    ban_user_mock = AsyncMock()
    monkeypatch.setattr(author_card, "ban_user", ban_user_mock)
    monkeypatch.setattr(author_card, "get_user_by_id", AsyncMock(return_value=target))
    monkeypatch.setattr(author_card.admin_notifications, "notify_admins", AsyncMock())
    monkeypatch.setattr(author_card.topics, "request_topic_title_sync", AsyncMock())
    request_mock = AsyncMock()
    monkeypatch.setattr(author_card, "request_author_card", request_mock)

    await author_card.handle_ban_reason(message, session, state, db_user)

    ban_user_mock.assert_awaited_once_with(session, 9, "Spam")
    request_mock.assert_called_once_with(9)
    assert state.cleared is True
    message.answer.assert_awaited()


async def test_handle_ban_reason_handles_concurrent_grant_race(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"author_card_user_id": 9})
    db_user = make_user(user_id=2)
    message = make_message(text="Spam")

    monkeypatch.setattr(author_card, "ban_user", AsyncMock(side_effect=CannotBanModeratorError()))
    notify = AsyncMock()
    monkeypatch.setattr(author_card.admin_notifications, "notify_admins", notify)

    await author_card.handle_ban_reason(message, session, state, db_user)

    notify.assert_not_awaited()
    message.answer.assert_awaited_once_with(msg.AUTHOR_CARD_CANNOT_BAN_MODERATOR)
    assert state.cleared is True


# ── unban from card ──────────────────────────────────────────────────

async def test_handle_unban_clears_flag_and_marks_card_dirty(monkeypatch) -> None:
    session = AsyncMock()
    db_user = make_user(user_id=2)
    target = make_user(user_id=9, is_banned=True, ban_reason="spam")
    callback = make_callback()

    monkeypatch.setattr(author_card, "get_user_by_id", AsyncMock(return_value=target))
    unban_mock = AsyncMock()
    monkeypatch.setattr(author_card, "unban_user", unban_mock)
    monkeypatch.setattr(author_card.admin_notifications, "notify_admins", AsyncMock())
    monkeypatch.setattr(author_card.topic_notifications, "notify_unbanned", AsyncMock())
    monkeypatch.setattr(author_card.topics, "request_topic_title_sync", AsyncMock())
    request_mock = AsyncMock()
    monkeypatch.setattr(author_card, "request_author_card", request_mock)

    await author_card.handle_unban(callback, AuthorCardCB(action="unban", user_id=9), session, db_user)

    unban_mock.assert_awaited_once_with(session, 9)
    request_mock.assert_called_once_with(9)
    callback.answer.assert_awaited_once()


# ── note ─────────────────────────────────────────────────────────────

async def test_handle_note_text_saves_note(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"author_card_user_id": 9})
    message = make_message(text="Хороший автор")

    set_note_mock = AsyncMock()
    monkeypatch.setattr(author_card, "set_moderator_note", set_note_mock)
    request_mock = AsyncMock()
    monkeypatch.setattr(author_card, "request_author_card", request_mock)

    await author_card.handle_note_text(message, session, state)

    set_note_mock.assert_awaited_once_with(session, 9, "Хороший автор")
    message.answer.assert_awaited_once_with(msg.AUTHOR_CARD_NOTE_SAVED)
    request_mock.assert_called_once_with(9)
    assert state.cleared is True


async def test_handle_note_text_saves_plain_text_not_html(monkeypatch) -> None:
    """The note is stored as plain text — services/author_card.py escapes it
    once at render time, so storing HTML here would double-escape it there."""
    session = AsyncMock()
    state = FakeState({"author_card_user_id": 9})
    message = make_message(text="<b>bold</b> & stuff")

    set_note_mock = AsyncMock()
    monkeypatch.setattr(author_card, "set_moderator_note", set_note_mock)
    monkeypatch.setattr(author_card, "request_author_card", AsyncMock())

    await author_card.handle_note_text(message, session, state)

    set_note_mock.assert_awaited_once_with(session, 9, "<b>bold</b> & stuff")


async def test_handle_note_text_dash_clears_note(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"author_card_user_id": 9})
    message = make_message(text="-")

    set_note_mock = AsyncMock()
    monkeypatch.setattr(author_card, "set_moderator_note", set_note_mock)
    monkeypatch.setattr(author_card, "request_author_card", AsyncMock())

    await author_card.handle_note_text(message, session, state)

    set_note_mock.assert_awaited_once_with(session, 9, None)
    message.answer.assert_awaited_once_with(msg.AUTHOR_CARD_NOTE_CLEARED)


# ── contact ──────────────────────────────────────────────────────────

async def test_handle_contact_text_sends_direct_message(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"author_card_user_id": 9})
    db_user = make_user(user_id=2)
    target = make_user(user_id=9)
    message = make_message(text="Привет")

    monkeypatch.setattr(author_card, "get_user_by_id", AsyncMock(return_value=target))
    deliver_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(author_card, "deliver_direct_message", deliver_mock)

    await author_card.handle_contact_text(message, session, state, db_user)

    deliver_mock.assert_awaited_once()
    message.answer.assert_awaited_once_with(msg.DIRECT_MESSAGE_SENT)
    assert state.cleared is True


async def test_handle_contact_text_reports_failure(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"author_card_user_id": 9})
    db_user = make_user(user_id=2)
    target = make_user(user_id=9)
    message = make_message(text="Привет")

    monkeypatch.setattr(author_card, "get_user_by_id", AsyncMock(return_value=target))
    monkeypatch.setattr(author_card, "deliver_direct_message", AsyncMock(return_value=False))

    await author_card.handle_contact_text(message, session, state, db_user)

    message.answer.assert_awaited_once_with(msg.DIRECT_MESSAGE_FAILED)


# ── /user ────────────────────────────────────────────────────────────

async def test_user_lookup_by_id_found(monkeypatch) -> None:
    session = AsyncMock()
    message = make_message(chat_id=-100999)
    target = make_user(user_id=9, telegram_id=123)
    topic = AsyncMock()
    topic.topic_id = 42

    monkeypatch.setattr(author_card.config, "moderator_group_id", -100999)
    monkeypatch.setattr(author_card, "get_user_by_telegram_id", AsyncMock(return_value=target))
    monkeypatch.setattr(author_card, "get_user_topic", AsyncMock(return_value=topic))
    monkeypatch.setattr(author_card, "build_author_card", AsyncMock(return_value="card"))
    request_mock = AsyncMock()
    monkeypatch.setattr(author_card, "request_author_card", request_mock)

    command = AsyncMock(args="123")

    await author_card.handle_user_lookup(message, command, session)

    message.answer.assert_awaited_once()
    request_mock.assert_called_once_with(9)


async def test_user_lookup_by_username_found(monkeypatch) -> None:
    session = AsyncMock()
    message = make_message(chat_id=-100999)
    target = make_user(user_id=9, username="artist")
    topic = AsyncMock()
    topic.topic_id = 42

    monkeypatch.setattr(author_card.config, "moderator_group_id", -100999)
    username_mock = AsyncMock(return_value=[target])
    monkeypatch.setattr(author_card, "get_users_by_username", username_mock)
    monkeypatch.setattr(author_card, "get_user_topic", AsyncMock(return_value=topic))
    monkeypatch.setattr(author_card, "build_author_card", AsyncMock(return_value="card"))
    monkeypatch.setattr(author_card, "request_author_card", AsyncMock())

    command = AsyncMock(args="@artist")

    await author_card.handle_user_lookup(message, command, session)

    username_mock.assert_awaited_once_with(session, "artist")
    message.answer.assert_awaited_once()


async def test_user_lookup_by_username_ambiguous(monkeypatch) -> None:
    """Two users sharing a reused nick — refuse to guess, don't show a card."""
    session = AsyncMock()
    message = make_message(chat_id=-100999)
    first = make_user(user_id=9, username="artist")
    second = make_user(user_id=10, username="artist")

    monkeypatch.setattr(author_card.config, "moderator_group_id", -100999)
    username_mock = AsyncMock(return_value=[first, second])
    monkeypatch.setattr(author_card, "get_users_by_username", username_mock)
    card_mock = AsyncMock()
    monkeypatch.setattr(author_card, "request_author_card", card_mock)

    command = AsyncMock(args="@artist")

    await author_card.handle_user_lookup(message, command, session)

    message.answer.assert_awaited_once_with(msg.USER_LOOKUP_AMBIGUOUS, parse_mode="HTML")
    card_mock.assert_not_called()


async def test_user_lookup_no_args_shows_usage(monkeypatch) -> None:
    session = AsyncMock()
    message = make_message(chat_id=-100999)
    monkeypatch.setattr(author_card.config, "moderator_group_id", -100999)

    command = AsyncMock(args=None)

    await author_card.handle_user_lookup(message, command, session)

    message.answer.assert_awaited_once_with(msg.USER_LOOKUP_USAGE, parse_mode="HTML")


async def test_user_lookup_outside_moderator_group_does_nothing(monkeypatch) -> None:
    session = AsyncMock()
    message = make_message(chat_id=1)
    monkeypatch.setattr(author_card.config, "moderator_group_id", -100999)

    command = AsyncMock(args="123")

    await author_card.handle_user_lookup(message, command, session)

    message.answer.assert_not_awaited()


async def test_user_lookup_not_found(monkeypatch) -> None:
    session = AsyncMock()
    message = make_message(chat_id=-100999)
    monkeypatch.setattr(author_card.config, "moderator_group_id", -100999)
    monkeypatch.setattr(author_card, "get_user_by_telegram_id", AsyncMock(return_value=None))

    command = AsyncMock(args="123")

    await author_card.handle_user_lookup(message, command, session)

    message.answer.assert_awaited_once_with(msg.USER_LOOKUP_NOT_FOUND)


async def test_user_lookup_no_topic(monkeypatch) -> None:
    session = AsyncMock()
    message = make_message(chat_id=-100999)
    target = make_user(user_id=9, telegram_id=123)
    monkeypatch.setattr(author_card.config, "moderator_group_id", -100999)
    monkeypatch.setattr(author_card, "get_user_by_telegram_id", AsyncMock(return_value=target))
    monkeypatch.setattr(author_card, "get_user_topic", AsyncMock(return_value=None))

    command = AsyncMock(args="123")

    await author_card.handle_user_lookup(message, command, session)

    message.answer.assert_awaited_once_with(msg.USER_LOOKUP_NO_TOPIC)
