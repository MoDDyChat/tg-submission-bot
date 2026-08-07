"""Unit tests for filters/is_moderator.py."""

from __future__ import annotations

from types import SimpleNamespace

from filters.is_moderator import IsModerator
from tests.helpers import make_callback, make_message, make_user


async def test_is_moderator_passes_for_moderator_user() -> None:
    db_user = make_user()
    db_user.is_moderator = True

    msg = make_message(from_user_id=db_user.telegram_id)
    assert await IsModerator()(msg, db_user=db_user) is True


async def test_is_moderator_denies_regular_user() -> None:
    db_user = make_user()
    db_user.is_moderator = False

    msg = make_message(from_user_id=db_user.telegram_id)
    assert await IsModerator()(msg, db_user=db_user) is False


async def test_is_moderator_passes_for_admin_user() -> None:
    """An admin is a moderator by definition (both DB flags set)."""
    db_user = make_user()
    db_user.is_moderator = True
    db_user.is_admin = True

    msg = make_message(from_user_id=db_user.telegram_id)
    assert await IsModerator()(msg, db_user=db_user) is True


async def test_is_moderator_works_with_callback() -> None:
    db_user = make_user()
    db_user.is_moderator = True

    cb = make_callback(from_user_id=db_user.telegram_id)
    assert await IsModerator()(cb, db_user=db_user) is True


async def test_is_moderator_denies_removed_flag() -> None:
    db_user = make_user()
    db_user.is_moderator = True

    msg = make_message(from_user_id=db_user.telegram_id)
    assert await IsModerator()(msg, db_user=db_user) is True

    db_user.is_moderator = False
    assert await IsModerator()(msg, db_user=db_user) is False


async def test_is_moderator_reads_db_user_not_event_from_user() -> None:
    """The flag must come from db_user; the event's from_user is irrelevant."""
    db_user = make_user()
    db_user.is_moderator = True

    event = SimpleNamespace(from_user=None)
    assert await IsModerator()(event, db_user=db_user) is True
