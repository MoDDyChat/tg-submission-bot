"""Integration tests for the grant/ban row-lock serialisation.

A parallel ``grant_moderator`` and ``ban_user`` on the same user must not both
succeed: the row lock (``SELECT ... FOR UPDATE``) plus re-check makes one of
them fail, so the final state never has ``is_moderator=True`` alongside
``is_banned=True``.

Requires TEST_DATABASE_URL pointing at a real PostgreSQL database.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.exceptions import CannotBanModeratorError, RoleTargetBannedError
from db.models import User
from db.queries import ban_user, get_or_create_user
from services import roles

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_parallel_grant_and_ban_cannot_both_succeed(
    db_session, integration_engine,
) -> None:
    actor, _ = await get_or_create_user(db_session, 8801, "chief", "Chief")
    target, _ = await get_or_create_user(db_session, 8802, "artist", "Artist")
    await db_session.commit()

    factory = async_sessionmaker(integration_engine, expire_on_commit=False)

    async def do_grant() -> None:
        async with factory() as session:
            t = await session.get(User, target.id)
            try:
                await roles.grant_moderator(session, AsyncMock(), actor=actor, target=t)
            except Exception:
                await session.rollback()
                raise
            await session.commit()

    async def do_ban() -> None:
        async with factory() as session:
            try:
                await ban_user(session, target.id, "spam")
            except Exception:
                await session.rollback()
                raise
            await session.commit()

    grant_result, ban_result = await asyncio.gather(
        do_grant(), do_ban(), return_exceptions=True,
    )

    # Row-lock serialisation: exactly one operation wins, the other fails.
    assert isinstance(grant_result, Exception) != isinstance(ban_result, Exception)
    failed = grant_result if isinstance(grant_result, Exception) else ban_result
    assert isinstance(failed, (CannotBanModeratorError, RoleTargetBannedError))

    await db_session.refresh(target)
    assert not (target.is_moderator and target.is_banned)
