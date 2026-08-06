"""Integration tests for db/queries/topics.py (user_topics upsert, CRUD).

Requires TEST_DATABASE_URL pointing at a real PostgreSQL database.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from db.queries.topics import (
    delete_user_topic,
    enqueue_topic_title_sync,
    ensure_topic_title_sync_pending,
    get_user_topic,
    mark_topic_title_sync_applied,
    update_user_topic_status,
    upsert_user_topic,
)
from db.queries.users import get_or_create_user

pytestmark = pytest.mark.integration


async def _make_user(session: AsyncSession, telegram_id: int) -> int:
    user, _ = await get_or_create_user(session, telegram_id, username=None, full_name="Test User")
    await session.flush()
    return user.id


# ── upsert creates / updates ──────────────────────────────────────────

async def test_upsert_creates_topic_for_user(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session, telegram_id=1001)

    topic = await upsert_user_topic(db_session, user_id, topic_id=10, status_key="idle")
    await db_session.commit()

    assert topic.user_id == user_id
    assert topic.topic_id == 10
    assert topic.current_status_key == "idle"


async def test_upsert_updates_existing_topic(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session, telegram_id=1002)

    await upsert_user_topic(db_session, user_id, topic_id=20, status_key="idle")
    await db_session.commit()

    updated = await upsert_user_topic(db_session, user_id, topic_id=21, status_key="pending")
    await db_session.commit()

    assert updated.topic_id == 21
    assert updated.current_status_key == "pending"


async def test_upsert_is_idempotent(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session, telegram_id=1003)

    t1 = await upsert_user_topic(db_session, user_id, topic_id=30, status_key="idle")
    await db_session.commit()
    t2 = await upsert_user_topic(db_session, user_id, topic_id=30, status_key="idle")
    await db_session.commit()

    assert t1.user_id == t2.user_id


# ── get_user_topic ────────────────────────────────────────────────────

async def test_get_user_topic_returns_record(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session, telegram_id=1004)
    await upsert_user_topic(db_session, user_id, topic_id=40, status_key="idle")
    await db_session.commit()

    result = await get_user_topic(db_session, user_id)
    assert result is not None
    assert result.topic_id == 40


async def test_get_user_topic_returns_none_for_unknown_user(db_session: AsyncSession) -> None:
    result = await get_user_topic(db_session, user_id=99999)
    assert result is None


# ── update_user_topic_status ─────────────────────────────────────────

async def test_update_status_changes_status_key(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session, telegram_id=1005)
    await upsert_user_topic(db_session, user_id, topic_id=50, status_key="idle")
    await db_session.commit()

    await update_user_topic_status(db_session, user_id, status_key="editing")
    await db_session.commit()

    result = await get_user_topic(db_session, user_id)
    assert result is not None
    assert result.current_status_key == "editing"


# ── durable title sync revisions ────────────────────────────────────

async def test_title_sync_revision_survives_until_applied(
    db_session: AsyncSession,
) -> None:
    user_id = await _make_user(db_session, telegram_id=1007)
    await upsert_user_topic(
        db_session, user_id, topic_id=70, status_key="pending"
    )
    await enqueue_topic_title_sync(db_session, user_id)
    await db_session.commit()

    pending = await get_user_topic(db_session, user_id)
    assert pending is not None
    assert pending.title_sync_version == 1
    assert pending.title_applied_version == 0

    await mark_topic_title_sync_applied(
        db_session, user_id, applied_version=1, status_key="scheduled"
    )
    await db_session.commit()

    applied = await get_user_topic(db_session, user_id)
    assert applied is not None
    assert applied.current_status_key == "scheduled"
    assert applied.title_sync_version == 1
    assert applied.title_applied_version == 1


async def test_reconcile_does_not_duplicate_existing_pending_revision(
    db_session: AsyncSession,
) -> None:
    user_id = await _make_user(db_session, telegram_id=1008)
    await upsert_user_topic(
        db_session, user_id, topic_id=80, status_key="pending"
    )
    await enqueue_topic_title_sync(db_session, user_id)
    await ensure_topic_title_sync_pending(db_session, user_id)
    await db_session.commit()

    topic = await get_user_topic(db_session, user_id)
    assert topic is not None
    assert topic.title_sync_version == 1


# ── delete ────────────────────────────────────────────────────────────

async def test_delete_removes_record(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session, telegram_id=1006)
    await upsert_user_topic(db_session, user_id, topic_id=60, status_key="idle")
    await db_session.commit()

    await delete_user_topic(db_session, user_id)
    await db_session.commit()

    result = await get_user_topic(db_session, user_id)
    assert result is None


async def test_delete_is_safe_for_nonexistent_user(db_session: AsyncSession) -> None:
    # Should not raise
    await delete_user_topic(db_session, user_id=88888)
    await db_session.commit()
