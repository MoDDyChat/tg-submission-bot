"""Shared submission intake logic for viewer and moderator-submit modes."""

import asyncio
import time

from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.logging import get_logger, fmt_user
from db.models import User
from db.session import session_factory
from db.queries import (
    add_media,
    create_submission,
    get_submission_with_user,
)
from keyboards.viewer import submission_confirmed_kb
from services import topics
from services.topics_queue import render_queue as _render_queue
from utils.html_entities import get_html_caption, get_html_text
from utils.media import extract_media_info
from utils.tags import MAX_TEXT_CAPTION, compose_caption, strip_html_for_length, validate_caption_length

logger = get_logger(__name__)

_media_group_buffers: dict[str, list[Message]] = {}
_media_group_locks: dict[str, asyncio.Lock] = {}
_media_group_timestamps: dict[str, float] = {}

_MEDIA_GROUP_WAIT = 2.0
_BUFFER_TTL = 300


def reset_media_group_buffers() -> None:
    """Clear all in-flight media group state. Used in tests and graceful shutdown."""
    _media_group_buffers.clear()
    _media_group_locks.clear()
    _media_group_timestamps.clear()


async def wait_for_pending_groups(timeout: float = 30.0) -> None:
    """Wait until all in-flight media groups are finalised or *timeout* seconds pass."""
    deadline = asyncio.get_running_loop().time() + timeout
    while _media_group_buffers and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.5)


async def _finalize_media_group(
    group_id: str,
    sf,
    db_user: User,
) -> None:
    """Wait for all media group messages to arrive, then save as one submission."""
    now_mono = time.monotonic()
    stale = [gid for gid, ts in _media_group_timestamps.items() if now_mono - ts > _BUFFER_TTL]
    for gid in stale:
        _media_group_buffers.pop(gid, None)
        _media_group_locks.pop(gid, None)
        _media_group_timestamps.pop(gid, None)

    await asyncio.sleep(_MEDIA_GROUP_WAIT)

    lock = _media_group_locks.pop(group_id, None)
    if lock is None:
        lock = asyncio.Lock()
    async with lock:
        messages = _media_group_buffers.pop(group_id, [])
    _media_group_timestamps.pop(group_id, None)

    if not messages:
        return

    messages.sort(key=lambda m: m.message_id)
    first_msg = messages[0]
    raw_caption = get_html_caption(first_msg) or first_msg.text
    clean_caption = raw_caption or None

    extra_captioned = [
        m for m in messages[1:]
        if get_html_caption(m) or m.caption
    ]
    if extra_captioned:
        await first_msg.answer(
            msg.MEDIA_GROUP_EXTRA_CAPTIONS_WARNING.format(count=len(extra_captioned))
        )

    if clean_caption and not validate_caption_length([], clean_caption):
        await first_msg.answer(msg.CAPTION_TOO_LONG)
        return

    try:
        async with sf() as session:
            sub = await create_submission(session, user_id=db_user.id, caption=clean_caption)

            for i, m in enumerate(messages):
                info = extract_media_info(m)
                if info:
                    file_id, file_unique_id, media_type = info
                    await add_media(
                        session,
                        submission_id=sub.id,
                        file_id=file_id,
                        file_unique_id=file_unique_id,
                        media_type=media_type,
                        sort_order=i,
                    )

            await session.commit()

            logger.info(
                "Новый альбом #%d от %s, медиа: %d шт.",
                sub.id, fmt_user(db_user), len(messages),
            )

            await first_msg.answer(
                msg.SUBMISSION_ACCEPTED.format(sub_id=sub.id),
                reply_markup=submission_confirmed_kb(sub.id),
            )

            sub_full = await get_submission_with_user(session, sub.id)
            if sub_full:
                try:
                    await topics.post_submission_card(first_msg.bot, session, sub_full)
                    await topics.request_topic_title_sync(session, sub_full.user.id)
                    await _render_queue(first_msg.bot, session)
                    await session.commit()
                except Exception:
                    await first_msg.answer(msg.SUBMISSION_SEND_ERROR)
    except Exception:
        logger.exception("Ошибка при финализации медиа-группы %s", group_id)


async def buffer_media_group_message(message: Message, db_user: User) -> None:
    """Buffer a media group message and schedule finalization on first message."""
    group_id = message.media_group_id
    _media_group_timestamps[group_id] = time.monotonic()
    _media_group_locks.setdefault(group_id, asyncio.Lock())

    async with _media_group_locks[group_id]:
        if group_id not in _media_group_buffers:
            _media_group_buffers[group_id] = []
            asyncio.create_task(
                _finalize_media_group(group_id, session_factory, db_user)
            )
        _media_group_buffers[group_id].append(message)


async def submit_single_media(message: Message, session: AsyncSession, db_user: User) -> None:
    """Create a single-media submission (ban guard must be applied by caller)."""
    info = extract_media_info(message)
    if not info:
        await message.answer(msg.UNSUPPORTED_MEDIA)
        return

    file_id, file_unique_id, media_type = info
    raw_caption = get_html_caption(message)
    clean_caption = raw_caption or None

    if clean_caption and not validate_caption_length([], clean_caption):
        await message.answer(msg.CAPTION_TOO_LONG)
        return

    sub = await create_submission(session, user_id=db_user.id, caption=clean_caption)
    await add_media(
        session,
        submission_id=sub.id,
        file_id=file_id,
        file_unique_id=file_unique_id,
        media_type=media_type,
    )

    logger.info(
        "Новый пост #%d от %s, тип: %s",
        sub.id, fmt_user(db_user), media_type,
    )

    await message.answer(
        msg.SUBMISSION_ACCEPTED.format(sub_id=sub.id),
        reply_markup=submission_confirmed_kb(sub.id),
    )

    sub_full = await get_submission_with_user(session, sub.id)
    if sub_full:
        try:
            await topics.post_submission_card(message.bot, session, sub_full)
            await topics.request_topic_title_sync(session, sub_full.user.id)
            await _render_queue(message.bot, session)
        except Exception:
            logger.error("Не удалось отправить пост #%d в тему форума", sub.id)
            await message.answer(msg.SUBMISSION_SEND_ERROR)


async def submit_text(message: Message, session: AsyncSession, db_user: User) -> None:
    """Create a text-only submission (ban guard must be applied by caller)."""
    text = get_html_text(message)
    if not text.strip():
        await message.answer(msg.TEXT_EMPTY_WARNING)
        return

    if not validate_caption_length([], text, has_media=False):
        length = len(strip_html_for_length(compose_caption([], text)))
        await message.answer(msg.TEXT_TOO_LONG.format(length=length, max_length=MAX_TEXT_CAPTION))
        return

    sub = await create_submission(session, user_id=db_user.id, caption=text)

    logger.info("Новый текстовый пост #%d от %s", sub.id, fmt_user(db_user))

    await message.answer(
        msg.SUBMISSION_ACCEPTED.format(sub_id=sub.id),
        reply_markup=submission_confirmed_kb(sub.id),
    )

    sub_full = await get_submission_with_user(session, sub.id)
    if sub_full:
        try:
            await topics.post_submission_card(message.bot, session, sub_full)
            await topics.request_topic_title_sync(session, sub_full.user.id)
            await _render_queue(message.bot, session)
        except Exception:
            logger.error("Не удалось отправить текстовый пост #%d в тему форума", sub.id)
            await message.answer(msg.SUBMISSION_SEND_ERROR)
