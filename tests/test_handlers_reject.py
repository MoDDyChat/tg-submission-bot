"""Unit tests for handlers/moderator/reject.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

import core.messages as msg
from handlers.moderator import reject
from states.moderator import ModeratorReview
from tests.helpers import FakeState, make_callback, make_message, make_submission, make_user


# ── handle_reject (start) ────────────────────────────────────────────

async def test_handle_reject_sets_state_and_prompts(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    sub = make_submission(sub_id=7, status="pending")
    callback = make_callback()

    monkeypatch.setattr(reject, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(reject.edit_lock, "extend_lock", AsyncMock(return_value=True))

    await reject.handle_reject(callback, AsyncMock(sub_id=7), session, state, db_user)

    assert state.state == ModeratorReview.entering_reject_reason
    callback.answer.assert_awaited()


async def test_handle_reject_fails_on_missing_submission(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    callback = make_callback()

    monkeypatch.setattr(reject, "get_submission_with_user", AsyncMock(return_value=None))

    await reject.handle_reject(callback, AsyncMock(sub_id=99), session, state, db_user)

    callback.answer.assert_awaited_once_with(msg.SUBMISSION_NOT_AVAILABLE, show_alert=True)
    assert state.state is None


async def test_handle_reject_aborts_on_lock_lost(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    sub = make_submission(sub_id=7, status="pending")
    callback = make_callback()

    monkeypatch.setattr(reject, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(reject.edit_lock, "extend_lock", AsyncMock(return_value=False))

    await reject.handle_reject(callback, AsyncMock(sub_id=7), session, state, db_user)

    callback.answer.assert_awaited_once_with(msg.MODERATOR_LOCK_LOST, show_alert=True)
    assert state.cleared is True


# ── handle_reject_reason ─────────────────────────────────────────────

async def test_handle_reject_reason_rejects_submission(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 7})
    db_user = make_user(user_id=2, telegram_id=202)
    message = make_message(text="Doesn't match style")
    sub = make_submission(sub_id=7, status="pending")
    update_status = AsyncMock()

    monkeypatch.setattr(reject.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(reject, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(reject, "update_submission_status", update_status)
    monkeypatch.setattr(reject.topic_notifications, "notify_rejected", AsyncMock())
    monkeypatch.setattr(reject.topics, "finalize_submission_card", AsyncMock())
    monkeypatch.setattr(reject.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(reject.edit_lock, "release_lock", AsyncMock())
    monkeypatch.setattr(reject, "_render_queue", AsyncMock())
    monkeypatch.setattr(reject, "_delete_tracked_messages", AsyncMock())

    await reject.handle_reject_reason(message, session, state, db_user)

    update_status.assert_awaited_once_with(session, 7, "rejected")
    message.bot.send_message.assert_awaited_once()
    assert message.bot.send_message.await_args.args[0] == sub.user.telegram_id
    assert state.cleared is True


async def test_handle_reject_reason_skips_dm_when_moderator_is_author(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 7})
    db_user = make_user()
    message = make_message(text="Doesn't match style")
    sub = make_submission(sub_id=7, status="pending", user=db_user)

    monkeypatch.setattr(reject.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(reject, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(reject, "update_submission_status", AsyncMock())
    monkeypatch.setattr(reject.topic_notifications, "notify_rejected", AsyncMock())
    monkeypatch.setattr(reject.topics, "finalize_submission_card", AsyncMock())
    monkeypatch.setattr(reject.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(reject.edit_lock, "release_lock", AsyncMock())
    monkeypatch.setattr(reject, "_render_queue", AsyncMock())
    monkeypatch.setattr(reject, "_delete_tracked_messages", AsyncMock())

    await reject.handle_reject_reason(message, session, state, db_user)

    message.bot.send_message.assert_not_awaited()
    message.answer.assert_awaited()


async def test_handle_reject_reason_aborts_on_lock_lost(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 7})
    db_user = make_user()
    message = make_message(text="Reason")
    update_status = AsyncMock()

    monkeypatch.setattr(reject.edit_lock, "extend_lock", AsyncMock(return_value=False))
    monkeypatch.setattr(reject, "update_submission_status", update_status)

    await reject.handle_reject_reason(message, session, state, db_user)

    update_status.assert_not_awaited()
    assert state.cleared is True


# ── handle_reject_silent ─────────────────────────────────────────────

async def test_handle_reject_silent_rejects_without_notification(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 7})
    db_user = make_user()
    sub = make_submission(sub_id=7, status="pending")
    callback = make_callback()
    update_status = AsyncMock()
    notify_rejected = AsyncMock()

    monkeypatch.setattr(reject, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(reject.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(reject, "update_submission_status", update_status)
    monkeypatch.setattr(reject.topic_notifications, "notify_rejected", notify_rejected)
    monkeypatch.setattr(reject.topics, "finalize_submission_card", AsyncMock())
    monkeypatch.setattr(reject.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(reject.edit_lock, "release_lock", AsyncMock())
    monkeypatch.setattr(reject, "_render_queue", AsyncMock())
    monkeypatch.setattr(reject, "_delete_tracked_messages", AsyncMock())

    await reject.handle_reject_silent(callback, AsyncMock(sub_id=7), session, state, db_user)

    update_status.assert_awaited_once_with(session, 7, "rejected")
    # Silent reject passes silent=True
    notify_rejected.assert_awaited_once()
    assert notify_rejected.await_args.kwargs.get("silent") is True
    assert state.cleared is True
