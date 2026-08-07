"""Runtime role management — invariants, side effects, audit notifications.

The four mutation functions only change role flags (and burn the target's
unused invites) inside the caller's transaction: they never commit and never
touch Telegram. The caller commits, then sends notifications via
``notify_role_change`` and repaints the released submission cards via
``repaint_released_cards``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import core.messages as msg
from core.config import config
from core.exceptions import (
    CannotChangeOwnRoleError,
    CannotRemoveLastAdminError,
    ConfigProtectedRoleError,
    RoleAlreadyGrantedError,
    RoleTargetBannedError,
)
from core.logging import get_logger
from db.models import EditLock, User
from db.queries import count_admins, set_roles
from services import admin_notifications
from services.edit_lock import release_moderator_locks
from utils.formatting import user_mention

logger = get_logger(__name__)

# Advisory-lock key serialising concurrent admin mutations so the
# "last admin" count cannot race two revocations into zero admins.
_ADMIN_MUTATION_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('roles_admin_mutation'))"

_ACTION_TEXTS: dict[str, str] = {
    "moderator_granted": msg.ADMIN_NOTIFY_MODERATOR_ADDED,
    "moderator_removed": msg.ADMIN_NOTIFY_MODERATOR_REMOVED,
    "admin_granted": msg.ADMIN_NOTIFY_ADMIN_GRANTED,
    "admin_revoked": msg.ADMIN_NOTIFY_ADMIN_REVOKED,
}

_TARGET_TEXTS: dict[str, str] = {
    "moderator_granted": msg.MODERATOR_INVITE_WELCOME,
    "moderator_removed": msg.MODERATOR_ROLE_REMOVED_NOTICE,
    "admin_granted": msg.MODERATOR_ADMIN_GRANTED_NOTICE,
    "admin_revoked": msg.MODERATOR_ADMIN_REVOKED_NOTICE,
}


def is_moderator_protected(user: User) -> bool:
    """True when the user holds moderator via MODERATOR_IDS (break-glass)."""
    return user.telegram_id in config.moderator_ids


def is_admin_protected(user: User) -> bool:
    """True when the user holds admin via ADMIN_IDS (break-glass)."""
    return user.telegram_id in config.admin_ids


def _ensure_not_self(actor: User, target: User) -> None:
    if actor.id == target.id:
        raise CannotChangeOwnRoleError()


def _ensure_not_banned(target: User) -> None:
    if target.is_banned:
        raise RoleTargetBannedError()


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _invalidate_invites(session: AsyncSession, target_id: int) -> None:
    """Burn every unused invite issued by *target_id* in the same transaction."""
    await session.execute(
        text(
            "UPDATE moderator_invites SET used_at = NOW() "
            "WHERE created_by = :target_id AND used_at IS NULL"
        ),
        {"target_id": target_id},
    )


async def _serialize_admin_mutation(session: AsyncSession) -> None:
    await session.execute(text(_ADMIN_MUTATION_LOCK_SQL))


async def _lock_target_row(session: AsyncSession, target: User) -> None:
    """Re-read *target* from the DB under a row lock.

    Serialises concurrent grant/ban mutations on the same user: after the
    lock is taken the caller's flag checks see the latest committed state.
    """
    await session.refresh(target, with_for_update=True)


async def grant_moderator(session: AsyncSession, bot: Bot, *, actor: User, target: User) -> None:
    _ensure_not_self(actor, target)
    await _lock_target_row(session, target)
    _ensure_not_banned(target)
    if target.is_moderator:
        raise RoleAlreadyGrantedError()
    await set_roles(
        session,
        target.id,
        is_moderator=True,
        is_admin=target.is_admin,
        granted_by=actor.id,
        granted_at=_now(),
    )


async def remove_moderator(
    session: AsyncSession,
    bot: Bot,
    *,
    actor: User,
    target: User,
) -> list[EditLock]:
    """Demote *target* to a plain user and release their edit locks.

    Returns the released locks so the caller can repaint the affected
    submission cards after committing.
    """
    _ensure_not_self(actor, target)
    if is_moderator_protected(target):
        raise ConfigProtectedRoleError()
    await set_roles(
        session,
        target.id,
        is_moderator=False,
        is_admin=False,
        granted_by=None,
        granted_at=None,
    )
    await _invalidate_invites(session, target.id)
    return await release_moderator_locks(
        session, user_id=target.id, telegram_id=target.telegram_id,
    )


async def grant_admin(session: AsyncSession, bot: Bot, *, actor: User, target: User) -> None:
    _ensure_not_self(actor, target)
    await _lock_target_row(session, target)
    _ensure_not_banned(target)
    if target.is_admin:
        raise RoleAlreadyGrantedError()
    await set_roles(
        session,
        target.id,
        is_moderator=True,
        is_admin=True,
        granted_by=actor.id,
        granted_at=_now(),
    )


async def revoke_admin(session: AsyncSession, bot: Bot, *, actor: User, target: User) -> None:
    _ensure_not_self(actor, target)
    if is_admin_protected(target):
        raise ConfigProtectedRoleError()
    await _serialize_admin_mutation(session)
    if await count_admins(session) <= 1:
        raise CannotRemoveLastAdminError()
    await set_roles(
        session,
        target.id,
        is_moderator=True,
        is_admin=False,
        granted_by=actor.id,
        granted_at=_now(),
    )
    await _invalidate_invites(session, target.id)


async def notify_role_change(
    bot: Bot,
    session: AsyncSession,
    *,
    actor: User,
    target: User,
    action: str,
) -> None:
    """DM every admin and best-effort DM the target about a role change.

    Must be called by the caller after its own commit — never from inside an
    open write transaction. The target DM is best-effort: ``TelegramAPIError``
    (including network errors and a blocked bot) is swallowed and logged.

    The target is excluded from the admin broadcast even when they are an admin
    themselves: they get the second-person notice below instead of the
    third-person audit line, so they are notified exactly once either way.
    """
    action_text = _ACTION_TEXTS[action].format(
        actor=admin_notifications.actor_display(actor),
        user=user_mention(target),
    )
    await admin_notifications.notify_admins(
        bot,
        session,
        actor=actor,
        action_text=action_text,
        exclude_telegram_ids={target.telegram_id},
    )

    try:
        await bot.send_message(
            chat_id=target.telegram_id,
            text=_TARGET_TEXTS[action].format(user=user_mention(target)),
            parse_mode="HTML",
            disable_notification=config.silent_moderator_notifications,
        )
    except TelegramAPIError as exc:
        logger.warning(
            "Не удалось отправить DM о смене роли пользователю %d: %s",
            target.telegram_id,
            exc,
        )


async def repaint_released_cards(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    locks: list[EditLock],
) -> None:
    """Repaint forum cards whose edit locks were force-released.

    Must run after the role-change transaction is committed: each card gets
    its own short session so the lock indicator reads the committed state.
    No notifications are sent.
    """
    from db.queries import get_submission_with_user
    from services import topics

    for lock in locks:
        if lock.resource_type != "submission":
            continue
        try:
            sub_id = int(lock.resource_id)
        except ValueError:
            continue
        async with session_factory() as session:
            sub = await get_submission_with_user(session, sub_id)
            if sub is not None:
                await topics.update_submission_card(bot, session, sub)
                await topics.request_topic_title_sync(session, sub.user.id)
            await session.commit()
