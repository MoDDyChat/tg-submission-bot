"""Unit tests for handlers/moderator/schedule.py."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import core.messages as msg
from core.config import config
from handlers.moderator import schedule
from keyboards.callbacks import CalendarCB, SubmissionCB, TimeCB
from services.schedule_occupancy import DayOccupancy
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


async def test_calendar_day_shows_busy_block_and_marks_hour(monkeypatch) -> None:
    session = AsyncMock()
    user = make_user()
    callback = make_callback()
    callback_data = CalendarCB(action="day", year=2027, month=1, day=15)
    state = FakeState({"sub_id": 6})

    occ = DayOccupancy(
        hours={12},
        minute_slots={(12, 0)},
        entries=[(datetime(2027, 1, 15, 12, 0), 45)],
    )
    monkeypatch.setattr(schedule, "_extend_submission_lock_from_state", AsyncMock(return_value=True))
    mock_get_day_occupancy = AsyncMock(return_value=occ)
    monkeypatch.setattr(schedule, "get_day_occupancy", mock_get_day_occupancy)

    await schedule.handle_calendar_day(callback, callback_data, session, state, user)

    text = callback.message.edit_text.call_args.args[0]
    assert "12:00 — #45" in text

    kb = callback.message.edit_text.call_args.kwargs["reply_markup"]
    busy_texts = [
        btn.text
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("time:")
    ]
    assert "\U0001f53812" in busy_texts


async def test_pick_hour_marks_busy_minute_only_for_busy_hour(monkeypatch) -> None:
    session = AsyncMock()
    user = make_user()

    occ = DayOccupancy(
        hours={12},
        minute_slots={(12, 0)},
        entries=[(datetime(2027, 1, 15, 12, 0), 45)],
    )
    monkeypatch.setattr(schedule, "_extend_submission_lock_from_state", AsyncMock(return_value=True))
    monkeypatch.setattr(schedule, "get_day_occupancy", AsyncMock(return_value=occ))

    # Hour 12 -> minute 00 is busy
    callback12 = make_callback()
    state12 = FakeState({"sub_id": 6, "pub_year": 2027, "pub_month": 1, "pub_day": 15})
    await schedule.handle_pick_hour(callback12, TimeCB(hour=12, minute=-1), session, state12, user)
    kb12 = callback12.message.edit_text.call_args.kwargs["reply_markup"]
    texts12 = [
        btn.text
        for row in kb12.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("time:")
    ]
    assert any("\U0001f538" in t for t in texts12)

    # Hour 13 -> no busy minutes
    callback13 = make_callback()
    state13 = FakeState({"sub_id": 6, "pub_year": 2027, "pub_month": 1, "pub_day": 15})
    await schedule.handle_pick_hour(callback13, TimeCB(hour=13, minute=-1), session, state13, user)
    kb13 = callback13.message.edit_text.call_args.kwargs["reply_markup"]
    texts13 = [
        btn.text
        for row in kb13.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("time:")
    ]
    assert all("\U0001f538" not in t for t in texts13)


async def test_schedule_start_shows_busy_days_and_excludes_self(monkeypatch) -> None:
    session = AsyncMock()
    user = make_user()
    sub = make_submission(sub_id=6, status="pending")
    callback = make_callback()
    callback_data = SubmissionCB(action="schedule", sub_id=6)
    state = FakeState({})

    today = datetime.now(ZoneInfo(config.timezone))
    monkeypatch.setattr(schedule, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(schedule.edit_lock, "extend_lock", AsyncMock(return_value=True))
    mock_get_month_occupancy = AsyncMock(return_value={today.day: 2})
    monkeypatch.setattr(schedule, "get_month_occupancy", mock_get_month_occupancy)

    await schedule.handle_schedule_start(callback, callback_data, session, state, user)

    kb = callback.message.answer.call_args.kwargs["reply_markup"]
    day_texts = {
        CalendarCB.unpack(btn.callback_data).day: btn.text
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("cal:")
        and CalendarCB.unpack(btn.callback_data).action == "day"
    }
    assert day_texts.get(today.day) == f"{today.day} ❷"

    _, kwargs = mock_get_month_occupancy.call_args
    assert kwargs.get("exclude_submission_id") == 6


async def test_pick_hour_no_busy_block_when_no_entries(monkeypatch) -> None:
    session = AsyncMock()
    user = make_user()
    callback = make_callback()

    occ = DayOccupancy()
    monkeypatch.setattr(schedule, "_extend_submission_lock_from_state", AsyncMock(return_value=True))
    monkeypatch.setattr(schedule, "get_day_occupancy", AsyncMock(return_value=occ))

    state = FakeState({"sub_id": 6, "pub_year": 2027, "pub_month": 1, "pub_day": 15})
    await schedule.handle_pick_hour(callback, TimeCB(hour=12, minute=-1), session, state, user)

    text = callback.message.edit_text.call_args.args[0]
    assert msg.PICK_BUSY_HEADER not in text
