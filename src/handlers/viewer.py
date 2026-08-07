"""Viewer submission handlers: receiving posts and cancellation."""

import asyncio
import html
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.logging import fmt_user, get_logger
from db.models import User
from db.queries import (
    get_submission_with_user,
    update_submission_status,
)
from filters.is_moderator import IsModerator
from keyboards.callbacks import ViewerCancelCB
from services import topic_notifications, topics
from services.author_card import request_author_card
from services.dashboard import request_dashboard
from services.submission_intake import (
    _media_group_buffers,
    _media_group_timestamps,
    _MEDIA_GROUP_WAIT,
    buffer_media_group_message,
    reset_media_group_buffers,
    submit_single_media,
    submit_text,
    wait_for_pending_groups,
)
from services.topics_queue import render_queue as _render_queue

logger = get_logger(__name__)

router = Router()

# Re-export for backward compatibility (bot.py, tests)
__all__ = ["reset_media_group_buffers", "wait_for_pending_groups"]


@router.message(F.media_group_id, F.photo | F.video | F.animation | F.document, ~IsModerator())
async def handle_media_group(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Collect media group messages into a buffer."""
    if db_user.is_banned:
        group_id = message.media_group_id
        if group_id not in _media_group_buffers:
            _media_group_buffers[group_id] = []
            _media_group_timestamps[group_id] = time.monotonic()
            await message.answer(
                msg.YOU_ARE_BANNED.format(
                    reason=html.escape(db_user.ban_reason or "Не указана")
                ),
                parse_mode="HTML",
            )
            async def _cleanup(gid: str = group_id) -> None:
                await asyncio.sleep(_MEDIA_GROUP_WAIT + 1)
                _media_group_buffers.pop(gid, None)
                _media_group_timestamps.pop(gid, None)
            asyncio.create_task(_cleanup())
        return

    await buffer_media_group_message(message, db_user)


@router.message(F.photo | F.video | F.animation | F.document, ~IsModerator())
async def handle_single_media(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Handle a single media message (not part of a media group)."""
    if db_user.is_banned:
        await message.answer(
            msg.YOU_ARE_BANNED.format(
                reason=html.escape(db_user.ban_reason or "Не указана")
            ),
            parse_mode="HTML",
        )
        return

    await submit_single_media(message, session, db_user)


@router.message(F.text & ~F.text.startswith("/"), ~IsModerator())
async def handle_text_submission(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Handle text-only submission (no media attachment)."""
    if db_user.is_banned:
        await message.answer(
            msg.YOU_ARE_BANNED.format(
                reason=html.escape(db_user.ban_reason or "Не указана")
            ),
            parse_mode="HTML",
        )
        return

    await submit_text(message, session, db_user)


@router.callback_query(ViewerCancelCB.filter())
async def handle_viewer_cancel(
    callback: CallbackQuery,
    callback_data: ViewerCancelCB,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Viewer cancels their pending submission."""
    sub = await get_submission_with_user(session, callback_data.sub_id)

    if sub is None:
        await callback.answer(msg.SUBMISSION_NOT_FOUND, show_alert=True)
        return

    if sub.user_id != db_user.id:
        await callback.answer(msg.NOT_YOUR_SUBMISSION, show_alert=True)
        return

    if sub.status != "pending":
        await callback.answer(
            msg.SUBMISSION_CANT_CANCEL.format(status=sub.status), show_alert=True
        )
        return

    await update_submission_status(session, sub.id, "cancelled")
    sub.status = "cancelled"
    await topic_notifications.notify_viewer_cancelled(callback.bot, session, sub)
    await topics.finalize_submission_card(callback.bot, session, sub)
    await topics.request_topic_title_sync(session, sub.user.id)
    await _render_queue(callback.bot, session)
    request_dashboard()
    request_author_card(db_user.id)

    logger.info("%s отменил пост #%d", fmt_user(db_user), sub.id)

    try:
        await callback.message.edit_text(msg.SUBMISSION_CANCELLED.format(sub_id=sub.id))
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await callback.answer(msg.SUBMISSION_CANCELLED.format(sub_id=sub.id))

