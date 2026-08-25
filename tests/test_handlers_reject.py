"""Unit tests for handlers/moderator/reject.py."""

from __future__ import annotations

from types import SimpleNamespace
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
    transition = AsyncMock(return_value=True)

    monkeypatch.setattr(reject.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(reject, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(reject, "transition_submission_status", transition)
    monkeypatch.setattr(reject.topic_notifications, "notify_rejected", AsyncMock())
    monkeypatch.setattr(reject.topics, "finalize_submission_card", AsyncMock())
    monkeypatch.setattr(reject.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(reject.edit_lock, "release_lock", AsyncMock())
    monkeypatch.setattr(reject, "_render_queue", AsyncMock())
    monkeypatch.setattr(reject, "_render_schedule", AsyncMock())
    monkeypatch.setattr(reject, "_drop_pending_publication", AsyncMock(return_value=None))
    monkeypatch.setattr(reject, "cancel_scheduled", lambda pub_id: None)
    monkeypatch.setattr(reject, "_delete_tracked_messages", AsyncMock())

    await reject.handle_reject_reason(message, session, state, db_user)

    transition.assert_awaited_once_with(session, 7, "rejected", expected={"pending", "scheduled"})
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
    monkeypatch.setattr(reject, "transition_submission_status", AsyncMock(return_value=True))
    monkeypatch.setattr(reject.topic_notifications, "notify_rejected", AsyncMock())
    monkeypatch.setattr(reject.topics, "finalize_submission_card", AsyncMock())
    monkeypatch.setattr(reject.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(reject.edit_lock, "release_lock", AsyncMock())
    monkeypatch.setattr(reject, "_render_queue", AsyncMock())
    monkeypatch.setattr(reject, "_render_schedule", AsyncMock())
    monkeypatch.setattr(reject, "_drop_pending_publication", AsyncMock(return_value=None))
    monkeypatch.setattr(reject, "cancel_scheduled", lambda pub_id: None)
    monkeypatch.setattr(reject, "_delete_tracked_messages", AsyncMock())

    await reject.handle_reject_reason(message, session, state, db_user)

    message.bot.send_message.assert_not_awaited()
    message.answer.assert_awaited()


async def test_handle_reject_reason_aborts_on_lock_lost(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 7})
    db_user = make_user()
    message = make_message(text="Reason")
    transition = AsyncMock(return_value=True)

    monkeypatch.setattr(reject.edit_lock, "extend_lock", AsyncMock(return_value=False))
    monkeypatch.setattr(reject, "transition_submission_status", transition)

    await reject.handle_reject_reason(message, session, state, db_user)

    transition.assert_not_awaited()
    assert state.cleared is True


async def test_handle_reject_reason_race_loser_skips_side_effects(monkeypatch) -> None:
    """Loser of a double click must not touch notifications, DMs or renders."""
    session = AsyncMock()
    state = FakeState({"sub_id": 7})
    db_user = make_user(user_id=2, telegram_id=202)
    message = make_message(text="Reason")

    monkeypatch.setattr(reject.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(
        reject, "get_submission_with_user",
        AsyncMock(return_value=make_submission(sub_id=7)),
    )
    monkeypatch.setattr(reject, "transition_submission_status", AsyncMock(return_value=False))
    notify_rejected = AsyncMock()
    monkeypatch.setattr(reject.topic_notifications, "notify_rejected", notify_rejected)
    finalize_card = AsyncMock()
    monkeypatch.setattr(reject.topics, "finalize_submission_card", finalize_card)
    render_queue = AsyncMock()
    monkeypatch.setattr(reject, "_render_queue", render_queue)
    monkeypatch.setattr(reject, "_drop_pending_publication", AsyncMock())
    monkeypatch.setattr(reject, "_delete_tracked_messages", AsyncMock())

    await reject.handle_reject_reason(message, session, state, db_user)

    message.answer.assert_awaited_once_with(msg.SUBMISSION_NOT_AVAILABLE)
    assert state.cleared is True
    notify_rejected.assert_not_awaited()
    finalize_card.assert_not_awaited()
    message.bot.send_message.assert_not_awaited()
    render_queue.assert_not_awaited()


# ── handle_reject_silent ─────────────────────────────────────────────

async def test_handle_reject_silent_rejects_without_notification(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 7})
    db_user = make_user()
    sub = make_submission(sub_id=7, status="pending")
    callback = make_callback()
    transition = AsyncMock(return_value=True)
    notify_rejected = AsyncMock()

    monkeypatch.setattr(reject, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(reject.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(reject, "transition_submission_status", transition)
    monkeypatch.setattr(reject.topic_notifications, "notify_rejected", notify_rejected)
    monkeypatch.setattr(reject.topics, "finalize_submission_card", AsyncMock())
    monkeypatch.setattr(reject.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(reject.edit_lock, "release_lock", AsyncMock())
    monkeypatch.setattr(reject, "_render_queue", AsyncMock())
    monkeypatch.setattr(reject, "_render_schedule", AsyncMock())
    monkeypatch.setattr(reject, "_drop_pending_publication", AsyncMock(return_value=None))
    monkeypatch.setattr(reject, "cancel_scheduled", lambda pub_id: None)
    monkeypatch.setattr(reject, "_delete_tracked_messages", AsyncMock())

    await reject.handle_reject_silent(callback, AsyncMock(sub_id=7), session, state, db_user)

    transition.assert_awaited_once_with(session, 7, "rejected", expected={"pending", "scheduled"})
    # Silent reject passes silent=True
    notify_rejected.assert_awaited_once()
    assert notify_rejected.await_args.kwargs.get("silent") is True
    assert state.cleared is True


async def test_handle_reject_silent_race_loser_skips_side_effects(monkeypatch) -> None:
    """Loser of a double click must not touch notifications or renders."""
    session = AsyncMock()
    state = FakeState({"sub_id": 7})
    db_user = make_user()
    callback = make_callback()

    monkeypatch.setattr(
        reject, "get_submission_with_user",
        AsyncMock(return_value=make_submission(sub_id=7)),
    )
    monkeypatch.setattr(reject.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(reject, "transition_submission_status", AsyncMock(return_value=False))
    notify_rejected = AsyncMock()
    monkeypatch.setattr(reject.topic_notifications, "notify_rejected", notify_rejected)
    finalize_card = AsyncMock()
    monkeypatch.setattr(reject.topics, "finalize_submission_card", finalize_card)
    render_queue = AsyncMock()
    monkeypatch.setattr(reject, "_render_queue", render_queue)
    monkeypatch.setattr(reject, "_drop_pending_publication", AsyncMock())

    await reject.handle_reject_silent(callback, AsyncMock(sub_id=7), session, state, db_user)

    callback.answer.assert_awaited_once_with(msg.SUBMISSION_NOT_AVAILABLE, show_alert=True)
    notify_rejected.assert_not_awaited()
    finalize_card.assert_not_awaited()
    callback.bot.send_message.assert_not_awaited()
    render_queue.assert_not_awaited()


# ── scheduled posts: publication must be dropped ─────────────────────

async def test_handle_reject_silent_drops_publication_of_scheduled_post(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 7})
    db_user = make_user()
    sub = make_submission(sub_id=7, status="scheduled")
    callback = make_callback()
    drop = AsyncMock(return_value=42)
    cancelled: list[int] = []
    render_schedule = AsyncMock()

    monkeypatch.setattr(reject, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(reject.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(reject, "transition_submission_status", AsyncMock(return_value=True))
    monkeypatch.setattr(reject.topic_notifications, "notify_rejected", AsyncMock())
    monkeypatch.setattr(reject.topics, "finalize_submission_card", AsyncMock())
    monkeypatch.setattr(reject.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(reject.edit_lock, "release_lock", AsyncMock())
    monkeypatch.setattr(reject, "_render_queue", AsyncMock())
    monkeypatch.setattr(reject, "_render_schedule", render_schedule)
    monkeypatch.setattr(reject, "_drop_pending_publication", drop)
    monkeypatch.setattr(reject, "cancel_scheduled", cancelled.append)
    monkeypatch.setattr(reject, "_delete_tracked_messages", AsyncMock())

    await reject.handle_reject_silent(callback, AsyncMock(sub_id=7), session, state, db_user)

    drop.assert_awaited_once_with(session, 7)
    assert cancelled == [42]
    render_schedule.assert_awaited_once()


async def test_handle_reject_reason_drops_publication_of_scheduled_post(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 7})
    db_user = make_user(user_id=2, telegram_id=202)
    message = make_message(text="Not this time")
    sub = make_submission(sub_id=7, status="scheduled")
    drop = AsyncMock(return_value=42)
    cancelled: list[int] = []
    render_schedule = AsyncMock()

    monkeypatch.setattr(reject.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(reject, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(reject, "transition_submission_status", AsyncMock(return_value=True))
    monkeypatch.setattr(reject.topic_notifications, "notify_rejected", AsyncMock())
    monkeypatch.setattr(reject.topics, "finalize_submission_card", AsyncMock())
    monkeypatch.setattr(reject.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(reject.edit_lock, "release_lock", AsyncMock())
    monkeypatch.setattr(reject, "_render_queue", AsyncMock())
    monkeypatch.setattr(reject, "_render_schedule", render_schedule)
    monkeypatch.setattr(reject, "_drop_pending_publication", drop)
    monkeypatch.setattr(reject, "cancel_scheduled", cancelled.append)
    monkeypatch.setattr(reject, "_delete_tracked_messages", AsyncMock())

    await reject.handle_reject_reason(message, session, state, db_user)

    drop.assert_awaited_once_with(session, 7)
    assert cancelled == [42]
    render_schedule.assert_awaited_once()


# ── _drop_pending_publication ────────────────────────────────────────

async def test_drop_pending_publication_deletes_row_and_returns_id(monkeypatch) -> None:
    from handlers.moderator import _helpers

    session = AsyncMock()
    pub = SimpleNamespace(id=42)
    deleted = AsyncMock()

    monkeypatch.setattr(
        "db.queries.get_publication_by_submission", AsyncMock(return_value=pub)
    )
    monkeypatch.setattr("db.queries.delete_publication", deleted)

    assert await _helpers._drop_pending_publication(session, 7) == 42
    deleted.assert_awaited_once_with(session, 42)


async def test_drop_pending_publication_noop_without_publication(monkeypatch) -> None:
    from handlers.moderator import _helpers

    session = AsyncMock()
    deleted = AsyncMock()

    monkeypatch.setattr(
        "db.queries.get_publication_by_submission", AsyncMock(return_value=None)
    )
    monkeypatch.setattr("db.queries.delete_publication", deleted)

    assert await _helpers._drop_pending_publication(session, 7) is None
    deleted.assert_not_awaited()
