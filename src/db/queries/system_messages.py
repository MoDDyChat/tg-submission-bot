"""System message DB queries (idempotent bot-managed messages: legend, queue, etc.)."""

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import SystemMessage


async def get_system_message(
    session: AsyncSession,
    key: str,
) -> SystemMessage | None:
    stmt = select(SystemMessage).where(SystemMessage.key == key)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_system_message(
    session: AsyncSession,
    key: str,
    chat_id: int,
    message_id: int,
    payload: dict | None = None,
) -> SystemMessage:
    stmt = (
        pg_insert(SystemMessage)
        .values(key=key, chat_id=chat_id, message_id=message_id, payload=payload)
        .on_conflict_do_update(
            index_elements=[SystemMessage.key],
            set_=dict(
                chat_id=chat_id,
                message_id=message_id,
                payload=payload,
                updated_at=func.now(),
            ),
        )
        .returning(SystemMessage)
    )
    result = await session.execute(stmt)
    row = result.scalar_one()
    await session.flush()
    return row


async def delete_system_message(session: AsyncSession, key: str) -> None:
    stmt = delete(SystemMessage).where(SystemMessage.key == key)
    await session.execute(stmt)
    await session.flush()


async def list_system_messages_by_prefix(
    session: AsyncSession,
    prefix: str,
) -> list[SystemMessage]:
    stmt = (
        select(SystemMessage)
        .where(SystemMessage.key.like(f"{prefix}%"))
        .order_by(SystemMessage.key)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
