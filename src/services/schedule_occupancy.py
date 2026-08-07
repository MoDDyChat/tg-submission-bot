"""Occupancy data for the schedule wizard: which days/hours/minutes are taken."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from db.queries import get_scheduled_times_between


@dataclass
class DayOccupancy:
    """Local-time occupancy of a single day."""

    hours: set[int] = field(default_factory=set)
    minute_slots: set[tuple[int, int]] = field(default_factory=set)  # (hour, 5-min-floored minute)
    entries: list[tuple[datetime, int]] = field(default_factory=list)  # (local dt, submission_id), sorted


def _to_local(publish_at: datetime, tz: ZoneInfo) -> datetime:
    if publish_at.tzinfo is None:
        publish_at = publish_at.replace(tzinfo=timezone.utc)
    return publish_at.astimezone(tz)


async def get_month_occupancy(
    session: AsyncSession,
    year: int,
    month: int,
    tz: ZoneInfo,
    exclude_submission_id: int | None = None,
) -> dict[int, int]:
    """Map day-of-month -> count of scheduled posts, in local tz."""
    start_local = datetime(year, month, 1, tzinfo=tz)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=tz)
    else:
        end_local = datetime(year, month + 1, 1, tzinfo=tz)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    rows = await get_scheduled_times_between(
        session, start_utc, end_utc, exclude_submission_id=exclude_submission_id
    )

    counts: dict[int, int] = {}
    for publish_at, _submission_id in rows:
        local_dt = _to_local(publish_at, tz)
        counts[local_dt.day] = counts.get(local_dt.day, 0) + 1
    return counts


async def get_day_occupancy(
    session: AsyncSession,
    year: int,
    month: int,
    day: int,
    tz: ZoneInfo,
    exclude_submission_id: int | None = None,
) -> DayOccupancy:
    start_local = datetime(year, month, day, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    rows = await get_scheduled_times_between(
        session, start_utc, end_utc, exclude_submission_id=exclude_submission_id
    )

    occupancy = DayOccupancy()
    for publish_at, submission_id in rows:
        local_dt = _to_local(publish_at, tz)
        occupancy.hours.add(local_dt.hour)
        occupancy.minute_slots.add((local_dt.hour, local_dt.minute - local_dt.minute % 5))
        occupancy.entries.append((local_dt, submission_id))
    return occupancy
