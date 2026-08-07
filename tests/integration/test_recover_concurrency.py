"""Integration test: concurrent Recover attempts must not double-post cards.

Two simultaneous ``handle_recover`` invocations are serialised by the
module-level guard: only one background run starts, so every submission card
is posted exactly once and no orphaned duplicates appear in the DB.

Requires TEST_DATABASE_URL pointing at a real PostgreSQL database.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import core.messages as msg
from db.models import Submission
from db.queries import create_submission, get_or_create_user
from handlers.moderator import management, recover as recover_mod
from tests.helpers import FakeState, make_bot, make_callback, make_user

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_concurrent_recover_attempts_run_once(
    db_session, integration_engine, monkeypatch,
) -> None:
    user, _ = await get_or_create_user(db_session, 8805, "artist", "Artist")
    for sub_id in (1, 2):
        sub = await create_submission(db_session, user.id, f"Caption {sub_id}", [])
        sub.topic_card_message_id = 9000 + sub_id
    await db_session.commit()

    # Reset the module-level guard so this test uses the current event loop.
    management._recover_lock = None
    management._recover_task = None

    bot = make_bot()
    factory = async_sessionmaker(integration_engine, expire_on_commit=False)
    monkeypatch.setattr(management, "session_factory", factory)
    monkeypatch.setattr(management, "_render_management_message", AsyncMock())
    monkeypatch.setattr(management.admin_notifications, "notify_admins", AsyncMock())
    # Every card is confirmed missing, so the real recover flow reposts each.
    monkeypatch.setattr(recover_mod.topics, "probe_submission_card", AsyncMock(return_value=False))

    real_recover = recover_mod.recover_missing_posts

    async def slow_recover(bot, session_factory):
        await asyncio.sleep(0.1)
        return await real_recover(bot, session_factory)

    monkeypatch.setattr(management, "recover_missing_posts", slow_recover)

    admin = make_user(user_id=1, telegram_id=8805)
    admin.is_admin = True
    state1 = FakeState()
    state2 = FakeState()
    cb1 = make_callback(bot=bot, from_user_id=8805)
    cb2 = make_callback(bot=bot, from_user_id=8805)

    # Barrier: hold the first callback inside the slot check so both callbacks
    # really race for the free slot — sequential awaits would not model it.
    entered = asyncio.Event()
    release = asyncio.Event()
    first_answer = True

    async def gated_answer(*args, **kwargs):
        nonlocal first_answer
        if first_answer:
            first_answer = False
            entered.set()
            await release.wait()
        return None

    cb1.answer = AsyncMock(side_effect=gated_answer)
    cb2.answer = AsyncMock(side_effect=gated_answer)

    gather_task = asyncio.gather(
        management.handle_recover(cb1, AsyncMock(), state1, admin),
        management.handle_recover(cb2, AsyncMock(), state2, admin),
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    release.set()
    await gather_task

    # The second attempt is rejected while the first is still running.
    cb2.answer.assert_awaited_once_with(msg.RECOVER_ALREADY_RUNNING, show_alert=True)
    task = management._recover_task
    assert task is not None
    await asyncio.wait_for(task, timeout=15)
    await asyncio.sleep(0)
    assert management._recover_task is None

    # Exactly one run: both cards were posted exactly once, no orphaned copies.
    async with factory() as session:
        subs = list(
            (await session.execute(select(Submission).order_by(Submission.id))).scalars().all()
        )
    assert len(subs) == 2
    for sub in subs:
        assert sub.topic_card_message_id is not None
        assert sub.topic_media_message_ids == []
