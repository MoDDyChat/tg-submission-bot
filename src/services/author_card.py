"""Author card service: the pinned first message of an author's forum topic.

The card is **never** posted as a standalone message by the render jobs: it is
created once, as the topic's opening message (``create_author_card_message``,
called from ``services/topics.ensure_user_topic``), or retrofitted onto an old
topic's welcome message by ``scripts/backfill_author_cards.py``. Everything
else only *edits* that message in place. A render pass that finds no registered
card simply does nothing — otherwise a restart would fire one new message into
every existing topic at once.

Mirrors the render pattern from ``services/topics_queue.py``: build the text in a
read-only transaction, commit before touching Telegram, then record the result
(and its MD5 checksum) in a fresh transaction. Idempotent via ``system_messages``.

IMPORTANT: This service is designed for **single-process** bots only — the
in-memory dirty set and ``_render_lock`` are process-local and won't coordinate
across multiple processes.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import core.messages as msg
from core.config import config
from core.logging import get_logger
from db.models import SystemMessage, User, UserTopic
from db.queries import (
    delete_system_message,
    get_author_stats,
    get_system_message,
    get_user_by_id,
    get_user_topic,
    upsert_system_message,
)
from keyboards.moderator import author_card_kb

logger = get_logger(__name__)

_MAX_CARDS_PER_PASS = 10
_RENDER_DELAY = 0.5
_RECONCILE_BATCH = 20

_dirty_user_ids: set[int] = set()
_render_lock = asyncio.Lock()
_reconcile_cursor: int = 0


def card_key(user_id: int) -> str:
    """``system_messages`` key of an author's card. Public: the backfill script uses it."""
    return f"user:{user_id}:card"


def _checksum(text: str, kb: InlineKeyboardMarkup | None = None) -> str:
    """Digest of the exact card payload sent to Telegram (text + keyboard).

    ``kb`` defaults to ``None`` so callers that only care about the text
    (e.g. nothing) can still use the plain form. The keyboard must be part of
    the checksum: a moderator promotion hides the ban button without changing
    any other line of the card text, so a text-only checksum would miss it.
    """
    payload = text if kb is None else text + repr(kb.model_dump())
    return hashlib.md5(payload.encode()).hexdigest()  # noqa: S324 (non-security checksum)


def _payload(cs: str, existing: SystemMessage | None = None) -> dict:
    """Card payload: the checksum plus the sticky ``opening`` flag.

    ``opening`` marks a card that *is* the topic's first message (created with
    the topic, or retrofitted onto its welcome message). It must survive every
    re-render — the backfill script uses it to skip already-converted topics.
    """
    payload: dict = {"checksum": cs}
    if existing is not None and existing.payload and existing.payload.get("opening"):
        payload["opening"] = True
    return payload


async def build_author_card(session: AsyncSession, user: User) -> str:
    """Build the author card's HTML text (no keyboard — see ``services/author_card_kb``)."""
    tz = ZoneInfo(config.timezone)
    name = html.escape(user.full_name)
    username_line = (
        msg.AUTHOR_CARD_USERNAME_LINE.format(username=html.escape(user.username))
        if user.username
        else ""
    )

    stats = await get_author_stats(session, user.id)
    if stats.first_seen is not None:
        first_seen = stats.first_seen.astimezone(tz).strftime("%d.%m.%Y")
    else:
        first_seen = msg.AUTHOR_CARD_FIRST_SEEN_NEVER

    if user.is_banned:
        # ban_reason may already contain HTML (see handlers/moderator/ban.py) — passed as-is.
        ban_line = msg.AUTHOR_CARD_BAN_LINE.format(reason=user.ban_reason or "—")
    else:
        ban_line = msg.AUTHOR_CARD_NO_BAN_LINE

    if user.moderator_note:
        note_line = msg.AUTHOR_CARD_NOTE_LINE.format(note=html.escape(user.moderator_note))
    else:
        note_line = msg.AUTHOR_CARD_NO_NOTE_LINE

    return msg.AUTHOR_CARD_TEXT.format(
        name=name,
        username_line=username_line,
        telegram_id=user.telegram_id,
        first_seen=first_seen,
        total=stats.total,
        published=stats.published,
        rejected=stats.rejected,
        pending=stats.pending,
        scheduled=stats.scheduled,
        ban_line=ban_line,
        note_line=note_line,
    )


async def render_author_card(
    bot: Bot,
    session: AsyncSession,
    user_id: int,
    *,
    force: bool = False,
) -> None:
    """Idempotently re-render an author's card **in place**.

    Never sends a message: a card that was never created (old topic not yet
    backfilled) or whose message is gone is silently skipped. Creation happens
    only in ``create_author_card_message``.
    """
    user = await get_user_by_id(session, user_id)
    topic = await get_user_topic(session, user_id)
    if user is None or topic is None:
        return  # No topic to render the card into.

    existing = await get_system_message(session, card_key(user_id))
    if existing is None:
        return  # No card message to edit — see the module docstring.

    text = await build_author_card(session, user)
    reply_markup = author_card_kb(user)
    cs = _checksum(text, reply_markup)

    if existing.payload and existing.payload.get("checksum") == cs and not force:
        return  # Content unchanged — skip

    # Close the read-only transaction before the first Telegram call below.
    await session.commit()

    try:
        await bot.edit_message_text(
            chat_id=existing.chat_id,
            message_id=existing.message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except TelegramAPIError as e:
        err = str(e).lower()
        if "message is not modified" not in err:
            if "message to edit not found" in err or "message_id_invalid" in err:
                # The card is unrecoverable: it was the topic's opening message and
                # a new one cannot take its place. Drop the record so the render
                # jobs stop retrying; scripts/backfill_author_cards.py can restore it.
                await delete_system_message(session, card_key(user_id))
                await session.commit()
                logger.warning(
                    "Карточка автора user_id=%d потеряна (message_id=%d), запись удалена",
                    user_id,
                    existing.message_id,
                )
            else:
                logger.warning(
                    "Ошибка при обновлении карточки автора user_id=%d: %s", user_id, e
                )
            return
        # "not modified" — the message already matches; just record the checksum.

    await upsert_system_message(
        session,
        card_key(user_id),
        existing.chat_id,
        existing.message_id,
        payload=_payload(cs, existing),
    )
    await session.commit()
    logger.debug("Карточка автора user_id=%d обновлена", user_id)


async def create_author_card_message(
    bot: Bot,
    session: AsyncSession,
    user: User,
    topic_id: int,
) -> int:
    """Post the author card as a topic's opening message, pin it, and register it.

    The single creation path in the running bot — called right after a forum
    topic is created (``services/topics.ensure_user_topic``). Returns the new
    ``message_id``; the caller owns the transaction and commits.
    """
    text = await build_author_card(session, user)
    reply_markup = author_card_kb(user)
    group_id = config.moderator_group_id

    sent = await bot.send_message(
        group_id,
        text,
        message_thread_id=topic_id,
        parse_mode="HTML",
        reply_markup=reply_markup,
        disable_notification=True,
        disable_web_page_preview=True,
    )
    try:
        await bot.pin_chat_message(group_id, sent.message_id, disable_notification=True)
    except TelegramAPIError as e:
        logger.warning("Не удалось закрепить карточку автора user_id=%d: %s", user.id, e)

    await upsert_system_message(
        session,
        card_key(user.id),
        group_id,
        sent.message_id,
        payload={"checksum": _checksum(text, reply_markup), "opening": True},
    )
    logger.info("Карточка автора user_id=%d создана (message_id=%d)", user.id, sent.message_id)
    return sent.message_id


def request_author_card(user_id: int) -> None:
    """Mark an author's card as dirty. Synchronous, no IO — safe to call from any handler."""
    _dirty_user_ids.add(user_id)


async def author_card_render_job(
    bot: Bot, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Periodic coalesced render: drains up to ``_MAX_CARDS_PER_PASS`` dirty cards."""
    try:
        async with _render_lock:
            batch = sorted(_dirty_user_ids)[:_MAX_CARDS_PER_PASS]
            _dirty_user_ids.difference_update(batch)

        for i, user_id in enumerate(batch):
            if i > 0:
                await asyncio.sleep(_RENDER_DELAY)
            try:
                async with session_factory() as session:
                    await render_author_card(bot, session, user_id)
            except Exception:
                _dirty_user_ids.add(user_id)
                logger.warning(
                    "Не удалось отрисовать карточку автора user_id=%d", user_id, exc_info=True
                )
    except Exception:
        logger.warning("Не удалось выполнить проход отрисовки карточек авторов", exc_info=True)


async def author_card_reconcile_job(
    bot: Bot, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Self-heal: periodically re-mark authors dirty so a lost in-memory set self-repairs."""
    global _reconcile_cursor
    try:
        async with session_factory() as session:
            stmt = (
                select(UserTopic.user_id)
                .where(UserTopic.user_id > _reconcile_cursor)
                .order_by(UserTopic.user_id.asc())
                .limit(_RECONCILE_BATCH)
            )
            result = await session.execute(stmt)
            user_ids = [row[0] for row in result.all()]

        if not user_ids:
            _reconcile_cursor = 0
            return

        for user_id in user_ids:
            _dirty_user_ids.add(user_id)
        _reconcile_cursor = user_ids[-1]
    except Exception:
        logger.warning("Не удалось выполнить reconcile карточек авторов", exc_info=True)
