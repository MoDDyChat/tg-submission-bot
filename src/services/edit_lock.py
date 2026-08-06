"""Edit lock service.

Provides atomic acquire/extend/release operations for per-resource edit locks
stored in the ``edit_locks`` DB table.

Primary flow uses tuple returns (no exceptions) for ergonomic handler code.
``EditLockHeldError`` is available for callers that prefer exception semantics.

Resource naming conventions:
  - ``("submission", str(sub_id))``   — lock on a specific submission
  - ``("management", "presets")``     — global lock on tag preset CRUD
  - ``("management", "banned")``      — global lock on banned-user CRUD
"""

from datetime import datetime, timezone

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging import get_logger
from db.models import EditLock

logger = get_logger(__name__)


async def acquire_lock(
    session: AsyncSession,
    resource_type: str,
    resource_id: str,
    moderator_id: int,
    *,
    ttl_seconds: int,
) -> tuple[bool, int]:
    """Atomically acquire (or extend) an edit lock.

    Uses a PostgreSQL CTE that:
      - INSERTs the lock row, or
      - UPDATEs it only if the existing lock has expired OR belongs to the same moderator.
    Then falls back to SELECT the current owner if no update occurred.

    Returns:
        ``(True, moderator_id)``   — lock acquired/extended by *this* moderator.
        ``(False, owner_id)``      — lock is held by another live moderator.
    """
    stmt = text("""
        WITH upserted AS (
            INSERT INTO edit_locks (resource_type, resource_id, moderator_id, acquired_at, expires_at)
            VALUES (:rt, :rid, :mid, NOW(), NOW() + (:ttl * INTERVAL '1 second'))
            ON CONFLICT (resource_type, resource_id) DO UPDATE
                SET moderator_id = EXCLUDED.moderator_id,
                    acquired_at  = EXCLUDED.acquired_at,
                    expires_at   = EXCLUDED.expires_at
                WHERE edit_locks.expires_at < EXCLUDED.acquired_at
                   OR edit_locks.moderator_id = EXCLUDED.moderator_id
            RETURNING moderator_id
        )
        SELECT moderator_id FROM upserted
        UNION ALL
        SELECT el.moderator_id
        FROM   edit_locks el
        WHERE  el.resource_type = :rt
          AND  el.resource_id   = :rid
          AND  NOT EXISTS (SELECT 1 FROM upserted)
        LIMIT 1
    """)
    result = await session.execute(
        stmt,
        {"rt": resource_type, "rid": resource_id, "mid": moderator_id, "ttl": ttl_seconds},
    )
    row = result.fetchone()
    if row is None:
        # Shouldn't happen; treat as success (INSERT path, nothing in conflict)
        await session.flush()
        return True, moderator_id

    owner_id: int = row[0]
    await session.flush()
    if owner_id == moderator_id:
        return True, moderator_id
    return False, owner_id


async def extend_lock(
    session: AsyncSession,
    resource_type: str,
    resource_id: str,
    moderator_id: int,
    *,
    ttl_seconds: int,
) -> bool:
    """Extend the TTL of an existing lock owned by *moderator_id*.

    Returns ``True`` if the lock was extended, ``False`` if it no longer belongs
    to this moderator or has already expired.
    """
    now = datetime.now(tz=timezone.utc)
    stmt = (
        update(EditLock)
        .where(
            EditLock.resource_type == resource_type,
            EditLock.resource_id == resource_id,
            EditLock.moderator_id == moderator_id,
            EditLock.expires_at > now,
        )
        .values(
            expires_at=text("NOW() + (:ttl * INTERVAL '1 second')").bindparams(ttl=ttl_seconds),
        )
    )
    result = await session.execute(stmt)
    await session.flush()
    return (result.rowcount or 0) > 0


async def release_lock(
    session: AsyncSession,
    resource_type: str,
    resource_id: str,
    moderator_id: int,
) -> bool:
    """Release a lock owned by *moderator_id*. Best-effort.

    Returns ``True`` if a row was deleted, ``False`` if the lock was already gone
    or belonged to someone else.
    """
    stmt = delete(EditLock).where(
        EditLock.resource_type == resource_type,
        EditLock.resource_id == resource_id,
        EditLock.moderator_id == moderator_id,
    )
    result = await session.execute(stmt)
    await session.flush()
    return (result.rowcount or 0) > 0


async def force_release_lock(
    session: AsyncSession,
    resource_type: str,
    resource_id: str,
) -> int | None:
    """Delete a lock regardless of who owns it (admin/cleanup).

    Uses a single ``DELETE ... RETURNING`` to avoid a SELECT+DELETE race.

    Returns the ``moderator_id`` of the previous owner, or ``None`` if no lock existed.
    """
    stmt = text("""
        DELETE FROM edit_locks
        WHERE resource_type = :rt AND resource_id = :rid
        RETURNING moderator_id
    """)
    result = await session.execute(stmt, {"rt": resource_type, "rid": resource_id})
    row = result.fetchone()
    await session.flush()
    return row[0] if row is not None else None


async def get_active_lock(
    session: AsyncSession,
    resource_type: str,
    resource_id: str,
) -> EditLock | None:
    """Return the active (non-expired) lock for the given resource, or ``None``."""
    now = datetime.now(tz=timezone.utc)
    stmt = select(EditLock).where(
        EditLock.resource_type == resource_type,
        EditLock.resource_id == resource_id,
        EditLock.expires_at > now,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_expired_locks(session: AsyncSession) -> list[EditLock]:
    """Return all locks whose TTL has elapsed."""
    now = datetime.now(tz=timezone.utc)
    stmt = select(EditLock).where(EditLock.expires_at <= now)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def cleanup_expired_locks(session: AsyncSession) -> list[EditLock]:
    """Delete all expired locks and return the removed rows.

    Callers are responsible for sending topic notifications for each removed lock.
    Uses a single DELETE ... RETURNING to avoid a race where another coroutine
    could acquire the lock between the SELECT and DELETE.
    """
    stmt = text("""
        DELETE FROM edit_locks
        WHERE expires_at <= NOW()
        RETURNING resource_type, resource_id, moderator_id, acquired_at, expires_at
    """)
    result = await session.execute(stmt)
    rows = result.fetchall()
    if not rows:
        return []

    expired: list[EditLock] = []
    for row in rows:
        lock = EditLock()
        lock.resource_type = row.resource_type
        lock.resource_id = row.resource_id
        lock.moderator_id = row.moderator_id
        lock.acquired_at = row.acquired_at
        lock.expires_at = row.expires_at
        expired.append(lock)

    await session.flush()
    logger.info("Удалено истёкших локов редактирования: %d", len(expired))
    return expired
