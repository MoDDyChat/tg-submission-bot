"""Append media to existing submissions (used by moderator media manager)."""

import asyncio
import time
from enum import Enum

from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import core.messages as msg
from core.logging import get_logger
from db.models import Submission
from db.queries import add_media, get_submission_with_user
from db.session import session_factory
from services import edit_lock
from utils.media import extract_media_info, validate_media_group_composition
from utils.tags import validate_caption_length

logger = get_logger(__name__)

TERMINAL_STATUSES = {"cancelled", "published", "rejected"}


class AppendResult(str, Enum):
    OK = "ok"
    INVALID_COMPOSITION = "invalid_composition"
    CAPTION_TOO_LONG = "caption_too_long"
    UNSUPPORTED = "unsupported"


async def append_media_to_submission(
    session: AsyncSession,
    submission: Submission,
    new_items: list[tuple[str, str, str]],  # (file_id, file_unique_id, media_type)
) -> tuple[AppendResult, int]:
    """Validate composition + caption limit, then add_media for each item."""
    if not new_items:
        return AppendResult.UNSUPPORTED, 0
    existing_types = [m.media_type for m in submission.media]
    combined = existing_types + [t for _, _, t in new_items]
    if not validate_media_group_composition(combined):
        return AppendResult.INVALID_COMPOSITION, 0
    if (
        not submission.media
        and submission.caption
        and not validate_caption_length(submission.tags or [], submission.caption, has_media=True)
    ):
        return AppendResult.CAPTION_TOO_LONG, 0
    start = (max((m.sort_order for m in submission.media), default=-1)) + 1
    for idx, (file_id, file_unique_id, media_type) in enumerate(new_items):
        await add_media(
            session,
            submission_id=submission.id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            media_type=media_type,
            sort_order=start + idx,
        )
    return AppendResult.OK, len(new_items)


_append_buffers: dict[str, list[Message]] = {}
_append_locks: dict[str, asyncio.Lock] = {}
_append_timestamps: dict[str, float] = {}
_append_sub_ids: dict[str, int] = {}
_append_done: dict[str, asyncio.Event] = {}
_append_cancelled: dict[int, bool] = {}
_sub_write_locks: dict[int, asyncio.Lock] = {}
_WAIT = 2.0
_BUFFER_TTL = 300


def reset_append_buffers() -> None:
    _append_buffers.clear()
    _append_locks.clear()
    _append_timestamps.clear()
    _append_sub_ids.clear()
    _append_done.clear()
    _append_cancelled.clear()
    _sub_write_locks.clear()


def cancel_append_for_sub(sub_id: int) -> None:
    _append_cancelled[sub_id] = True


def clear_append_cancel(sub_id: int) -> None:
    _append_cancelled.pop(sub_id, None)


async def wait_for_pending_append(sub_id: int, timeout: float = 30.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        events = [
            ev for gid, ev in list(_append_done.items())
            if _append_sub_ids.get(gid) == sub_id
        ]
        if not events:
            return
        for ev in events:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            try:
                await asyncio.wait_for(ev.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return


async def buffer_append_media_group(message: Message, sub_id: int, moderator_id: int) -> None:
    group_id = message.media_group_id
    _append_timestamps[group_id] = time.monotonic()
    _append_sub_ids[group_id] = sub_id
    _append_locks.setdefault(group_id, asyncio.Lock())
    async with _append_locks[group_id]:
        if group_id not in _append_buffers:
            _append_buffers[group_id] = []
            _append_done[group_id] = asyncio.Event()
            asyncio.create_task(
                _finalize_append(group_id, session_factory, sub_id, moderator_id)
            )
        _append_buffers[group_id].append(message)


async def _finalize_append(group_id: str, sf: async_sessionmaker[AsyncSession], sub_id: int, moderator_id: int) -> None:
    try:
        now_mono = time.monotonic()
        stale = [
            gid for gid, ts in list(_append_timestamps.items())
            if now_mono - ts > _BUFFER_TTL
        ]
        for gid in stale:
            _append_buffers.pop(gid, None)
            _append_locks.pop(gid, None)
            _append_timestamps.pop(gid, None)
            _append_sub_ids.pop(gid, None)
            done_ev = _append_done.pop(gid, None)
            if done_ev:
                done_ev.set()

        await asyncio.sleep(_WAIT)

        if _append_cancelled.get(sub_id, False):
            return

        lock = _append_locks.get(group_id)
        if lock:
            async with lock:
                messages = _append_buffers.pop(group_id, [])
        else:
            messages = _append_buffers.pop(group_id, [])
        _append_locks.pop(group_id, None)
        _append_timestamps.pop(group_id, None)

        if not messages:
            return

        messages.sort(key=lambda m: m.message_id)
        first_msg = messages[0]

        new_items = [
            info for m in messages
            if (info := extract_media_info(m)) is not None
        ]

        sub_lock = _sub_write_locks.setdefault(sub_id, asyncio.Lock())
        async with sub_lock:
            async with sf() as session:
                active = await edit_lock.get_active_lock(
                    session, "submission", str(sub_id)
                )
                if active is None or active.moderator_id != moderator_id:
                    await first_msg.answer(msg.MODERATOR_LOCK_LOST)
                    return
                sub = await get_submission_with_user(session, sub_id)
                if sub is None or sub.status in TERMINAL_STATUSES:
                    return
                result, count = await append_media_to_submission(session, sub, new_items)
                if result == AppendResult.OK:
                    await session.commit()
                    await first_msg.answer(msg.MEDIA_ADDED.format(count=count))
                elif result == AppendResult.INVALID_COMPOSITION:
                    await first_msg.answer(msg.MEDIA_COMPOSITION_INVALID)
                elif result == AppendResult.CAPTION_TOO_LONG:
                    await first_msg.answer(msg.MEDIA_CAPTION_TOO_LONG_FOR_MEDIA)
                elif result == AppendResult.UNSUPPORTED:
                    await first_msg.answer(msg.MEDIA_UNSUPPORTED)
    except Exception:
        logger.exception("Ошибка при финализации альбома %s", group_id)
    finally:
        _append_sub_ids.pop(group_id, None)
        done_ev = _append_done.pop(group_id, None)
        if done_ev:
            done_ev.set()
