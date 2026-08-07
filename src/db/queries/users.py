"""User-related DB queries."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import CannotBanModeratorError
from db.models import Submission, User


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
    full_name: str,
    is_admin: bool = False,
) -> tuple[User, bool]:
    """Upsert user. Returns ``(user, is_new)``.

    On conflict only ``username``/``full_name`` are refreshed —
    ``is_admin``/``is_moderator`` are deliberately NOT touched: those flags are
    owned by ``bootstrap_roles()`` at startup and by the roles service at
    runtime, the DB is the source of truth rather than the config. The
    ``is_admin`` parameter applies on the INSERT path only (fresh rows).

    ``is_new`` is derived from ``xmax = 0``: PostgreSQL sets xmax to 0 for
    freshly inserted rows and to the updating transaction XID for updated ones,
    making this a race-free alternative to a separate existence SELECT.
    """
    stmt = (
        pg_insert(User)
        .values(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name,
            is_admin=is_admin,
        )
        .on_conflict_do_update(
            index_elements=[User.telegram_id],
            set_=dict(
                username=username,
                full_name=full_name,
                # is_admin/is_moderator are owned by bootstrap_roles() and the
                # roles service — never touched here on conflict
                updated_at=func.now(),
            ),
        )
        .returning(User, literal_column("(xmax::text::bigint = 0)").label("is_new"))
    )
    result = await session.execute(stmt)
    row = result.fetchone()
    user: User = row[0]
    is_new: bool = bool(row[1])
    await session.flush()
    return user, is_new


async def ban_user(session: AsyncSession, user_id: int, reason: str) -> None:
    """Ban *user_id* unless they still hold a moderator/admin role.

    The row is locked ``FOR UPDATE`` and re-read so a concurrent role grant
    cannot slip past the handler's pre-check and leave a user both banned and
    a moderator.
    """
    target = await session.get(
        User, user_id, with_for_update=True, populate_existing=True,
    )
    if target is not None and (target.is_moderator or target.is_admin):
        raise CannotBanModeratorError()
    stmt = (
        update(User)
        .where(User.id == user_id)
        .values(is_banned=True, ban_reason=reason, updated_at=func.now())
    )
    await session.execute(stmt)
    await session.flush()


async def unban_user(session: AsyncSession, user_id: int) -> None:
    stmt = (
        update(User)
        .where(User.id == user_id)
        .values(is_banned=False, ban_reason=None, updated_at=func.now())
    )
    await session.execute(stmt)
    await session.flush()


async def get_banned_users(session: AsyncSession) -> list[User]:
    stmt = select(User).where(User.is_banned.is_(True)).order_by(User.full_name)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_admin_users(session: AsyncSession) -> list[User]:
    """Return all users with ``is_admin=True`` (for DM notifications)."""
    stmt = select(User).where(User.is_admin.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    """Return a User by internal DB id, or None."""
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    """Return a User by Telegram user id, or None."""
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def get_users_by_username(session: AsyncSession, username: str) -> list[User]:
    """Return up to 2 Users matching *username* (case-insensitive).

    ``username`` is not unique in the schema: a Telegram handle can be
    released and picked up by someone else, leaving a stale row with the old
    owner's ``username`` unless it's manually cleared — nothing in the row
    (not even ``updated_at``, which changes on bans/notes/role edits unrelated
    to identity) tells us which row is the *current* holder of the handle.
    Rather than guess, this returns every match (capped at 2, just enough to
    tell "one" from "more than one") and leaves the ambiguity to the caller:
    a human with the numeric Telegram id is the only reliable tiebreaker.
    """
    stmt = (
        select(User)
        .where(func.lower(User.username) == username.lower())
        .order_by(User.id)
        .limit(2)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def set_moderator_note(session: AsyncSession, user_id: int, note: str | None) -> None:
    """Set or clear (``note=None``) the moderator's note about the user."""
    stmt = (
        update(User)
        .where(User.id == user_id)
        .values(moderator_note=note, updated_at=func.now())
    )
    await session.execute(stmt)
    await session.flush()


@dataclass(frozen=True)
class AuthorStats:
    total: int
    published: int
    rejected: int
    pending: int
    scheduled: int
    cancelled: int
    first_seen: datetime | None


async def get_author_stats(session: AsyncSession, user_id: int) -> AuthorStats:
    """Aggregate submission counts per status for the author *user_id*."""
    stmt = (
        select(
            Submission.status,
            func.count().label("cnt"),
            func.min(Submission.created_at).label("first_seen"),
        )
        .where(Submission.user_id == user_id)
        .group_by(Submission.status)
    )
    result = await session.execute(stmt)
    counts: dict[str, int] = {}
    first_seen: datetime | None = None
    for status, cnt, first in result.all():
        counts[status] = cnt
        if first is not None and (first_seen is None or first < first_seen):
            first_seen = first
    return AuthorStats(
        total=sum(counts.values()),
        published=counts.get("published", 0),
        rejected=counts.get("rejected", 0),
        pending=counts.get("pending", 0),
        scheduled=counts.get("scheduled", 0),
        cancelled=counts.get("cancelled", 0),
        first_seen=first_seen,
    )
