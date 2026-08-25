"""Submission media DB queries."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import SubmissionMedia


async def add_media(
    session: AsyncSession,
    submission_id: int,
    file_id: str,
    file_unique_id: str,
    media_type: str,
    sort_order: int = 0,
) -> SubmissionMedia:
    media = SubmissionMedia(
        submission_id=submission_id,
        file_id=file_id,
        file_unique_id=file_unique_id,
        media_type=media_type,
        sort_order=sort_order,
    )
    session.add(media)
    await session.flush()
    return media


async def get_submission_media(
    session: AsyncSession, submission_id: int
) -> list[SubmissionMedia]:
    stmt = (
        select(SubmissionMedia)
        .where(SubmissionMedia.submission_id == submission_id)
        .order_by(SubmissionMedia.sort_order)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def delete_media(session: AsyncSession, media_id: int, submission_id: int) -> bool:
    """Delete one media row scoped to its submission. Returns True if a row was deleted."""
    stmt = (
        delete(SubmissionMedia)
        .where(SubmissionMedia.id == media_id)
        .where(SubmissionMedia.submission_id == submission_id)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount > 0


async def delete_media_unless_last(
    session: AsyncSession, media_id: int, submission_id: int
) -> bool:
    """Delete one media row, but never the last one of its submission.

    Rows of the submission are locked ``FOR UPDATE`` before counting, so two
    concurrent deletes of different items serialise and the second one sees the
    already-reduced count instead of a stale snapshot.
    """
    lock_stmt = (
        select(SubmissionMedia.id)
        .where(SubmissionMedia.submission_id == submission_id)
        .with_for_update()
    )
    locked = await session.execute(lock_stmt)
    ids = list(locked.scalars().all())
    if len(ids) <= 1 or media_id not in ids:
        return False
    stmt = (
        delete(SubmissionMedia)
        .where(SubmissionMedia.id == media_id)
        .where(SubmissionMedia.submission_id == submission_id)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount > 0
