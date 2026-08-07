"""DB queries for moderator/admin role flags."""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User


async def list_moderators(session: AsyncSession) -> list[User]:
    """Return all users with ``is_moderator=True`` — admins first, then by name."""
    stmt = (
        select(User)
        .where(User.is_moderator.is_(True))
        .order_by(User.is_admin.desc(), User.full_name)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_admins(session: AsyncSession) -> int:
    """Return the number of users with ``is_admin=True``."""
    stmt = select(func.count()).select_from(User).where(User.is_admin.is_(True))
    result = await session.execute(stmt)
    return result.scalar_one()


async def set_roles(
    session: AsyncSession,
    user_id: int,
    *,
    is_moderator: bool,
    is_admin: bool,
    granted_by: int | None,
    granted_at: datetime | None,
) -> None:
    """Targeted UPDATE of a user's role flags and grant metadata."""
    stmt = (
        update(User)
        .where(User.id == user_id)
        .values(
            is_moderator=is_moderator,
            is_admin=is_admin,
            role_granted_by=granted_by,
            role_granted_at=granted_at,
            updated_at=func.now(),
        )
    )
    await session.execute(stmt)
    await session.flush()
