"""Moderator: unschedule publication handler."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.config import config
from core.logging import get_logger
from db.models import User
from db.queries import (
    delete_publication,
    get_publication_by_submission,
    get_submission_with_user,
    update_submission_status,
)
from keyboards.callbacks import SubmissionCB
from keyboards.moderator import submission_actions_kb
from services import edit_lock, topic_notifications, topics
from services.scheduler import cancel_scheduled
from services.topics_queue import render_queue as _render_queue
from services.topics_queue import render_schedule as _render_schedule
from states.moderator import ModeratorReview

logger = get_logger(__name__)

router = Router()


@router.callback_query(SubmissionCB.filter(F.action == "unschedule"))
async def handle_unschedule(
    callback: CallbackQuery,
    callback_data: SubmissionCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    """Remove scheduled publication and revert to pending."""
    sub_id = callback_data.sub_id
    sub = await get_submission_with_user(session, sub_id)
    if sub is None or sub.status != "scheduled":
        await callback.answer(msg.SUBMISSION_NOT_AVAILABLE, show_alert=True)
        return

    # Extend lock before modifying
    still_mine = await edit_lock.extend_lock(
        session, "submission", str(sub_id), db_user.id,
        ttl_seconds=config.edit_lock_ttl_seconds,
    )
    if not still_mine:
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return

    pub = await get_publication_by_submission(session, sub_id)
    if pub is None:
        await callback.answer(msg.SUBMISSION_NOT_AVAILABLE, show_alert=True)
        return

    cancel_scheduled(pub.id)
    await delete_publication(session, pub.id)

    await update_submission_status(session, sub_id, "pending")
    sub.status = "pending"
    await session.commit()

    logger.info("Пост #%d снят с расписания", sub_id)

    await topic_notifications.notify_unscheduled(callback.bot, session, sub, db_user)
    await topics.update_submission_card(callback.bot, session, sub)
    await topics.request_topic_title_sync(session, sub.user.id)

    await _render_queue(callback.bot, session)
    await _render_schedule(callback.bot, session)

    # Update actions keyboard in-place to show pending-state buttons
    data = await state.get_data()
    if actions_id := data.get("actions_message_id"):
        try:
            await callback.bot.edit_message_reply_markup(
                chat_id=callback.message.chat.id,
                message_id=actions_id,
                reply_markup=submission_actions_kb(sub_id, "pending"),
            )
        except Exception:
            pass
    await state.set_state(ModeratorReview.viewing_post)
    await state.update_data(schedule_message_id=None, prompt_message_id=None)
    await callback.answer(msg.UNSCHEDULED_OK.format(sub_id=sub_id))
