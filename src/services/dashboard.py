"""Dashboard service: the single pinned message in the General topic.

It carries three blocks in one message — stats summary, status legend, useful
commands. The legend used to live in its own pinned message (``general:legend``,
posted by ``services/topics.ensure_general_topic_nav``); it is folded in here so
the General topic keeps exactly one pin. ``cleanup_legacy_legend_pin`` removes
the old separate message on startup.


The queue board is already event-driven with a 5-minute self-heal (see
``services/topics_queue.py``). Hanging the dashboard on the same events would
double the ``editMessageText`` traffic into one group and risk
``TelegramRetryAfter`` on submission bursts. So event-driven call sites never
render the dashboard directly — they only call ``request_dashboard()`` to mark
it dirty; a dedicated job (``dashboard_render_job``) renders it on its own
minimal interval, coalescing any number of dirty flags raised between ticks.

IMPORTANT: This service is designed for **single-process** bots only.
``_render_lock`` is a process-local asyncio.Lock and will not prevent
concurrent rendering across multiple processes.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.config import config
from core.logging import get_logger
from core.topic_status_config import build_topic_nav_legend
from db.models import EditLock
from db.queries import (
    count_recent_publications,
    count_recent_rejections,
    count_submissions_by_status,
    get_system_message,
    get_user_by_id,
    list_dead_publications,
    upsert_system_message,
)
from utils.formatting import user_mention

logger = get_logger(__name__)

_DASHBOARD_KEY = "general:stats"
_MAX_LOCK_LINES = 20  # cap on rendered lock lines, to stay clear of the 4096-char message limit
_MIN_RENDER_INTERVAL = 60  # seconds between re-renders
_FORCE_RENDER_INTERVAL = 300  # forced self-heal, even if not dirty
_dirty: bool = False
_last_render_at: float = 0.0  # loop.time()
_render_lock = asyncio.Lock()


def _checksum(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()  # noqa: S324 (non-security checksum)


def request_dashboard() -> None:
    """Mark the dashboard dirty. No IO — the render job picks it up on its next tick."""
    global _dirty
    _dirty = True


async def _build_locks_block(session: AsyncSession) -> str:
    """Build the "who's editing what right now" block."""
    result = await session.execute(
        select(EditLock).where(EditLock.expires_at > datetime.now(timezone.utc))
    )
    locks = list(result.scalars().all())
    if not locks:
        return msg.DASHBOARD_LOCKS_EMPTY

    lines = [msg.DASHBOARD_LOCKS_HEADER]
    for lock in locks[:_MAX_LOCK_LINES]:
        owner = await get_user_by_id(session, lock.moderator_id)
        mod = user_mention(owner) if owner else f"id:{lock.moderator_id}"
        if lock.resource_type == "submission":
            lines.append(msg.DASHBOARD_LOCK_SUBMISSION.format(mod=mod, sub_id=lock.resource_id))
        elif lock.resource_type == "management":
            section = msg.DASHBOARD_SECTION_LABELS.get(lock.resource_id, lock.resource_id)
            lines.append(msg.DASHBOARD_LOCK_MANAGEMENT.format(mod=mod, section=section))
    if len(locks) > _MAX_LOCK_LINES:
        lines.append(msg.DASHBOARD_LOCKS_MORE.format(count=len(locks) - _MAX_LOCK_LINES))
    return "\n".join(lines)


async def _build_dashboard_text(session: AsyncSession, legend: str = "") -> str:
    status_counts = await count_submissions_by_status(session)
    dead = len(await list_dead_publications(session))
    pending = status_counts.get("pending", 0)
    scheduled = status_counts.get("scheduled", 0) - dead

    since = datetime.now(timezone.utc) - timedelta(days=7)
    published_7d = await count_recent_publications(session, since)
    rejected_7d = await count_recent_rejections(session, since)

    locks_block = await _build_locks_block(session)

    stats = msg.DASHBOARD_TEXT.format(
        pending=pending,
        scheduled=scheduled,
        dead=dead,
        published_7d=published_7d,
        rejected_7d=rejected_7d,
        locks_block=locks_block,
    )
    return f"{stats}\n\n{legend}" if legend else stats


async def _render_dashboard_inner(
    bot: Bot, session: AsyncSession, *, force: bool = False
) -> None:
    existing = await get_system_message(session, _DASHBOARD_KEY)
    # ``bot.me()`` is cached by aiogram after the first call — no extra API traffic.
    legend = build_topic_nav_legend((await bot.me()).username or "")
    text = await _build_dashboard_text(session, legend)
    cs = _checksum(text)

    if (
        existing is not None
        and existing.payload
        and existing.payload.get("checksum") == cs
        and not force
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
                _DASHBOARD_KEY,
                existing.chat_id,
                existing.message_id,
                payload={"checksum": cs},
            )
            await session.commit()
            logger.debug("Дашборд обновлён")
            return
        except TelegramAPIError as e:
            err = str(e).lower()
            if "message is not modified" in err:
                await upsert_system_message(
                    session,
                    _DASHBOARD_KEY,
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
                logger.warning("Ошибка при обновлении дашборда: %s", e)
                return
            logger.info(
                "Сообщение дашборда не найдено (message_id=%d), пересоздаём",
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
    try:
        await bot.pin_chat_message(group_id, sent.message_id, disable_notification=True)
    except TelegramAPIError as e:
        logger.warning("Не удалось закрепить дашборд: %s", e)

    await upsert_system_message(
        session,
        _DASHBOARD_KEY,
        group_id,
        sent.message_id,
        payload={"checksum": cs},
    )
    await session.commit()
    logger.info("Дашборд создан (message_id=%d)", sent.message_id)


async def render_dashboard(bot: Bot, session: AsyncSession, *, force: bool = False) -> None:
    """Render the dashboard in the General topic. Best-effort, thread-safe.

    ``force`` bypasses the checksum skip (self-heal), matching
    ``render_queue``/``render_schedule``'s ``force_reconcile``.
    """
    try:
        await _render_dashboard_inner(bot, session, force=force)
    except Exception:
        logger.warning("Не удалось обновить дашборд в General-теме", exc_info=True)


async def dashboard_render_job(bot: Bot, session_factory) -> None:
    """Periodic tick (called every 60s by the scheduler).

    Coalesces any number of ``request_dashboard()`` calls between ticks into
    at most one render. ``_dirty`` is cleared *before* rendering so an event
    that lands mid-render is not lost — it will be picked up by the next tick.
    """
    global _dirty, _last_render_at

    async with _render_lock:
        loop = asyncio.get_running_loop()
        now = loop.time()
        elapsed = now - _last_render_at
        force = elapsed >= _FORCE_RENDER_INTERVAL
        if not _dirty and not force:
            return

        _dirty = False
        async with session_factory() as session:
            await render_dashboard(bot, session, force=force)
        _last_render_at = loop.time()
