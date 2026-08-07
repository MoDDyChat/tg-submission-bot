"""Submission-related DB queries."""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Submission


async def create_submission(
    session: AsyncSession,
    user_id: int,
    caption: str | None,
    tags: list[str] | None = None,
) -> Submission:
    sub = Submission(user_id=user_id, caption=caption, tags=tags or [])
    session.add(sub)
    await session.flush()
    return sub


async def get_submission(
    session: AsyncSession, sub_id: int, *, with_media: bool = False
) -> Submission | None:
    stmt = select(Submission).where(Submission.id == sub_id)
    if with_media:
        stmt = stmt.options(selectinload(Submission.media))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_submission_with_user(
    session: AsyncSession, sub_id: int
) -> Submission | None:
    stmt = (
        select(Submission)
        .options(selectinload(Submission.user), selectinload(Submission.media))
        .where(Submission.id == sub_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_active_submissions(session: AsyncSession) -> list[Submission]:
    """All submissions with non-terminal status (pending, scheduled)."""
    terminal = ("published", "rejected", "cancelled")
    stmt = (
        select(Submission)
        .options(selectinload(Submission.user), selectinload(Submission.media))
        .where(Submission.status.notin_(terminal))
        .order_by(Submission.created_at.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_submission_status(
    session: AsyncSession, sub_id: int, status: str
) -> None:
    stmt = (
        update(Submission)
        .where(Submission.id == sub_id)
        .values(status=status, updated_at=func.now())
    )
    await session.execute(stmt)
    await session.flush()


async def update_submission_tags(
    session: AsyncSession, sub_id: int, tags: list[str]
) -> None:
    stmt = (
        update(Submission)
        .where(Submission.id == sub_id)
        .values(tags=tags, updated_at=func.now())
    )
    await session.execute(stmt)
    await session.flush()


async def update_submission_caption(
    session: AsyncSession, sub_id: int, caption: str | None
) -> None:
    stmt = (
        update(Submission)
        .where(Submission.id == sub_id)
        .values(caption=caption, updated_at=func.now())
    )
    await session.execute(stmt)
    await session.flush()


async def get_submission_by_topic_card_id(
    session: AsyncSession,
    msg_id: int,
) -> Submission | None:
    """Return the submission whose topic card has the given message_id, or None."""
    stmt = select(Submission).where(Submission.topic_card_message_id == msg_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def count_submissions_by_status(session: AsyncSession) -> dict[str, int]:
    """Return a ``{status: count}`` mapping over all submissions."""
    stmt = select(Submission.status, func.count()).group_by(Submission.status)
    result = await session.execute(stmt)
    return {status: count for status, count in result.all()}


async def count_recent_rejections(session: AsyncSession, since: datetime) -> int:
    """Count submissions rejected at or after *since*."""
    stmt = (
        select(func.count())
        .select_from(Submission)
        .where(Submission.status == "rejected", Submission.updated_at >= since)
    )
    result = await session.execute(stmt)
    return result.scalar_one()
