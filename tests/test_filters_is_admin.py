"""Unit tests for filters/is_admin.py."""

from __future__ import annotations

from types import SimpleNamespace

from filters.is_admin import IsAdmin
from tests.helpers import make_callback, make_message, make_user


async def test_is_admin_passes_for_admin_user() -> None:
    db_user = make_user()
    db_user.is_admin = True

    msg = make_message(from_user_id=db_user.telegram_id)
    assert await IsAdmin()(msg, db_user=db_user) is True


async def test_is_admin_denies_regular_user() -> None:
    db_user = make_user()
    db_user.is_admin = False

    msg = make_message(from_user_id=db_user.telegram_id)
    assert await IsAdmin()(msg, db_user=db_user) is False


async def test_is_admin_works_with_callback() -> None:
    db_user = make_user()
    db_user.is_admin = True

    cb = make_callback(from_user_id=db_user.telegram_id)
    assert await IsAdmin()(cb, db_user=db_user) is True


async def test_is_admin_denies_removed_flag() -> None:
    db_user = make_user()
    db_user.is_admin = True

    msg = make_message(from_user_id=db_user.telegram_id)
    assert await IsAdmin()(msg, db_user=db_user) is True

    db_user.is_admin = False
    assert await IsAdmin()(msg, db_user=db_user) is False


async def test_is_admin_reads_db_user_not_event_from_user() -> None:
    """The flag must come from db_user; the event's from_user is irrelevant."""
    db_user = make_user()
    db_user.is_admin = True

    event = SimpleNamespace(from_user=None)
    assert await IsAdmin()(event, db_user=db_user) is True
