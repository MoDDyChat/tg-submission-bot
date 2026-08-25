"""Unit tests for handlers/moderator/unschedule.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

import core.messages as msg
from handlers.moderator import unschedule
from states.moderator import ModeratorReview
from tests.helpers import FakeState, make_callback, make_publication, make_submission, make_user


# ── handle_unschedule ─────────────────────────────────────────────────

async def test_unschedule_cancels_and_reverts_to_pending(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 6, "actions_message_id": 10})
    db_user = make_user()
    sub = make_submission(sub_id=6, status="scheduled")
    pub = make_publication(pub_id=2, submission_id=6)
    callback = make_callback()
    transition = AsyncMock(return_value=True)
    cancel_sched = AsyncMock()

    monkeypatch.setattr(unschedule, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(unschedule.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(unschedule, "transition_submission_status", transition)
    monkeypatch.setattr(unschedule, "get_publication_by_submission", AsyncMock(return_value=pub))
    monkeypatch.setattr(unschedule, "cancel_scheduled", cancel_sched)
    monkeypatch.setattr(unschedule, "delete_publication", AsyncMock())
    monkeypatch.setattr(unschedule.topic_notifications, "notify_unscheduled", AsyncMock())
    monkeypatch.setattr(unschedule.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(unschedule.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(unschedule, "_render_queue", AsyncMock())
    mock_render_schedule = AsyncMock()
    monkeypatch.setattr(unschedule, "_render_schedule", mock_render_schedule)

    await unschedule.handle_unschedule(callback, AsyncMock(sub_id=6), session, state, db_user)

    cancel_sched.assert_called_once_with(pub.id)
    transition.assert_awaited_once_with(session, 6, "pending", expected={"scheduled"})
    mock_render_schedule.assert_awaited_once_with(callback.bot, session)
    assert state.state == ModeratorReview.viewing_post
    callback.answer.assert_awaited()


async def test_unschedule_rejects_non_scheduled_submission(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    sub = make_submission(sub_id=6, status="pending")
    callback = make_callback()

    monkeypatch.setattr(unschedule, "get_submission_with_user", AsyncMock(return_value=sub))

    await unschedule.handle_unschedule(callback, AsyncMock(sub_id=6), session, state, db_user)

    callback.answer.assert_awaited_once_with(msg.SUBMISSION_NOT_AVAILABLE, show_alert=True)


async def test_unschedule_aborts_on_lock_lost(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    sub = make_submission(sub_id=6, status="scheduled")
    callback = make_callback()
    transition = AsyncMock(return_value=True)

    monkeypatch.setattr(unschedule, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(unschedule.edit_lock, "extend_lock", AsyncMock(return_value=False))
    monkeypatch.setattr(unschedule, "transition_submission_status", transition)

    await unschedule.handle_unschedule(callback, AsyncMock(sub_id=6), session, state, db_user)

    transition.assert_not_awaited()
    callback.answer.assert_awaited_once_with(msg.MODERATOR_LOCK_LOST, show_alert=True)
    assert state.cleared is True


async def test_unschedule_race_loser_skips_side_effects(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    sub = make_submission(sub_id=6, status="scheduled")
    callback = make_callback()

    monkeypatch.setattr(unschedule, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(unschedule.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(
        unschedule, "transition_submission_status", AsyncMock(return_value=False),
    )
    get_pub = AsyncMock()
    monkeypatch.setattr(unschedule, "get_publication_by_submission", get_pub)
    cancel_sched = AsyncMock()
    monkeypatch.setattr(unschedule, "cancel_scheduled", cancel_sched)
    delete_pub = AsyncMock()
    monkeypatch.setattr(unschedule, "delete_publication", delete_pub)
    notify = AsyncMock()
    monkeypatch.setattr(unschedule.topic_notifications, "notify_unscheduled", notify)

    await unschedule.handle_unschedule(callback, AsyncMock(sub_id=6), session, state, db_user)

    get_pub.assert_not_awaited()
    cancel_sched.assert_not_called()
    delete_pub.assert_not_awaited()
    notify.assert_not_awaited()
    callback.answer.assert_awaited_once_with(msg.SUBMISSION_NOT_AVAILABLE, show_alert=True)


async def test_unschedule_proceeds_when_publication_already_gone(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"actions_message_id": 10})
    db_user = make_user()
    sub = make_submission(sub_id=6, status="scheduled")
    callback = make_callback()

    monkeypatch.setattr(unschedule, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(unschedule.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(
        unschedule, "transition_submission_status", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(unschedule, "get_publication_by_submission", AsyncMock(return_value=None))
    cancel_sched = AsyncMock()
    monkeypatch.setattr(unschedule, "cancel_scheduled", cancel_sched)
    delete_pub = AsyncMock()
    monkeypatch.setattr(unschedule, "delete_publication", delete_pub)
    notify = AsyncMock()
    monkeypatch.setattr(unschedule.topic_notifications, "notify_unscheduled", notify)
    monkeypatch.setattr(unschedule.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(unschedule.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(unschedule, "_render_queue", AsyncMock())
    mock_render_schedule = AsyncMock()
    monkeypatch.setattr(unschedule, "_render_schedule", mock_render_schedule)

    await unschedule.handle_unschedule(callback, AsyncMock(sub_id=6), session, state, db_user)

    assert sub.status == "pending"
    cancel_sched.assert_not_called()
    delete_pub.assert_not_awaited()
    notify.assert_awaited_once()
    mock_render_schedule.assert_awaited_once_with(callback.bot, session)
    callback.answer.assert_awaited()
