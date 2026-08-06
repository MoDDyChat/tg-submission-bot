"""Unit tests for handlers/moderator/ban.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

import core.messages as msg
from handlers.moderator import ban
from states.moderator import ModeratorReview
from tests.helpers import FakeState, make_callback, make_message, make_submission, make_user


# ── handle_ban_author_start ─────────────────────────────────────────

async def test_ban_author_start_sets_state_and_prompts(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    user = make_user(user_id=9, telegram_id=909)
    sub = make_submission(sub_id=3, user=user, status="pending")
    callback = make_callback(from_user_id=1)

    monkeypatch.setattr(ban, "get_submission_with_user", AsyncMock(return_value=sub))

    await ban.handle_ban_author_start(callback, AsyncMock(sub_id=3), session, state)

    assert state.state == ModeratorReview.entering_ban_reason
    assert state.data["ban_user_id"] == user.id
    callback.answer.assert_awaited()


async def test_ban_author_start_rejects_already_banned_user(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    user = make_user(is_banned=True)
    sub = make_submission(user=user)
    callback = make_callback()

    monkeypatch.setattr(ban, "get_submission_with_user", AsyncMock(return_value=sub))

    await ban.handle_ban_author_start(callback, AsyncMock(sub_id=1), session, state)

    callback.answer.assert_awaited_once_with(msg.USER_ALREADY_BANNED, show_alert=True)
    assert state.state is None  # state not changed


async def test_ban_author_start_rejects_missing_submission(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    callback = make_callback()

    monkeypatch.setattr(ban, "get_submission_with_user", AsyncMock(return_value=None))

    await ban.handle_ban_author_start(callback, AsyncMock(sub_id=99), session, state)

    callback.answer.assert_awaited_once_with(msg.SUBMISSION_NOT_FOUND, show_alert=True)


# ── handle_ban_reason ───────────────────────────────────────────────

async def test_ban_reason_bans_user_and_notifies(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 5, "ban_user_id": 9, "ban_user_display": "@artist"})
    db_user = make_user(user_id=2)
    message = make_message(text="Spam")

    sub = make_submission(sub_id=5, status="pending")
    ban_user_mock = AsyncMock()
    send_view = AsyncMock(return_value=True)

    monkeypatch.setattr(ban.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(ban, "ban_user", ban_user_mock)
    monkeypatch.setattr(ban.admin_notifications, "notify_admins", AsyncMock())
    monkeypatch.setattr(ban, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(ban.topic_notifications, "notify_banned", AsyncMock())
    monkeypatch.setattr(ban.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(ban, "_send_submission_view", send_view)

    await ban.handle_ban_reason(message, session, state, db_user)

    ban_user_mock.assert_awaited_once_with(session, 9, "Spam")
    send_view.assert_awaited_once()


async def test_ban_reason_aborts_on_lock_lost(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 5, "ban_user_id": 9})
    db_user = make_user()
    message = make_message(text="Reason")
    ban_user_mock = AsyncMock()

    monkeypatch.setattr(ban.edit_lock, "extend_lock", AsyncMock(return_value=False))
    monkeypatch.setattr(ban, "ban_user", ban_user_mock)

    await ban.handle_ban_reason(message, session, state, db_user)

    ban_user_mock.assert_not_awaited()
    assert state.cleared is True


async def test_ban_reason_works_without_sub_id(monkeypatch) -> None:
    """Ban can be initiated without an active submission (from banned-users list)."""
    session = AsyncMock()
    state = FakeState({"ban_user_id": 9, "ban_user_display": "@artist"})
    db_user = make_user()
    message = make_message(text="Spam")
    ban_user_mock = AsyncMock()

    monkeypatch.setattr(ban, "ban_user", ban_user_mock)
    monkeypatch.setattr(ban.admin_notifications, "notify_admins", AsyncMock())

    await ban.handle_ban_reason(message, session, state, db_user)

    ban_user_mock.assert_awaited_once()
    # No _send_submission_view call, state cleared
    assert state.cleared is True
