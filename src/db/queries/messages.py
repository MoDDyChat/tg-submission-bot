"""Message DB queries (moderator ↔ viewer correspondence)."""

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Message


async def create_message(
    session: AsyncSession,
    submission_id: int | None,
    sender_telegram_id: int,
    text: str,
    target_user_id: int | None = None,
) -> Message:
    """Persist one conversation entry.

    ``submission_id`` is set for topic conversations; direct correspondence
    passes ``None`` and identifies the viewer thread via ``target_user_id``
    (internal ``users.id``) on both sides.
    """
    msg = Message(
        submission_id=submission_id,
        sender_telegram_id=sender_telegram_id,
        text=text,
        target_user_id=target_user_id,
    )
    session.add(msg)
    await session.flush()
    return msg
