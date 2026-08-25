"""Integration tests for atomic check-and-set transitions.

Every race runs two independent sessions against a real PostgreSQL so the
guards in the WHERE clause decide the winner: exactly one side must observe
success, the loser gets ``False`` and the row ends up in a consistent state.

Requires TEST_DATABASE_URL pointing at a real PostgreSQL database.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from db.models import SubmissionMedia
from db.queries import (
    add_media,
    ban_user,
    create_publication,
    create_submission,
    delete_media_unless_last,
    get_or_create_user,
    reschedule_publication,
    transition_submission_status,
    unban_user,
)

pytestmark = pytest.mark.integration


def _exactly_one_true(results: list[object]) -> bool:
    """True iff both sides returned plain bools and exactly one was ``True``."""
    return (
        all(isinstance(r, bool) for r in results)
        and sum(1 for r in results if r is True) == 1
    )


@pytest.mark.asyncio
async def test_parallel_transition_exactly_one_wins(
    db_session, integration_engine,
) -> None:
    user, _ = await get_or_create_user(db_session, 8901, "author", "Author")
    sub = await create_submission(db_session, user.id, "caption")
    await db_session.commit()

    factory = async_sessionmaker(integration_engine, expire_on_commit=False)

    async def cancel() -> bool:
        async with factory() as session:
            won = await transition_submission_status(
                session, sub.id, "cancelled", expected={"pending"},
            )
            await session.commit()
            return won

    results = await asyncio.gather(cancel(), cancel(), return_exceptions=True)

    assert _exactly_one_true(results)

    await db_session.refresh(sub)
    assert sub.status == "cancelled"


@pytest.mark.asyncio
async def test_transition_from_unexpected_status_is_noop(
    db_session, integration_engine,
) -> None:
    user, _ = await get_or_create_user(db_session, 8902, "author", "Author")
    sub = await create_submission(db_session, user.id, "caption")
    await db_session.commit()

    factory = async_sessionmaker(integration_engine, expire_on_commit=False)
    async with factory() as session:
        moved = await transition_submission_status(
            session, sub.id, "published", expected={"scheduled"},
        )
        await session.commit()

    assert moved is False
    await db_session.refresh(sub)
    assert sub.status == "pending"


@pytest.mark.asyncio
async def test_parallel_ban_and_unban_are_idempotent(
    db_session, integration_engine,
) -> None:
    target, _ = await get_or_create_user(db_session, 8903, "spammer", "Spammer")
    await db_session.commit()

    factory = async_sessionmaker(integration_engine, expire_on_commit=False)

    async def ban() -> bool:
        async with factory() as session:
            won = await ban_user(session, target.id, "spam")
            await session.commit()
            return won

    results = await asyncio.gather(ban(), ban(), return_exceptions=True)
    assert _exactly_one_true(results)

    await db_session.refresh(target)
    assert target.is_banned is True

    async def unban() -> bool:
        async with factory() as session:
            won = await unban_user(session, target.id)
            await session.commit()
            return won

    results = await asyncio.gather(unban(), unban(), return_exceptions=True)
    assert _exactly_one_true(results)

    await db_session.refresh(target)
    assert target.is_banned is False


@pytest.mark.asyncio
async def test_parallel_delete_never_empties_submission(
    db_session, integration_engine,
) -> None:
    user, _ = await get_or_create_user(db_session, 8904, "author", "Author")
    sub = await create_submission(db_session, user.id, "photo dump")
    first = await add_media(db_session, sub.id, "file-1", "uniq-1", "photo")
    second = await add_media(db_session, sub.id, "file-2", "uniq-2", "photo")
    await db_session.commit()

    factory = async_sessionmaker(integration_engine, expire_on_commit=False)

    async def drop(media_id: int) -> bool:
        async with factory() as session:
            won = await delete_media_unless_last(session, media_id, sub.id)
            await session.commit()
            return won

    results = await asyncio.gather(
        drop(first.id), drop(second.id), return_exceptions=True,
    )
    assert _exactly_one_true(results)

    remaining = await db_session.scalars(
        select(SubmissionMedia).where(SubmissionMedia.submission_id == sub.id)
    )
    ids = [m.id for m in remaining]
    assert len(ids) == 1
    assert ids[0] in (first.id, second.id)


@pytest.mark.asyncio
async def test_parallel_reschedule_exactly_one_wins(
    db_session, integration_engine,
) -> None:
    user, _ = await get_or_create_user(db_session, 8905, "author", "Author")
    sub = await create_submission(db_session, user.id, "caption")
    old_at = datetime(2027, 1, 15, 12, 0, tzinfo=timezone.utc)
    new_at = datetime(2027, 1, 15, 18, 0, tzinfo=timezone.utc)
    pub = await create_publication(db_session, sub.id, "caption", old_at)
    await db_session.commit()

    factory = async_sessionmaker(integration_engine, expire_on_commit=False)

    async def move() -> bool:
        async with factory() as session:
            won = await reschedule_publication(
                session, pub.id, old_publish_at=old_at, new_publish_at=new_at,
            )
            await session.commit()
            return won

    results = await asyncio.gather(move(), move(), return_exceptions=True)
    assert _exactly_one_true(results)

    await db_session.refresh(pub)
    assert pub.publish_at == new_at
