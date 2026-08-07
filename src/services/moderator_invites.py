"""Moderator invite links — issue and one-shot redemption."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy import delete, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging import get_logger
from db.models import ModeratorInvite, User
from db.queries import get_user_by_id
from services import roles

logger = get_logger(__name__)

# Deep-link payload prefix; the full payload (prefix + token) must stay within
# Telegram's 64-char limit with only A-Za-z0-9_- characters.
INVITE_PREFIX = "modinvite_"


def _expires_at(ttl_hours: int) -> datetime:
    return datetime.now(tz=timezone.utc) + timedelta(hours=ttl_hours)


async def create_invite(
    session: AsyncSession,
    *,
    created_by: User,
    ttl_hours: int = 24,
) -> ModeratorInvite:
    """Create a one-shot moderator invite row. The caller owns the commit."""
    invite = ModeratorInvite(
        token=secrets.token_urlsafe(24),
        created_by=created_by.id,
        expires_at=_expires_at(ttl_hours),
    )
    session.add(invite)
    return invite


def build_invite_link(token: str, bot_username: str) -> str:
    """Return the deep-link that redeems *token* on /start."""
    return f"https://t.me/{bot_username}?start={INVITE_PREFIX}{token}"


async def redeem_invite(
    session: AsyncSession,
    bot: Bot,
    *,
    token: str,
    user: User,
) -> bool:
    """Atomically redeem a one-shot invite and grant moderator on success.

    The UPDATE itself resolves the race of two simultaneous redeems: only one
    of them sees a row with ``used_at IS NULL``. Role flags are mutated in the
    same transaction and published by the single commit; notifications run
    only after that commit.
    """
    result = await session.execute(
        text(
            "UPDATE moderator_invites SET used_at = NOW(), used_by = :user_id "
            "WHERE token = :token AND used_at IS NULL AND expires_at > NOW() "
            "RETURNING token, created_by"
        ),
        {"user_id": user.id, "token": token},
    )
    row = result.first()
    if row is None:
        # Token expired, already used, or never issued — no side effects.
        return False

    creator = await get_user_by_id(session, row.created_by)
    if creator is None:
        await session.rollback()
        return False

    try:
        # Actor is the one who issued the link — grant_moderator forbids
        # actor.id == target.id.
        await roles.grant_moderator(session, bot, actor=creator, target=user)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    await roles.notify_role_change(
        bot, session, actor=creator, target=user, action="moderator_granted",
    )
    return True


async def cleanup_expired_invites(session: AsyncSession) -> int:
    """Delete expired invites and return how many were removed."""
    result = await session.execute(
        delete(ModeratorInvite).where(ModeratorInvite.expires_at < func.now())
    )
    removed = result.rowcount or 0
    if removed:
        logger.info("Удалено протухших инвайтов модератора: %d", removed)
    return removed
