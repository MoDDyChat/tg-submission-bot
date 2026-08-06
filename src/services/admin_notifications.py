"""Admin DM notification service.

Sends DM notifications to all users with ``is_admin=True`` when moderators
perform sensitive operations (preset CRUD, ban/unban).

Usage example::

    await notify_admins(
        bot, session,
        actor=db_user,
        action_text=ADMIN_NOTIFY_USER_UNBANNED.format(
            actor=actor_display(db_user),
            user=user_mention(target),
        ),
    )

All calls are best-effort: ``TelegramForbiddenError`` (user blocked the bot)
and other ``TelegramAPIError`` exceptions are caught and logged, never raised.
"""

from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import config
from core.logging import get_logger
from db.models import User
from db.queries import get_admin_users
from utils.formatting import user_mention

logger = get_logger(__name__)


def actor_display(user: User) -> str:
    """Return an HTML-safe display string for the actor (moderator)."""
    return user_mention(user)


_NOTIFY_MAX_ATTEMPTS = 2


async def notify_admins(
    bot: Bot,
    session: AsyncSession,
    *,
    actor: User,
    action_text: str,
    exclude_actor: bool = True,
) -> None:
    """Send *action_text* as a DM to every admin user.

    Best-effort: ``TelegramForbiddenError`` and other ``TelegramAPIError`` are caught
    and logged. ``TelegramRetryAfter`` is retried once with the requested delay.

    Note: callers that must not block the handler should wrap this call in
    ``asyncio.create_task(notify_admins(...))``.

    Args:
        bot: The Telegram bot instance.
        session: An active async DB session (read-only usage).
        actor: The moderator who performed the action.
        action_text: Pre-formatted HTML notification text.
        exclude_actor: When ``True`` (default), skip sending to *actor* themselves.
    """
    admins = await get_admin_users(session)
    for admin in admins:
        if exclude_actor and admin.telegram_id == actor.telegram_id:
            continue
        for attempt in range(1, _NOTIFY_MAX_ATTEMPTS + 1):
            try:
                await bot.send_message(
                    chat_id=admin.telegram_id,
                    text=action_text,
                    parse_mode="HTML",
                    disable_notification=config.silent_moderator_notifications,
                )
                break
            except TelegramRetryAfter as exc:
                if attempt < _NOTIFY_MAX_ATTEMPTS:
                    logger.warning(
                        "Flood control при уведомлении admin %d, ждём %ds",
                        admin.telegram_id, exc.retry_after,
                    )
                    await asyncio.sleep(exc.retry_after)
                else:
                    logger.warning(
                        "Не удалось отправить DM администратору %d (RetryAfter): %s",
                        admin.telegram_id, exc,
                    )
            except TelegramAPIError as exc:
                logger.warning(
                    "Не удалось отправить DM администратору %d: %s",
                    admin.telegram_id,
                    exc,
                )
                break
