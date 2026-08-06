"""Integration tests for services/edit_lock.py — concurrent & TTL behaviour.

Requires TEST_DATABASE_URL pointing at a real PostgreSQL database.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine

from services import edit_lock

pytestmark = pytest.mark.integration


# ── basic acquire / release ──────────────────────────────────────────

async def test_acquire_returns_true_for_first_caller(db_session: AsyncSession) -> None:
    ok, owner = await edit_lock.acquire_lock(
        db_session, "submission", "100", moderator_id=1, ttl_seconds=60
    )
    await db_session.commit()
    assert ok is True
    assert owner == 1


async def test_second_caller_gets_false_while_lock_held(db_session: AsyncSession) -> None:
    await edit_lock.acquire_lock(db_session, "submission", "200", moderator_id=1, ttl_seconds=60)
    await db_session.commit()

    ok, owner = await edit_lock.acquire_lock(
        db_session, "submission", "200", moderator_id=2, ttl_seconds=60
    )
    await db_session.commit()
    assert ok is False
    assert owner == 1


async def test_same_moderator_can_extend_own_lock(db_session: AsyncSession) -> None:
    await edit_lock.acquire_lock(db_session, "submission", "300", moderator_id=5, ttl_seconds=60)
    await db_session.commit()

    ok, owner = await edit_lock.acquire_lock(
        db_session, "submission", "300", moderator_id=5, ttl_seconds=60
    )
    await db_session.commit()
    assert ok is True
    assert owner == 5


async def test_release_allows_new_owner(db_session: AsyncSession) -> None:
    await edit_lock.acquire_lock(db_session, "submission", "400", moderator_id=1, ttl_seconds=60)
    await db_session.commit()

    released = await edit_lock.release_lock(db_session, "submission", "400", moderator_id=1)
    await db_session.commit()
    assert released is True

    ok, owner = await edit_lock.acquire_lock(
        db_session, "submission", "400", moderator_id=2, ttl_seconds=60
    )
    await db_session.commit()
    assert ok is True
    assert owner == 2


# ── extend / get_active ──────────────────────────────────────────────

async def test_extend_lock_returns_true_for_owner(db_session: AsyncSession) -> None:
    await edit_lock.acquire_lock(db_session, "submission", "500", moderator_id=7, ttl_seconds=60)
    await db_session.commit()

    ok = await edit_lock.extend_lock(db_session, "submission", "500", moderator_id=7, ttl_seconds=60)
    await db_session.commit()
    assert ok is True


async def test_extend_lock_returns_false_for_non_owner(db_session: AsyncSession) -> None:
    await edit_lock.acquire_lock(db_session, "submission", "600", moderator_id=7, ttl_seconds=60)
    await db_session.commit()

    ok = await edit_lock.extend_lock(db_session, "submission", "600", moderator_id=8, ttl_seconds=60)
    await db_session.commit()
    assert ok is False


async def test_get_active_lock_returns_owner(db_session: AsyncSession) -> None:
    await edit_lock.acquire_lock(db_session, "submission", "700", moderator_id=9, ttl_seconds=60)
    await db_session.commit()

    lock = await edit_lock.get_active_lock(db_session, "submission", "700")
    assert lock is not None
    assert lock.moderator_id == 9


async def test_get_active_lock_returns_none_when_absent(db_session: AsyncSession) -> None:
    lock = await edit_lock.get_active_lock(db_session, "submission", "9999")
    assert lock is None


# ── TTL / cleanup ─────────────────────────────────────────────────────

async def test_cleanup_expired_locks_removes_expired(
    db_session: AsyncSession, integration_engine: AsyncEngine
) -> None:
    """Insert a lock with an already-expired expires_at, then cleanup should remove it."""
    from sqlalchemy import update
    from db.models import EditLock

    await edit_lock.acquire_lock(db_session, "submission", "800", moderator_id=3, ttl_seconds=60)
    await db_session.commit()

    # Manually expire it
    stmt = (
        update(EditLock)
        .where(EditLock.resource_type == "submission", EditLock.resource_id == "800")
        .values(expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
    )
    await db_session.execute(stmt)
    await db_session.commit()

    removed = await edit_lock.cleanup_expired_locks(db_session)
    await db_session.commit()

    resource_ids = [lock.resource_id for lock in removed]
    assert "800" in resource_ids


async def test_acquire_succeeds_after_ttl_expiry(
    db_session: AsyncSession,
) -> None:
    """An expired lock should be acquirable by a different moderator."""
    from sqlalchemy import update
    from db.models import EditLock

    await edit_lock.acquire_lock(db_session, "submission", "900", moderator_id=1, ttl_seconds=60)
    await db_session.commit()

    # Expire the lock
    stmt = (
        update(EditLock)
        .where(EditLock.resource_type == "submission", EditLock.resource_id == "900")
        .values(expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
    )
    await db_session.execute(stmt)
    await db_session.commit()

    ok, owner = await edit_lock.acquire_lock(
        db_session, "submission", "900", moderator_id=2, ttl_seconds=60
    )
    await db_session.commit()
    assert ok is True
    assert owner == 2
