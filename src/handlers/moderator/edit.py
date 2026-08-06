"""Moderator: edit caption handlers."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.config import config
from core.logging import get_logger
from db.models import User
from db.queries import get_submission_with_user, update_submission_caption
from keyboards.callbacks import SubmissionCB
from services import edit_lock, topic_notifications, topics
from states.moderator import ModeratorReview
from utils.html_entities import get_html_text
from utils.tags import MAX_MEDIA_CAPTION, MAX_TEXT_CAPTION, compose_caption, strip_html_for_length, validate_caption_length

from ._helpers import TERMINAL_STATUSES, _send_submission_view

logger = get_logger(__name__)

router = Router()

MAX_CAPTION_LENGTH = MAX_MEDIA_CAPTION


@router.callback_query(SubmissionCB.filter(F.action == "edit_caption"))
async def handle_edit_caption_start(
    callback: CallbackQuery,
    callback_data: SubmissionCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    sub = await get_submission_with_user(session, callback_data.sub_id)
    if sub is None or sub.status in TERMINAL_STATUSES:
        await callback.answer(msg.SUBMISSION_NOT_AVAILABLE, show_alert=True)
        return

    # Extend lock to keep session alive
    still_mine = await edit_lock.extend_lock(
        session, "submission", str(callback_data.sub_id), db_user.id,
        ttl_seconds=config.edit_lock_ttl_seconds,
    )
    if not still_mine:
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return

    await state.set_state(ModeratorReview.editing_caption)
    prompt = await callback.message.answer(msg.EDIT_CAPTION_PROMPT)
    await state.update_data(sub_id=callback_data.sub_id, prompt_message_id=prompt.message_id)
    await callback.answer()


@router.message(ModeratorReview.editing_caption, F.text)
async def handle_edit_caption_text(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    data = await state.get_data()
    sub_id = data["sub_id"]

    # Extend lock before doing anything
    still_mine = await edit_lock.extend_lock(
        session, "submission", str(sub_id), db_user.id,
        ttl_seconds=config.edit_lock_ttl_seconds,
    )
    if not still_mine:
        await state.clear()
        await message.answer(msg.MODERATOR_LOCK_LOST)
        return

    sub = await get_submission_with_user(session, sub_id)
    if sub is None or sub.status in TERMINAL_STATUSES:
        await message.answer(msg.SUBMISSION_NOT_AVAILABLE)
        await state.clear()
        return

    new_caption = get_html_text(message)

    has_media = bool(sub.media)
    cap_limit = MAX_CAPTION_LENGTH if has_media else MAX_TEXT_CAPTION
    if not validate_caption_length(sub.tags or [], new_caption, has_media=has_media):
        total = len(strip_html_for_length(compose_caption(sub.tags or [], new_caption)))
        await message.answer(
            f"Описание с тегами слишком длинное ({total} символов). "
            f"Максимум — {cap_limit}."
        )
        return

    old_caption = sub.caption
    await update_submission_caption(session, sub_id, new_caption)
    sub.caption = new_caption

    logger.info("Описание поста #%d изменено модератором", sub_id)

    if prompt_id := data.get("prompt_message_id"):
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass

    await _send_submission_view(message, session, sub_id, state)
    await topic_notifications.notify_caption_changed(
        message.bot, session, sub, db_user, old_caption, new_caption
    )
    await topics.update_submission_card(message.bot, session, sub)
    await topics.request_topic_title_sync(session, sub.user.id)
