"""Tests for calendar/time-picker keyboards and busy-slot markers."""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from keyboards.calendar import calendar_kb, hours_kb, minutes_kb
from keyboards.callbacks import CalendarCB, TimeCB

TZ = ZoneInfo("Europe/Moscow")


def _future_month() -> tuple[int, int]:
    """A year/month far enough in the future that all days are selectable."""
    d = date.today() + timedelta(days=60)
    return d.year, d.month


def _all_day_buttons(kb):
    buttons = []
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data and btn.callback_data.startswith("cal:"):
                cb = CalendarCB.unpack(btn.callback_data)
                if cb.action == "day":
                    buttons.append(btn)
    return buttons


class TestCalendarKb:
    def test_no_busy_days_keeps_plain_numbers(self):
        year, month = _future_month()
        kb = calendar_kb(year, month, TZ)
        for btn in _all_day_buttons(kb):
            assert btn.text.isdigit()

    def _day_button(self, kb, day: int):
        for btn in _all_day_buttons(kb):
            if CalendarCB.unpack(btn.callback_data).day == day:
                return btn
        return None

    def test_busy_day_gets_circled_count(self):
        year, month = _future_month()
        kb = calendar_kb(year, month, TZ, busy_days={15: 2})
        target = self._day_button(kb, 15)
        assert target is not None
        assert target.text == "15 ❷"
        cb = CalendarCB.unpack(target.callback_data)
        assert cb.action == "day"
        assert cb.day == 15

    def test_busy_day_count_in_second_circled_decade(self):
        year, month = _future_month()
        kb = calendar_kb(year, month, TZ, busy_days={20: 12})
        target = self._day_button(kb, 20)
        assert target is not None
        assert target.text == "20 ⓬"

    def test_busy_day_count_at_the_circled_ceiling(self):
        year, month = _future_month()
        kb = calendar_kb(year, month, TZ, busy_days={20: 20})
        target = self._day_button(kb, 20)
        assert target is not None
        assert target.text == "20 ⓴"

    def test_busy_day_count_beyond_circled_range_falls_back_to_parentheses(self):
        year, month = _future_month()
        kb = calendar_kb(year, month, TZ, busy_days={20: 21})
        target = self._day_button(kb, 20)
        assert target is not None
        assert target.text == "20 (21)"


class TestHoursKb:
    def test_busy_hour_gets_marker(self):
        year, month = _future_month()
        day = 1
        kb = hours_kb(year, month, day, TZ, busy_hours={12})
        marked = []
        unmarked = []
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data and btn.callback_data.startswith("time:"):
                    cb = TimeCB.unpack(btn.callback_data)
                    if cb.hour == 12:
                        marked.append(btn)
                    else:
                        unmarked.append(btn)
        assert len(marked) == 1
        assert marked[0].text == "\U0001f538" + "12"
        cb = TimeCB.unpack(marked[0].callback_data)
        assert cb.hour == 12
        for btn in unmarked:
            assert "\U0001f538" not in btn.text


class TestMinutesKb:
    def test_busy_minute_gets_marker(self):
        year, month = _future_month()
        day = 1
        kb = minutes_kb(18, year, month, day, TZ, busy_minutes={30})
        found = False
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data and btn.callback_data.startswith("time:"):
                    cb = TimeCB.unpack(btn.callback_data)
                    if cb.hour == 18 and cb.minute == 30:
                        assert btn.text == "\U0001f53818:30"
                        found = True
        assert found

    def test_past_minutes_today_still_dots(self):
        from datetime import datetime

        now = datetime.now(TZ)
        today = now.date()
        kb = minutes_kb(now.hour, today.year, today.month, today.day, TZ)
        # There should be at least one "·" ignore button if we're not at :00-:04
        dots = [
            btn
            for row in kb.inline_keyboard
            for btn in row
            if btn.text == "·"
        ]
        if now.minute > 4:
            assert dots
