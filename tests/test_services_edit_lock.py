"""Tests for services/edit_lock.py.

Unit tests use a mock AsyncSession and run without a DB.
Integration tests require TEST_DATABASE_URL and are in tests/integration/.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock


from services import edit_lock
from db.models import EditLock


# ── Helpers ────────────────────────────────────────────────────────

def _make_lock(
    resource_type: str = "submission",
    resource_id: str = "1",
    moderator_id: int = 1,
    *,
    expired: bool = False,
) -> EditLock:
    now = datetime.now(tz=timezone.utc)
    lock = EditLock()
    lock.resource_type = resource_type
    lock.resource_id = resource_id
    lock.moderator_id = moderator_id
    lock.acquired_at = now - timedelta(minutes=5)
    lock.expires_at = (now - timedelta(minutes=1)) if expired else (now + timedelta(minutes=25))
    return lock


# ── get_active_lock ────────────────────────────────────────────────

async def test_get_active_lock_returns_none_when_no_row() -> None:
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.execute.return_value = mock_result

    result = await edit_lock.get_active_lock(session, "submission", "42")

    assert result is None


async def test_get_active_lock_returns_lock_when_exists() -> None:
    lock = _make_lock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = lock
    session = AsyncMock()
    session.execute.return_value = mock_result

    result = await edit_lock.get_active_lock(session, "submission", "1")

    assert result is lock


# ── list_expired_locks ─────────────────────────────────────────────

async def test_list_expired_locks_returns_empty_when_none() -> None:
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.return_value = mock_result

    result = await edit_lock.list_expired_locks(session)

    assert result == []


async def test_list_expired_locks_returns_expired_rows() -> None:
    lock1 = _make_lock(resource_id="1", expired=True)
    lock2 = _make_lock(resource_id="2", expired=True)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [lock1, lock2]
    session = AsyncMock()
    session.execute.return_value = mock_result

    result = await edit_lock.list_expired_locks(session)

    assert len(result) == 2


# ── release_lock ───────────────────────────────────────────────────

async def test_release_lock_returns_true_on_success() -> None:
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 1
    session.execute.return_value = mock_result

    result = await edit_lock.release_lock(session, "submission", "5", moderator_id=1)

    assert result is True
    session.flush.assert_awaited_once()


async def test_release_lock_returns_false_when_not_owner() -> None:
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 0
    session.execute.return_value = mock_result

    result = await edit_lock.release_lock(session, "submission", "5", moderator_id=99)

    assert result is False


# ── extend_lock ────────────────────────────────────────────────────

async def test_extend_lock_returns_true_when_updated() -> None:
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 1
    session.execute.return_value = mock_result

    result = await edit_lock.extend_lock(
        session, "submission", "3", moderator_id=2, ttl_seconds=1800
    )

    assert result is True


async def test_extend_lock_returns_false_when_no_match() -> None:
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 0
    session.execute.return_value = mock_result

    result = await edit_lock.extend_lock(
        session, "submission", "3", moderator_id=99, ttl_seconds=1800
    )

    assert result is False


# ── force_release_lock ─────────────────────────────────────────────

async def test_force_release_lock_returns_owner_id() -> None:
    session = AsyncMock()
    mock_row = MagicMock()
    mock_row.__getitem__ = MagicMock(return_value=7)
    mock_result = MagicMock()
    mock_result.fetchone.return_value = mock_row
    session.execute.return_value = mock_result

    result = await edit_lock.force_release_lock(session, "submission", "1")

    assert result == 7
    session.flush.assert_awaited_once()


async def test_force_release_lock_returns_none_when_no_lock() -> None:
    mock_result = MagicMock()
    mock_result.fetchone.return_value = None
    session = AsyncMock()
    session.execute.return_value = mock_result

    result = await edit_lock.force_release_lock(session, "submission", "999")

    assert result is None


# ── cleanup_expired_locks ──────────────────────────────────────────

async def test_cleanup_expired_locks_returns_empty_when_none() -> None:
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    session = AsyncMock()
    session.execute.return_value = mock_result

    result = await edit_lock.cleanup_expired_locks(session)

    assert result == []
    session.execute.assert_awaited_once()


async def test_cleanup_expired_locks_deletes_and_returns_locks() -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(tz=timezone.utc)

    row1 = MagicMock()
    row1.resource_type = "submission"
    row1.resource_id = "1"
    row1.moderator_id = 1
    row1.acquired_at = now - timedelta(minutes=10)
    row1.expires_at = now - timedelta(minutes=1)

    row2 = MagicMock()
    row2.resource_type = "submission"
    row2.resource_id = "2"
    row2.moderator_id = 2
    row2.acquired_at = now - timedelta(minutes=10)
    row2.expires_at = now - timedelta(minutes=1)

    mock_result = MagicMock()
    mock_result.fetchall.return_value = [row1, row2]
    session = AsyncMock()
    session.execute.return_value = mock_result

    result = await edit_lock.cleanup_expired_locks(session)

    assert len(result) == 2
    assert result[0].resource_id == "1"
    assert result[1].resource_id == "2"
    session.execute.assert_awaited_once()
    session.flush.assert_awaited_once()


