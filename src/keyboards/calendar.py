"""Inline calendar and time picker widgets."""

import calendar
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from keyboards.callbacks import CalendarCB, TimeCB

MONTH_NAMES_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]

DAY_NAMES_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _ignore_btn(year: int, month: int, day: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="·",
        callback_data=CalendarCB(action="ignore", year=year, month=month, day=day).pack(),
    )


def calendar_kb(year: int, month: int, tz: ZoneInfo) -> InlineKeyboardMarkup:
    """Build an inline calendar for the given month."""
    today = datetime.now(tz).date()

    rows: list[list[InlineKeyboardButton]] = []

    # Header: < Month Year >
    rows.append([
        InlineKeyboardButton(
            text="◀",
            callback_data=CalendarCB(
                action="prev_month", year=year, month=month, day=0
            ).pack(),
        ),
        InlineKeyboardButton(
            text=f"{MONTH_NAMES_RU[month]} {year}",
            callback_data=CalendarCB(
                action="ignore", year=year, month=month, day=0
            ).pack(),
        ),
        InlineKeyboardButton(
            text="▶",
            callback_data=CalendarCB(
                action="next_month", year=year, month=month, day=0
            ).pack(),
        ),
    ])

    # Day-of-week headers
    rows.append([
        InlineKeyboardButton(
            text=day,
            callback_data=CalendarCB(
                action="ignore", year=year, month=month, day=0
            ).pack(),
        )
        for day in DAY_NAMES_RU
    ])

    # Days grid
    cal = calendar.monthcalendar(year, month)
    for week in cal:
        row: list[InlineKeyboardButton] = []
        for day_num in week:
            if day_num == 0:
                row.append(_ignore_btn(year, month, 0))
            else:
                d = date(year, month, day_num)
                if d < today:
                    row.append(_ignore_btn(year, month, 0))
                else:
                    row.append(InlineKeyboardButton(
                        text=str(day_num),
                        callback_data=CalendarCB(
                            action="day", year=year, month=month, day=day_num
                        ).pack(),
                    ))
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def hours_kb(year: int, month: int, day: int, tz: ZoneInfo) -> InlineKeyboardMarkup:
    """Grid of hours (0-23), disabling past hours when the date is today."""
    now = datetime.now(tz)
    is_today = date(year, month, day) == now.date()
    # If it's today and we're at :56 or later, there are no valid minutes in the
    # current hour, so advance min_hour by one to avoid showing an empty minutes page.
    if is_today:
        min_hour = now.hour + 1 if now.minute > 55 else now.hour
    else:
        min_hour = -1

    rows: list[list[InlineKeyboardButton]] = []
    for start in range(0, 24, 6):
        row = []
        for h in range(start, min(start + 6, 24)):
            if h < min_hour:
                row.append(_ignore_btn(year, month, day))
            else:
                row.append(InlineKeyboardButton(
                    text=f"{h:02d}",
                    callback_data=TimeCB(hour=h, minute=-1).pack(),
                ))
        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            text="← Назад",
            callback_data=CalendarCB(
                action="back_to_cal", year=year, month=month, day=day
            ).pack(),
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def minutes_kb(hour: int, year: int, month: int, day: int, tz: ZoneInfo) -> InlineKeyboardMarkup:
    """Minute selection with 5-minute step, disabling past minutes for today's current hour."""
    now = datetime.now(tz)
    is_current_hour = date(year, month, day) == now.date() and hour == now.hour
    min_minute = now.minute if is_current_hour else -1

    all_minutes = list(range(0, 60, 5))  # 00, 05, 10, ..., 55

    rows: list[list[InlineKeyboardButton]] = []
    for start in range(0, len(all_minutes), 4):
        row = []
        for m in all_minutes[start:start + 4]:
            if m < min_minute:
                row.append(_ignore_btn(year, month, day))
            else:
                row.append(InlineKeyboardButton(
                    text=f"{hour:02d}:{m:02d}",
                    callback_data=TimeCB(hour=hour, minute=m).pack(),
                ))
        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            text="← Назад",
            callback_data=CalendarCB(
                action="back_to_hours", year=year, month=month, day=day
            ).pack(),
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)
