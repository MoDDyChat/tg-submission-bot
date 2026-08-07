from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from services import schedule_occupancy


def _mock_session() -> AsyncMock:
    return AsyncMock()


async def test_month_occupancy_midnight_boundary_rolls_into_next_local_day(monkeypatch) -> None:
    # 21:00 UTC on the last day of April + 3h (Moscow) = 00:00 on May 1st locally.
    entry = (datetime(2026, 4, 30, 21, 0, tzinfo=timezone.utc), 1)
    mock = AsyncMock(return_value=[entry])
    monkeypatch.setattr(schedule_occupancy, "get_scheduled_times_between", mock)

    may_counts = await schedule_occupancy.get_month_occupancy(
        _mock_session(), 2026, 5, ZoneInfo("Europe/Moscow")
    )

    assert may_counts == {1: 1}


async def test_month_occupancy_counts_multiple_posts_per_day(monkeypatch) -> None:
    entries = [
        (datetime(2026, 4, 15, 9, 0, tzinfo=timezone.utc), 1),
        (datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc), 2),
    ]
    mock = AsyncMock(return_value=entries)
    monkeypatch.setattr(schedule_occupancy, "get_scheduled_times_between", mock)

    counts = await schedule_occupancy.get_month_occupancy(
        _mock_session(), 2026, 4, ZoneInfo("Europe/Moscow")
    )

    assert counts == {15: 2}


async def test_day_occupancy_fills_hours_minute_slots_and_entries(monkeypatch) -> None:
    entries = [
        (datetime(2026, 4, 15, 9, 0, tzinfo=timezone.utc), 1),  # 12:00 Moscow
        (datetime(2026, 4, 15, 15, 30, tzinfo=timezone.utc), 2),  # 18:30 Moscow
    ]
    mock = AsyncMock(return_value=entries)
    monkeypatch.setattr(schedule_occupancy, "get_scheduled_times_between", mock)

    occ = await schedule_occupancy.get_day_occupancy(
        _mock_session(), 2026, 4, 15, ZoneInfo("Europe/Moscow")
    )

    assert occ.hours == {12, 18}
    assert occ.minute_slots == {(12, 0), (18, 30)}
    local_times = [dt.strftime("%H:%M") for dt, _sub_id in occ.entries]
    assert local_times == ["12:00", "18:30"]
    assert [sub_id for _dt, sub_id in occ.entries] == [1, 2]


async def test_day_occupancy_floors_non_5_minute_marks_to_grid(monkeypatch) -> None:
    entry = (datetime(2026, 4, 15, 15, 33, tzinfo=timezone.utc), 1)  # 18:33 Moscow
    mock = AsyncMock(return_value=[entry])
    monkeypatch.setattr(schedule_occupancy, "get_scheduled_times_between", mock)

    occ = await schedule_occupancy.get_day_occupancy(
        _mock_session(), 2026, 4, 15, ZoneInfo("Europe/Moscow")
    )

    assert occ.minute_slots == {(18, 30)}


async def test_exclude_submission_id_is_forwarded_to_query(monkeypatch) -> None:
    mock = AsyncMock(return_value=[])
    monkeypatch.setattr(schedule_occupancy, "get_scheduled_times_between", mock)

    await schedule_occupancy.get_month_occupancy(
        _mock_session(), 2026, 4, ZoneInfo("Europe/Moscow"), exclude_submission_id=42
    )
    assert mock.call_args.kwargs["exclude_submission_id"] == 42

    mock.reset_mock()
    await schedule_occupancy.get_day_occupancy(
        _mock_session(), 2026, 4, 15, ZoneInfo("Europe/Moscow"), exclude_submission_id=42
    )
    assert mock.call_args.kwargs["exclude_submission_id"] == 42


async def test_query_bounds_are_utc_matching_local_month_and_day(monkeypatch) -> None:
    mock = AsyncMock(return_value=[])
    monkeypatch.setattr(schedule_occupancy, "get_scheduled_times_between", mock)

    await schedule_occupancy.get_month_occupancy(
        _mock_session(), 2026, 4, ZoneInfo("Europe/Moscow")
    )
    start, end = mock.call_args.args[1], mock.call_args.args[2]
    assert start == datetime(2026, 3, 31, 21, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 4, 30, 21, 0, tzinfo=timezone.utc)
    assert start.tzinfo is timezone.utc
    assert end.tzinfo is timezone.utc

    mock.reset_mock()
    await schedule_occupancy.get_day_occupancy(
        _mock_session(), 2026, 4, 15, ZoneInfo("Europe/Moscow")
    )
    day_start, day_end = mock.call_args.args[1], mock.call_args.args[2]
    assert day_start == datetime(2026, 4, 14, 21, 0, tzinfo=timezone.utc)
    assert day_end == datetime(2026, 4, 15, 21, 0, tzinfo=timezone.utc)


async def test_dst_boundary_day_does_not_lose_or_duplicate_posts(monkeypatch) -> None:
    # Europe/Berlin springs forward on 2026-03-29 (02:00 -> 03:00 CET->CEST).
    entries = [
        (datetime(2026, 3, 29, 0, 30, tzinfo=timezone.utc), 1),  # 01:30 CET
        (datetime(2026, 3, 29, 10, 0, tzinfo=timezone.utc), 2),  # 12:00 CEST
        (datetime(2026, 3, 29, 20, 0, tzinfo=timezone.utc), 3),  # 22:00 CEST
    ]
    mock = AsyncMock(return_value=entries)
    monkeypatch.setattr(schedule_occupancy, "get_scheduled_times_between", mock)

    occ = await schedule_occupancy.get_day_occupancy(
        _mock_session(), 2026, 3, 29, ZoneInfo("Europe/Berlin")
    )

    assert [sub_id for _dt, sub_id in occ.entries] == [1, 2, 3]
    assert len(occ.entries) == 3


async def test_month_occupancy_december_rolls_over_to_january(monkeypatch) -> None:
    mock = AsyncMock(return_value=[])
    monkeypatch.setattr(schedule_occupancy, "get_scheduled_times_between", mock)

    await schedule_occupancy.get_month_occupancy(
        _mock_session(), 2026, 12, ZoneInfo("Europe/Moscow")
    )

    start, end = mock.call_args.args[1], mock.call_args.args[2]
    assert start == datetime(2026, 11, 30, 21, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 12, 31, 21, 0, tzinfo=timezone.utc)


async def test_naive_publish_at_from_driver_is_treated_as_utc(monkeypatch) -> None:
    naive_entry = (datetime(2026, 4, 15, 9, 0), 1)  # no tzinfo
    mock = AsyncMock(return_value=[naive_entry])
    monkeypatch.setattr(schedule_occupancy, "get_scheduled_times_between", mock)

    occ = await schedule_occupancy.get_day_occupancy(
        _mock_session(), 2026, 4, 15, ZoneInfo("Europe/Moscow")
    )

    assert occ.hours == {12}
