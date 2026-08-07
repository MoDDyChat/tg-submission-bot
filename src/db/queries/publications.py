"""Publication-related DB queries."""

from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Publication, Submission


async def create_publication(
    session: AsyncSession,
    submission_id: int,
    edited_caption: str | None,
    publish_at: datetime,
) -> Publication:
    pub = Publication(
        submission_id=submission_id,
        edited_caption=edited_caption,
        publish_at=publish_at,
    )
    session.add(pub)
    await session.flush()
    return pub


async def get_publication(
    session: AsyncSession, pub_id: int
) -> Publication | None:
    stmt = select(Publication).where(Publication.id == pub_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_publication_by_submission(
    session: AsyncSession, submission_id: int
) -> Publication | None:
    stmt = select(Publication).where(Publication.submission_id == submission_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_unpublished_publications(
    session: AsyncSession,
) -> list[Publication]:
    stmt = (
        select(Publication)
        .options(
            selectinload(Publication.submission).selectinload(Submission.media),
            selectinload(Publication.submission).selectinload(Submission.user),
        )
        .where(Publication.published_at.is_(None))
        .order_by(Publication.publish_at.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def mark_published(
    session: AsyncSession,
    pub_id: int,
    channel_message_id: int | None = None,
    channel_message_ids: list[int] | None = None,
) -> None:
    stmt = (
        update(Publication)
        .where(Publication.id == pub_id)
        .values(
            published_at=func.now(),
            channel_message_id=channel_message_id,
            channel_message_ids=channel_message_ids,
            updated_at=func.now(),
        )
    )
    await session.execute(stmt)
    await session.flush()


async def mark_publication_dead(session: AsyncSession, pub_id: int) -> bool:
    """Mark a publication dead unless it already is. Returns ``True`` on first mark.

    The ``dead_at IS NULL`` guard makes the UPDATE atomic — a restart of the
    bot must not re-notify admins about the same publication.
    """
    stmt = (
        update(Publication)
        .where(Publication.id == pub_id, Publication.dead_at.is_(None))
        .values(dead_at=func.now(), updated_at=func.now())
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount > 0


async def list_dead_publications(session: AsyncSession) -> list[Publication]:
    stmt = (
        select(Publication)
        .options(selectinload(Publication.submission).selectinload(Submission.user))
        .where(Publication.published_at.is_(None), Publication.dead_at.is_not(None))
        .order_by(Publication.publish_at.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_publication_time(
    session: AsyncSession, pub_id: int, publish_at: datetime
) -> None:
    stmt = (
        update(Publication)
        .where(Publication.id == pub_id)
        .values(publish_at=publish_at, dead_at=None, updated_at=func.now())
    )
    await session.execute(stmt)
    await session.flush()


async def count_recent_publications(session: AsyncSession, since: datetime) -> int:
    """Count publications published at or after *since*."""
    stmt = (
        select(func.count())
        .select_from(Publication)
        .where(Publication.published_at >= since)
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def delete_publication(session: AsyncSession, pub_id: int) -> None:
    stmt = delete(Publication).where(Publication.id == pub_id)
    await session.execute(stmt)
    await session.flush()
