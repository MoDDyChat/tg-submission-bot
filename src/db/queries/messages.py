"""Message DB queries (moderator ↔ viewer correspondence)."""

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Message


async def create_message(
    session: AsyncSession,
    submission_id: int,
    sender_telegram_id: int,
    text: str,
) -> Message:
    msg = Message(
        submission_id=submission_id,
        sender_telegram_id=sender_telegram_id,
        text=text,
    )
    session.add(msg)
    await session.flush()
    return msg
