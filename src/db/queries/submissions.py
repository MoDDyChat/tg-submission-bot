"""Submission-related DB queries."""

from collections.abc import Collection
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Submission


async def create_submission(
    session: AsyncSession,
    user_id: int,
    caption: str | None,
    tags: list[str] | None = None,
    suggested_tags: list[dict] | None = None,
) -> Submission:
    sub = Submission(
        user_id=user_id, caption=caption, tags=tags or [], suggested_tags=suggested_tags
    )
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


async def list_active_submissions_without_card(
    session: AsyncSession, min_age: timedelta = timedelta(minutes=10)
) -> list[Submission]:
    """Активные посты старше min_age, у которых карточка в теме так и не появилась.

    Без LIMIT: фиксированный head-лимит навсегда прятал бы хронически падающий
    пост. ``min_age`` отсекает посты, ещё не прошедшие этап карточки, — job не
    гонится с intake.
    """
    terminal = ("published", "rejected", "cancelled")
    stmt = (
        select(Submission)
        .options(selectinload(Submission.user), selectinload(Submission.media))
        .where(
            Submission.status.notin_(terminal),
            Submission.topic_card_message_id.is_(None),
            Submission.created_at < func.now() - min_age,
        )
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


async def transition_submission_status(
    session: AsyncSession,
    sub_id: int,
    new_status: str,
    *,
    expected: Collection[str],
) -> bool:
    """Move a submission to *new_status* only from one of *expected* statuses.

    Returns ``True`` if this call performed the transition. The status guard in
    the WHERE clause makes check-and-set atomic: of two concurrent clicks exactly
    one gets ``True``, the loser gets ``False`` and must skip every side effect.
    """
    stmt = (
        update(Submission)
        .where(Submission.id == sub_id, Submission.status.in_(list(expected)))
        .values(status=new_status, updated_at=func.now())
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount > 0


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
