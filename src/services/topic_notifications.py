"""System notification service for moderator group forum topics.

Each public ``notify_*`` function posts a silent system message
(``disable_notification=True``) to the author's forum topic thread.

All calls are best-effort: a ``TelegramAPIError`` is logged and swallowed
so that a notification failure never aborts the main moderation flow.
"""

from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import config
from core.logging import get_logger
from core.messages import (
    TOPIC_CONTACT_FROM_MOD,
    TOPIC_CONTACT_FROM_VIEWER,
    TOPIC_NOTIFY_BANNED,
    TOPIC_NOTIFY_CAPTION_CHANGED,
    TOPIC_NOTIFY_DIRECT_FROM_MODERATOR,
    TOPIC_NOTIFY_DIRECT_FROM_VIEWER,
    TOPIC_NOTIFY_MEDIA_CHANGED,
    TOPIC_NOTIFY_PUBLISHED_BY_MOD,
    TOPIC_NOTIFY_PUBLISHED_SCHEDULED,
    TOPIC_NOTIFY_REJECTED,
    TOPIC_NOTIFY_REJECTED_SILENT,
    TOPIC_NOTIFY_RESCHEDULED,
    TOPIC_NOTIFY_SCHEDULED,
    TOPIC_NOTIFY_TAGS_CHANGED,
    TOPIC_NOTIFY_UNBANNED,
    TOPIC_NOTIFY_UNSCHEDULED,
    TOPIC_NOTIFY_VIEWER_CANCELLED,
)
from db.models import Submission, User
from db.queries import get_user_topic
from utils.diffs import caption_diff, tags_diff
from utils.formatting import user_mention

logger = get_logger(__name__)


# ── Internal helpers ───────────────────────────────────────────────

_mod_display = user_mention
_user_display = user_mention


async def _get_topic_id(session: AsyncSession, sub: Submission) -> int | None:
    """Return the forum topic_id for the submission's author, or None.

    Logs a warning and returns None when no UserTopic row exists so that
    callers can skip the notification gracefully.
    """
    topic = await get_user_topic(session, sub.user_id)
    if topic is None:
        logger.warning(
            "UserTopic не найден для user_id=%d (sub #%d) — уведомление пропущено",
            sub.user_id,
            sub.id,
        )
        return None
    return topic.topic_id


async def _send(bot: Bot, topic_id: int, text: str) -> None:
    """Send a silent system message to a forum topic. Best-effort."""
    try:
        await bot.send_message(
            chat_id=config.moderator_group_id,
            message_thread_id=topic_id,
            text=text,
            parse_mode="HTML",
            disable_notification=True,
        )
    except TelegramAPIError as exc:
        logger.warning("Не удалось отправить уведомление в тему %d: %s", topic_id, exc)


def _format_time(dt: datetime) -> str:
    """Format a UTC datetime as a localised time string."""
    tz = ZoneInfo(config.timezone)
    return dt.astimezone(tz).strftime("%d.%m.%Y %H:%M")


# ── Public notification functions ──────────────────────────────────

async def notify_caption_changed(
    bot: Bot,
    session: AsyncSession,
    sub: Submission,
    moderator: User,
    old_caption: str | None,
    new_caption: str | None,
) -> None:
    topic_id = await _get_topic_id(session, sub)
    if topic_id is None:
        return
    diff = caption_diff(old_caption, new_caption)
    text = TOPIC_NOTIFY_CAPTION_CHANGED.format(
        mod=_mod_display(moderator), sub_id=sub.id, diff=diff
    )
    await _send(bot, topic_id, text)


async def notify_media_changed(
    bot: Bot,
    session: AsyncSession,
    sub: Submission,
    moderator: User,
) -> None:
    topic_id = await _get_topic_id(session, sub)
    if topic_id is None:
        return
    text = TOPIC_NOTIFY_MEDIA_CHANGED.format(mod=_mod_display(moderator), sub_id=sub.id)
    await _send(bot, topic_id, text)


async def notify_tags_changed(
    bot: Bot,
    session: AsyncSession,
    sub: Submission,
    moderator: User,
    old_tags: list[str] | None,
    new_tags: list[str] | None,
) -> None:
    topic_id = await _get_topic_id(session, sub)
    if topic_id is None:
        return
    diff = tags_diff(old_tags, new_tags)
    text = TOPIC_NOTIFY_TAGS_CHANGED.format(
        mod=_mod_display(moderator), sub_id=sub.id, diff=diff
    )
    await _send(bot, topic_id, text)


async def notify_scheduled(
    bot: Bot,
    session: AsyncSession,
    sub: Submission,
    moderator: User,
    publish_at_utc: datetime,
) -> None:
    topic_id = await _get_topic_id(session, sub)
    if topic_id is None:
        return
    text = TOPIC_NOTIFY_SCHEDULED.format(
        mod=_mod_display(moderator),
        sub_id=sub.id,
        time=_format_time(publish_at_utc),
    )
    await _send(bot, topic_id, text)


async def notify_rescheduled(
    bot: Bot,
    session: AsyncSession,
    sub: Submission,
    moderator: User,
    publish_at_utc: datetime,
) -> None:
    topic_id = await _get_topic_id(session, sub)
    if topic_id is None:
        return
    text = TOPIC_NOTIFY_RESCHEDULED.format(
        mod=_mod_display(moderator),
        sub_id=sub.id,
        time=_format_time(publish_at_utc),
    )
    await _send(bot, topic_id, text)


async def notify_unscheduled(
    bot: Bot,
    session: AsyncSession,
    sub: Submission,
    moderator: User,
) -> None:
    topic_id = await _get_topic_id(session, sub)
    if topic_id is None:
        return
    text = TOPIC_NOTIFY_UNSCHEDULED.format(mod=_mod_display(moderator), sub_id=sub.id)
    await _send(bot, topic_id, text)


async def notify_published(
    bot: Bot,
    session: AsyncSession,
    sub: Submission,
    *,
    by_moderator: User | None = None,
) -> None:
    topic_id = await _get_topic_id(session, sub)
    if topic_id is None:
        return
    if by_moderator is not None:
        text = TOPIC_NOTIFY_PUBLISHED_BY_MOD.format(
            mod=_mod_display(by_moderator), sub_id=sub.id
        )
    else:
        text = TOPIC_NOTIFY_PUBLISHED_SCHEDULED.format(sub_id=sub.id)
    await _send(bot, topic_id, text)


async def notify_rejected(
    bot: Bot,
    session: AsyncSession,
    sub: Submission,
    moderator: User,
    *,
    reason: str | None,
    silent: bool,
) -> None:
    topic_id = await _get_topic_id(session, sub)
    if topic_id is None:
        return
    if silent or not reason:
        text = TOPIC_NOTIFY_REJECTED_SILENT.format(mod=_mod_display(moderator), sub_id=sub.id)
    else:
        text = TOPIC_NOTIFY_REJECTED.format(
            mod=_mod_display(moderator),
            sub_id=sub.id,
            reason=html.escape(reason),
        )
    await _send(bot, topic_id, text)


async def notify_banned(
    bot: Bot,
    session: AsyncSession,
    sub: Submission,
    moderator: User,
    reason: str,
) -> None:
    topic_id = await _get_topic_id(session, sub)
    if topic_id is None:
        return
    text = TOPIC_NOTIFY_BANNED.format(
        mod=_mod_display(moderator),
        sub_id=sub.id,
        reason=html.escape(reason),
    )
    await _send(bot, topic_id, text)


async def notify_unbanned(
    bot: Bot,
    session: AsyncSession,
    unbanned_user: User,
    moderator: User,
) -> None:
    """Notify about an unban. Sent to the user's forum topic, if any."""
    topic = await get_user_topic(session, unbanned_user.id)
    if topic is None:
        logger.debug("notify_unbanned: нет темы для user_id=%d, уведомление пропущено", unbanned_user.id)
        return

    text = TOPIC_NOTIFY_UNBANNED.format(
        mod=_mod_display(moderator),
        user=_user_display(unbanned_user),
    )
    await _send(bot, topic.topic_id, text)


async def notify_viewer_cancelled(
    bot: Bot,
    session: AsyncSession,
    sub: Submission,
) -> None:
    topic_id = await _get_topic_id(session, sub)
    if topic_id is None:
        return
    text = TOPIC_NOTIFY_VIEWER_CANCELLED.format(sub_id=sub.id)
    await _send(bot, topic_id, text)


async def notify_contact_from_moderator(
    bot: Bot,
    session: AsyncSession,
    sub: Submission,
    moderator: User,
    text: str,
) -> None:
    topic_id = await _get_topic_id(session, sub)
    if topic_id is None:
        return
    msg = TOPIC_CONTACT_FROM_MOD.format(
        mod=_mod_display(moderator),
        text=html.escape(text),
    )
    await _send(bot, topic_id, msg)


async def notify_contact_from_viewer(
    bot: Bot,
    session: AsyncSession,
    sub: Submission,
    text: str,
) -> None:
    topic_id = await _get_topic_id(session, sub)
    if topic_id is None:
        return
    msg = TOPIC_CONTACT_FROM_VIEWER.format(text=html.escape(text))
    await _send(bot, topic_id, msg)


async def notify_direct_from_moderator(
    bot: Bot,
    session: AsyncSession,
    user: User,
    moderator: User,
    text: str,
) -> None:
    """Notify the author's forum topic about a direct (submission-less) moderator message."""
    topic = await get_user_topic(session, user.id)
    if topic is None:
        return
    msg = TOPIC_NOTIFY_DIRECT_FROM_MODERATOR.format(mod=_mod_display(moderator), text=text)
    await _send(bot, topic.topic_id, msg)


async def notify_direct_from_viewer(
    bot: Bot,
    session: AsyncSession,
    user: User,
    text: str,
) -> None:
    """Notify the author's forum topic about their reply on the direct channel."""
    topic = await get_user_topic(session, user.id)
    if topic is None:
        return
    msg = TOPIC_NOTIFY_DIRECT_FROM_VIEWER.format(text=text)
    await _send(bot, topic.topic_id, msg)
