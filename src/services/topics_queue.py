"""Queue board service: builds and maintains the submission queue in the General topic.

The queue is rendered as one or more pinned messages in the moderator group's General
topic, each message holding at most ``_QUEUE_MAX_LINES_PER_CHUNK`` entries.

Message IDs are tracked in ``system_messages`` under the prefix ``general:queue:``.
A checksum stored in ``payload`` enables skip-if-unchanged idempotency.

IMPORTANT: This service is designed for **single-process** bots only.
The ``_render_lock`` is a process-local asyncio.Lock and will not prevent
concurrent rendering across multiple processes.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.config import config
from core.logging import get_logger
from core.topic_status_config import get_style
from db.models import Publication, Submission, UserTopic
from db.queries import (
    delete_system_message,
    get_system_message,
    list_system_messages_by_prefix,
    upsert_system_message,
)

logger = get_logger(__name__)

_QUEUE_MAX_LINES_PER_CHUNK = 30
_QUEUE_KEY_PREFIX = "general:queue:"
_render_lock = asyncio.Lock()

_SCHEDULE_KEY = "general:schedule"
_schedule_render_lock = asyncio.Lock()

# Locale-independent Russian day names for the schedule board headers
_MONTHS_GENITIVE = (
    "января февраля марта апреля мая июня "
    "июля августа сентября октября ноября декабря"
).split()
_WEEKDAYS_SHORT = "пн вт ср чт пт сб вс".split()


def _chat_short(group_id: int) -> str:
    """Strip the -100 prefix from a supergroup ID for t.me/c/ deep links."""
    return str(abs(group_id))[3:]


def _topic_link(chat_short: str, topic_id: int | None, sub: Submission) -> str:
    """Build a deep link to the submission's post inside its forum topic.

    Falls back to the topic root when the card message is unknown, and to a dash
    when the user has no topic at all.
    """
    if not topic_id:
        return "—"
    message_id = None
    if sub.topic_media_message_ids:
        message_id = sub.topic_media_message_ids[0]
    elif sub.topic_card_message_id:
        message_id = sub.topic_card_message_id
    if message_id:
        url = f"https://t.me/c/{chat_short}/{topic_id}/{message_id}"
    else:
        url = f"https://t.me/c/{chat_short}/{topic_id}"
    return f"<a href='{url}'>тема</a>"


def _queue_key(idx: int) -> str:
    return f"{_QUEUE_KEY_PREFIX}{idx:02d}"


def _checksum(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()  # noqa: S324 (non-security checksum)


def _queue_line(
    idx: int, local_date: str, author: str, sub_id: int, link: str, status: str
) -> str:
    emoji = get_style(status).emoji
    return f"{emoji} <b>{idx}</b>. {local_date} — {author} (#{sub_id}) → {link}"


async def _build_queue_lines(session: AsyncSession) -> list[str]:
    """Build a list of formatted queue entry strings, one per active submission."""
    tz = ZoneInfo(config.timezone)
    group_id = config.moderator_group_id
    chat_short = _chat_short(group_id)

    result = await session.execute(
        select(Submission)
        .where(Submission.status.in_(["pending", "scheduled"]))
        .order_by(Submission.created_at.asc())
        .options(selectinload(Submission.user), selectinload(Submission.media))
    )
    submissions = list(result.scalars().all())

    # Bulk-fetch user topics for formatting deep links
    user_ids = [s.user_id for s in submissions]
    topics_by_user: dict[int, int] = {}
    if user_ids:
        topic_result = await session.execute(
            select(UserTopic.user_id, UserTopic.topic_id).where(
                UserTopic.user_id.in_(user_ids)
            )
        )
        topics_by_user = {row.user_id: row.topic_id for row in topic_result}

    lines: list[str] = []
    for i, sub in enumerate(submissions, start=1):
        raw_author = sub.user.full_name if sub.user else f"user:{sub.user_id}"
        author = html.escape(raw_author)
        if sub.created_at:
            local_date = sub.created_at.astimezone(tz).strftime("%d.%m.%Y")
        else:
            local_date = "—"

        link = _topic_link(chat_short, topics_by_user.get(sub.user_id), sub)

        lines.append(_queue_line(i, local_date, author, sub.id, link, sub.status))

    return lines


def _chunk_lines(lines: list[str]) -> list[str]:
    """Split queue lines into chunks of at most _QUEUE_MAX_LINES_PER_CHUNK.

    The first chunk gets the ``<b>Очередь постов в предложке:</b>`` header.
    An empty queue returns a single chunk with "Очередь пуста.".
    """
    if not lines:
        return ["Очередь пуста."]

    chunks: list[str] = []
    for start in range(0, len(lines), _QUEUE_MAX_LINES_PER_CHUNK):
        chunk_lines = lines[start : start + _QUEUE_MAX_LINES_PER_CHUNK]
        text = "\n".join(chunk_lines)
        if start == 0:
            text = f"<b>Очередь постов в предложке:</b>\n{text}"
        chunks.append(text)
    return chunks


async def _render_queue_inner(
    bot: Bot, session: AsyncSession, *, force_reconcile: bool = False
) -> None:
    group_id = config.moderator_group_id
    lines = await _build_queue_lines(session)
    chunks = _chunk_lines(lines)
    existing = await list_system_messages_by_prefix(session, _QUEUE_KEY_PREFIX)
    # Close the read-only transaction before the first Telegram call below.
    await session.commit()

    for idx, chunk_text in enumerate(chunks):
        key = _queue_key(idx)
        cs = _checksum(chunk_text)
        existing_row = next((r for r in existing if r.key == key), None)

        if (
            existing_row is not None
            and existing_row.payload
            and existing_row.payload.get("checksum") == cs
            and not force_reconcile
        ):
            continue  # Content unchanged — skip

        if existing_row is not None:
            # Try to edit in place
            try:
                await bot.edit_message_text(
                    chat_id=existing_row.chat_id,
                    message_id=existing_row.message_id,
                    text=chunk_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                await upsert_system_message(
                    session,
                    key,
                    existing_row.chat_id,
                    existing_row.message_id,
                    payload={"checksum": cs},
                )
                await session.commit()
                logger.debug("Очередь [%s] обновлена", key)
                continue
            except TelegramAPIError as e:
                err = str(e).lower()
                if "message is not modified" in err:
                    await upsert_system_message(
                        session,
                        key,
                        existing_row.chat_id,
                        existing_row.message_id,
                        payload={"checksum": cs},
                    )
                    await session.commit()
                    continue
                if (
                    "message to edit not found" not in err
                    and "message_id_invalid" not in err
                ):
                    logger.warning("Ошибка при обновлении очереди [%s]: %s", key, e)
                    continue
                logger.info(
                    "Сообщение очереди [%s] не найдено (message_id=%d), пересоздаём",
                    key,
                    existing_row.message_id,
                )
                # Fall through to create a new message

        # Create new message
        sent = await bot.send_message(
            group_id,
            chunk_text,
            parse_mode="HTML",
            disable_notification=True,
            disable_web_page_preview=True,
        )
        await bot.pin_chat_message(group_id, sent.message_id, disable_notification=True)
        await upsert_system_message(
            session,
            key,
            group_id,
            sent.message_id,
            payload={"checksum": cs},
        )
        await session.commit()
        logger.info("Очередь [%s] создана (message_id=%d)", key, sent.message_id)

    # Delete surplus chunks (queue shrank)
    for extra_row in existing:
        idx_str = extra_row.key.removeprefix(_QUEUE_KEY_PREFIX)
        try:
            extra_idx = int(idx_str)
        except ValueError:
            continue
        if extra_idx >= len(chunks):
            try:
                await bot.delete_message(extra_row.chat_id, extra_row.message_id)
            except TelegramAPIError as e:
                err = str(e).lower()
                if "message to delete not found" not in err:
                    logger.warning(
                        "Не удалось удалить лишний чанк очереди [%s]: %s", extra_row.key, e
                    )
                    continue
            await delete_system_message(session, extra_row.key)
            await session.commit()
            logger.info("Лишний чанк очереди [%s] удалён", extra_row.key)


async def render_queue(
    bot: Bot, session: AsyncSession, *, force_reconcile: bool = False
) -> None:
    """Update the queue board in the General topic. Best-effort, thread-safe."""
    try:
        async with _render_lock:
            await _render_queue_inner(bot, session, force_reconcile=force_reconcile)
    except Exception:
        logger.warning("Не удалось обновить очередь в General-теме", exc_info=True)


def _day_header(day: date, today: date) -> str:
    """Human-readable day header: «Сегодня, 15 июня» / «22 июня, пн»."""
    day_str = f"{day.day} {_MONTHS_GENITIVE[day.month - 1]}"
    delta = (day - today).days
    if delta == 0:
        label = f"Сегодня, {day_str}"
    elif delta == 1:
        label = f"Завтра, {day_str}"
    elif delta < 0:
        label = f"Просрочено, {day_str}"
    else:
        label = f"{day_str}, {_WEEKDAYS_SHORT[day.weekday()]}"
    return f"<b>── {label} ──</b>"


async def _build_schedule_lines(session: AsyncSession) -> list[str]:
    """Build formatted schedule lines, grouped into day blocks with headers.

    Blocks are separated by a blank line; within a block only the time is shown,
    since the date already lives in the day header.
    """
    tz = ZoneInfo(config.timezone)
    group_id = config.moderator_group_id
    chat_short = _chat_short(group_id)

    result = await session.execute(
        select(Publication)
        .options(
            selectinload(Publication.submission).selectinload(Submission.user),
        )
        .where(Publication.published_at.is_(None))
        .order_by(Publication.publish_at.asc())
    )
    publications = list(result.scalars().all())

    # Bulk-fetch user topics for formatting deep links
    user_ids = [p.submission.user_id for p in publications]
    topics_by_user: dict[int, int] = {}
    if user_ids:
        topic_result = await session.execute(
            select(UserTopic.user_id, UserTopic.topic_id).where(
                UserTopic.user_id.in_(user_ids)
            )
        )
        topics_by_user = {row.user_id: row.topic_id for row in topic_result}

    today = datetime.now(tz).date()
    lines: list[str] = []
    current_day: date | None = None
    for pub in publications:
        sub = pub.submission
        local_dt = pub.publish_at.astimezone(tz)
        if local_dt.date() != current_day:
            current_day = local_dt.date()
            if lines:
                lines.append("")
            lines.append(_day_header(current_day, today))

        raw_author = sub.user.full_name if sub.user else f"user:{sub.user_id}"
        author = html.escape(raw_author)
        link = _topic_link(chat_short, topics_by_user.get(sub.user_id), sub)
        lines.append(
            f"<b>{local_dt.strftime('%H:%M')}</b> · {author} (#{sub.id}) → {link}"
        )

    return lines


def _build_schedule_text(lines: list[str]) -> str:
    """Format schedule lines into the final message text."""
    header = "📅 <b>Запланированные посты:</b>"
    if not lines:
        return header + "\n" + "В данный момент ничего не запланировано."
    return header + "\n\n" + "\n".join(lines)


async def _render_schedule_inner(
    bot: Bot, session: AsyncSession, *, force_reconcile: bool = False
) -> None:
    existing = await get_system_message(session, _SCHEDULE_KEY)
    lines = await _build_schedule_lines(session)
    text = _build_schedule_text(lines)
    cs = _checksum(text)

    if (
        existing is not None
        and existing.payload
        and existing.payload.get("checksum") == cs
        and not force_reconcile
    ):
        return  # Content unchanged — skip

    # Close the read-only transaction before the first Telegram call below.
    await session.commit()

    if existing is not None:
        # Try to edit in place
        try:
            await bot.edit_message_text(
                chat_id=existing.chat_id,
                message_id=existing.message_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            await upsert_system_message(
                session,
                _SCHEDULE_KEY,
                existing.chat_id,
                existing.message_id,
                payload={"checksum": cs},
            )
            await session.commit()
            logger.debug("Расписание обновлено")
            return
        except TelegramAPIError as e:
            err = str(e).lower()
            if "message is not modified" in err:
                await upsert_system_message(
                    session,
                    _SCHEDULE_KEY,
                    existing.chat_id,
                    existing.message_id,
                    payload={"checksum": cs},
                )
                await session.commit()
                return
            if (
                "message to edit not found" not in err
                and "message_id_invalid" not in err
            ):
                logger.warning("Ошибка при обновлении расписания: %s", e)
                return
            logger.info(
                "Сообщение расписания не найдено (message_id=%d), пересоздаём",
                existing.message_id,
            )
        # Fall through to create a new message

    group_id = config.moderator_group_id
    sent = await bot.send_message(
        group_id,
        text,
        parse_mode="HTML",
        disable_notification=True,
        disable_web_page_preview=True,
    )
    await bot.pin_chat_message(group_id, sent.message_id, disable_notification=True)
    await upsert_system_message(
        session,
        _SCHEDULE_KEY,
        group_id,
        sent.message_id,
        payload={"checksum": cs},
    )
    await session.commit()
    logger.info("Расписание создано (message_id=%d)", sent.message_id)


async def render_schedule(
    bot: Bot, session: AsyncSession, *, force_reconcile: bool = False
) -> None:
    """Update the schedule board in the General topic. Best-effort, thread-safe."""
    try:
        async with _schedule_render_lock:
            await _render_schedule_inner(bot, session, force_reconcile=force_reconcile)
    except Exception:
        logger.warning(
            "Не удалось обновить борд расписания в General-теме", exc_info=True
        )
