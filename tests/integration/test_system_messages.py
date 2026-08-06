"""Integration tests for system_messages DB queries.

Requires TEST_DATABASE_URL pointing at a real PostgreSQL database.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from db.queries.system_messages import (
    delete_system_message,
    get_system_message,
    list_system_messages_by_prefix,
    upsert_system_message,
)

pytestmark = pytest.mark.integration


async def test_upsert_creates_new_record(db_session: AsyncSession) -> None:
    row = await upsert_system_message(db_session, "test:key1", chat_id=-100, message_id=1)
    await db_session.commit()

    assert row.key == "test:key1"
    assert row.chat_id == -100
    assert row.message_id == 1


async def test_upsert_is_idempotent_updates_existing(db_session: AsyncSession) -> None:
    await upsert_system_message(db_session, "test:key2", chat_id=-100, message_id=10)
    await db_session.commit()

    updated = await upsert_system_message(db_session, "test:key2", chat_id=-100, message_id=20)
    await db_session.commit()

    assert updated.message_id == 20

    fetched = await get_system_message(db_session, "test:key2")
    assert fetched is not None
    assert fetched.message_id == 20


async def test_upsert_stores_payload(db_session: AsyncSession) -> None:
    payload = {"md5": "abc123", "count": 5}
    await upsert_system_message(db_session, "test:key3", chat_id=-100, message_id=99, payload=payload)
    await db_session.commit()

    fetched = await get_system_message(db_session, "test:key3")
    assert fetched is not None
    assert fetched.payload == payload


async def test_get_returns_none_for_missing_key(db_session: AsyncSession) -> None:
    result = await get_system_message(db_session, "test:nonexistent")
    assert result is None


async def test_delete_removes_record(db_session: AsyncSession) -> None:
    await upsert_system_message(db_session, "test:del1", chat_id=-100, message_id=5)
    await db_session.commit()

    await delete_system_message(db_session, "test:del1")
    await db_session.commit()

    result = await get_system_message(db_session, "test:del1")
    assert result is None


async def test_delete_is_safe_for_missing_key(db_session: AsyncSession) -> None:
    # should not raise
    await delete_system_message(db_session, "test:never_existed")
    await db_session.commit()


async def test_list_by_prefix_returns_ordered_rows(db_session: AsyncSession) -> None:
    await upsert_system_message(db_session, "queue:01", chat_id=-100, message_id=11)
    await upsert_system_message(db_session, "queue:00", chat_id=-100, message_id=10)
    await upsert_system_message(db_session, "other:01", chat_id=-100, message_id=99)
    await db_session.commit()

    rows = await list_system_messages_by_prefix(db_session, "queue:")
    keys = [r.key for r in rows]
    assert keys == ["queue:00", "queue:01"]
    assert all(r.key.startswith("queue:") for r in rows)


async def test_upsert_payload_update_replaces_old_payload(db_session: AsyncSession) -> None:
    await upsert_system_message(
        db_session, "test:payload_update", chat_id=-100, message_id=1, payload={"old": True}
    )
    await db_session.commit()

    await upsert_system_message(
        db_session, "test:payload_update", chat_id=-100, message_id=1, payload={"new": True}
    )
    await db_session.commit()

    fetched = await get_system_message(db_session, "test:payload_update")
    assert fetched is not None
    assert fetched.payload == {"new": True}
