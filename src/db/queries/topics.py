"""UserTopic and topic-card-ID queries."""

from datetime import datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from db.models import Submission, UserTopic

_TERMINAL_STATUSES = ("published", "rejected", "cancelled")


async def get_user_topic(
    session: AsyncSession,
    user_id: int,
) -> UserTopic | None:
    stmt = select(UserTopic).where(UserTopic.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_user_topic(
    session: AsyncSession,
    user_id: int,
    topic_id: int,
    status_key: str,
) -> UserTopic:
    stmt = (
        pg_insert(UserTopic)
        .values(user_id=user_id, topic_id=topic_id, current_status_key=status_key)
        .on_conflict_do_update(
            index_elements=[UserTopic.user_id],
            set_=dict(
                topic_id=topic_id,
                current_status_key=status_key,
                updated_at=func.now(),
            ),
        )
        .returning(UserTopic)
    )
    result = await session.execute(stmt)
    topic = result.scalar_one()
    await session.flush()
    return topic


async def update_user_topic_status(
    session: AsyncSession,
    user_id: int,
    status_key: str,
) -> None:
    stmt = (
        update(UserTopic)
        .where(UserTopic.user_id == user_id)
        .values(current_status_key=status_key, updated_at=func.now())
    )
    await session.execute(stmt)
    await session.flush()


async def enqueue_topic_title_sync(session: AsyncSession, user_id: int) -> None:
    """Record a new desired-title revision in the caller's transaction."""
    stmt = (
        update(UserTopic)
        .where(UserTopic.user_id == user_id)
        .values(
            title_sync_version=UserTopic.title_sync_version + 1,
            updated_at=func.now(),
        )
    )
    await session.execute(stmt)
    await session.flush()


async def ensure_topic_title_sync_pending(
    session: AsyncSession, user_id: int
) -> None:
    """Queue a repair only when no newer title revision is already pending."""
    stmt = (
        update(UserTopic)
        .where(
            UserTopic.user_id == user_id,
            UserTopic.title_sync_version == UserTopic.title_applied_version,
        )
        .values(
            title_sync_version=UserTopic.title_sync_version + 1,
            updated_at=func.now(),
        )
    )
    await session.execute(stmt)
    await session.flush()


async def mark_topic_title_externally_drifted(
    session: AsyncSession, topic_id: int
) -> None:
    """Make a human-initiated Telegram rename visible to the sync worker."""
    stmt = (
        update(UserTopic)
        .where(UserTopic.topic_id == topic_id)
        .values(
            title_sync_version=UserTopic.title_sync_version + 1,
            title_force_sync_version=UserTopic.title_sync_version + 1,
            updated_at=func.now(),
        )
    )
    await session.execute(stmt)
    await session.flush()


async def mark_topic_title_sync_applied(
    session: AsyncSession,
    user_id: int,
    applied_version: int,
    status_key: str,
) -> None:
    """Persist the exact revision successfully sent to Telegram.

    A newer revision may have been queued while the API request was in flight;
    in that case ``title_sync_version`` remains ahead and the row stays pending.
    """
    stmt = (
        update(UserTopic)
        .where(
            UserTopic.user_id == user_id,
            UserTopic.title_applied_version < applied_version,
        )
        .values(
            current_status_key=status_key,
            title_applied_version=applied_version,
            updated_at=func.now(),
        )
    )
    await session.execute(stmt)
    await session.flush()


async def delete_user_topic(session: AsyncSession, user_id: int) -> None:
    """Remove the user_topics record so ensure_user_topic will create a fresh forum topic."""
    stmt = delete(UserTopic).where(UserTopic.user_id == user_id)
    await session.execute(stmt)
    await session.flush()


async def save_topic_card_ids(
    session: AsyncSession,
    sub_id: int,
    media_ids: list[int],
    card_id: int,
) -> None:
    stmt = (
        update(Submission)
        .where(Submission.id == sub_id)
        .values(topic_media_message_ids=media_ids, topic_card_message_id=card_id)
    )
    await session.execute(stmt)
    await session.flush()


async def clear_topic_card_ids(session: AsyncSession, sub_id: int) -> None:
    stmt = (
        update(Submission)
        .where(Submission.id == sub_id)
        .values(
            topic_media_message_ids=None,
            topic_card_message_id=None,
            card_rendered_hash=None,
        )
    )
    await session.execute(stmt)
    await session.flush()


async def mark_card_rendered(
    session: AsyncSession, sub_id: int, rendered_hash: str
) -> None:
    """Record the card payload Telegram has confirmed for this submission.

    Written only after a successful send/edit, so a failed call leaves the old
    digest in place and the reconcile pass still sees the card as drifted.
    ``updated_at`` is pinned to its current value: the card digest is delivery
    bookkeeping, not a domain change, and letting the column's ``onupdate``
    bump it would distort reconcile's time window.
    """
    stmt = (
        update(Submission)
        .where(Submission.id == sub_id)
        .values(card_rendered_hash=rendered_hash, updated_at=Submission.updated_at)
    )
    await session.execute(stmt)
    await session.flush()


async def mark_card_rendered_if_unchanged(
    session: AsyncSession, sub_id: int, rendered_hash: str, expected_hash: str | None
) -> bool:
    """Like ``mark_card_rendered``, but only writes if the digest hasn't moved.

    Used by the reconcile pass: the repaint is planned against a hash read
    before the (possibly slow) Telegram call, so another writer (e.g. a lock
    holder repainting the card) may have updated the digest in the meantime.
    Returns ``True`` if the write applied.
    """
    stmt = (
        update(Submission)
        .where(
            Submission.id == sub_id,
            Submission.card_rendered_hash.is_(expected_hash)
            if expected_hash is None
            else Submission.card_rendered_hash == expected_hash,
        )
        .values(card_rendered_hash=rendered_hash, updated_at=Submission.updated_at)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount > 0


async def list_cards_for_reconcile(
    session: AsyncSession, *, stale_terminal_cutoff: datetime
) -> list[Submission]:
    """Return submissions whose topic card may need repainting.

    Active submissions are always in scope. Terminal ones only while recent:
    once finalized they stop changing, so an old terminal card cannot drift and
    scanning the whole archive every few minutes would be wasted work.
    """
    stmt = (
        select(Submission)
        .options(selectinload(Submission.user))
        .where(
            Submission.topic_card_message_id.isnot(None),
            or_(
                Submission.status.notin_(_TERMINAL_STATUSES),
                Submission.updated_at >= stale_terminal_cutoff,
            ),
        )
        .order_by(Submission.updated_at)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
