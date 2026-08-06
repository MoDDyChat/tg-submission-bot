"""Unit tests for handlers/moderator/schedule.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

from handlers.moderator import schedule
from states.moderator import ModeratorReview
from tests.helpers import FakeState, make_callback, make_publication, make_submission, make_user


async def test_schedule_confirm_calls_render_schedule(monkeypatch) -> None:
    session = AsyncMock()
    user = make_user()
    sub = make_submission(sub_id=6, status="pending", caption="Test", tags=["Art"])
    pub = make_publication(pub_id=2, submission_id=6)
    callback = make_callback()

    # State data with a future date
    state = FakeState({
        "sub_id": 6,
        "pub_year": 2027,
        "pub_month": 1,
        "pub_day": 15,
        "pub_hour": 12,
        "pub_minute": 0,
        "actions_message_id": 10,
        "media_message_ids": [],
    })

    monkeypatch.setattr(schedule, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(schedule.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(schedule, "get_publication_by_submission", AsyncMock(return_value=None))
    monkeypatch.setattr(schedule, "create_publication", AsyncMock(return_value=pub))
    monkeypatch.setattr(schedule, "update_submission_status", AsyncMock())
    monkeypatch.setattr(schedule, "update_publication_time", AsyncMock())
    monkeypatch.setattr(schedule, "cancel_scheduled", AsyncMock())
    monkeypatch.setattr(schedule, "schedule_post", AsyncMock())
    monkeypatch.setattr(schedule.topic_notifications, "notify_scheduled", AsyncMock())
    monkeypatch.setattr(schedule.topic_notifications, "notify_rescheduled", AsyncMock())
    monkeypatch.setattr(schedule.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(schedule.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(schedule, "_render_queue", AsyncMock())
    mock_render_schedule = AsyncMock()
    monkeypatch.setattr(schedule, "_render_schedule", mock_render_schedule)

    await schedule.handle_confirm_yes(callback, session, state, user)

    mock_render_schedule.assert_awaited_once_with(callback.bot, session)
    assert state.state == ModeratorReview.viewing_post
