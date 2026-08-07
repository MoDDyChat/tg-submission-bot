"""Moderator: reject (with reason) and silent reject handlers."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.config import config
from core.logging import get_logger
from db.models import User
from db.queries import get_submission_with_user, update_submission_status
from keyboards.callbacks import SubmissionCB
from services import edit_lock, topic_notifications, topics
from services.author_card import request_author_card
from services.dashboard import request_dashboard
from services.scheduler import cancel_scheduled
from services.topics_queue import render_queue as _render_queue
from services.topics_queue import render_schedule as _render_schedule
from states.moderator import ModeratorReview
from utils.html_entities import get_html_text

from ._helpers import TERMINAL_STATUSES, _delete_tracked_messages, _drop_pending_publication

logger = get_logger(__name__)

router = Router()


@router.callback_query(SubmissionCB.filter(F.action == "reject"))
async def handle_reject(
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

    await state.set_state(ModeratorReview.entering_reject_reason)
    prompt = await callback.message.answer(
        msg.REJECT_REASON_PROMPT.format(sub_id=callback_data.sub_id)
    )
    await state.update_data(sub_id=callback_data.sub_id, prompt_message_id=prompt.message_id)
    await callback.answer()


@router.message(ModeratorReview.entering_reject_reason, F.text)
async def handle_reject_reason(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    data = await state.get_data()
    sub_id = data["sub_id"]

    # Extend lock before finalising
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

    reason_html = get_html_text(message)

    # A scheduled post can be rejected straight from the card — drop its
    # publication so no orphan job/row survives the terminal transition.
    dropped_pub_id = await _drop_pending_publication(session, sub_id)

    await update_submission_status(session, sub_id, "rejected")
    sub.status = "rejected"
    await session.commit()
    # Only now that the delete is durable — cancelling a job cannot be undone.
    if dropped_pub_id is not None:
        cancel_scheduled(dropped_pub_id)

    await topic_notifications.notify_rejected(message.bot, session, sub, db_user, reason=reason_html, silent=False)
    await topics.finalize_submission_card(message.bot, session, sub)
    await topics.request_topic_title_sync(session, sub.user.id)
    await edit_lock.release_lock(session, "submission", str(sub_id), db_user.id)

    logger.info("Пост #%d отклонён. Причина: %s", sub_id, message.text)

    await _render_queue(message.bot, session)
    if dropped_pub_id is not None:
        await _render_schedule(message.bot, session)
    request_dashboard()
    request_author_card(sub.user.id)

    # Delete prompt, moderator's reason message, media and action buttons
    try:
        await message.delete()
    except Exception:
        pass
    await _delete_tracked_messages(message.bot, message.chat.id, data)

    # Notify the viewer — unless the moderator rejected their own post and
    # would get the same reason twice in the very same chat
    if sub.user.id != db_user.id:
        try:
            await message.bot.send_message(
                sub.user.telegram_id,
                msg.REJECTION_NOTIFICATION.format(sub_id=sub_id, reason=reason_html),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Не удалось уведомить зрителя об отклонении поста #%d: %s", sub_id, e)

    await state.clear()
    await message.answer(
        msg.REJECTED_WITH_REASON.format(sub_id=sub_id, reason=reason_html),
        parse_mode="HTML",
    )


@router.callback_query(SubmissionCB.filter(F.action == "reject_silent"))
async def handle_reject_silent(
    callback: CallbackQuery,
    callback_data: SubmissionCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    sub_id = callback_data.sub_id
    sub = await get_submission_with_user(session, sub_id)
    if sub is None or sub.status in TERMINAL_STATUSES:
        await callback.answer(msg.SUBMISSION_NOT_AVAILABLE, show_alert=True)
        return

    # Extend lock before finalising
    still_mine = await edit_lock.extend_lock(
        session, "submission", str(sub_id), db_user.id,
        ttl_seconds=config.edit_lock_ttl_seconds,
    )
    if not still_mine:
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return

    dropped_pub_id = await _drop_pending_publication(session, sub_id)

    await update_submission_status(session, sub_id, "rejected")
    sub.status = "rejected"
    await session.commit()
    # Only now that the delete is durable — cancelling a job cannot be undone.
    if dropped_pub_id is not None:
        cancel_scheduled(dropped_pub_id)

    await topic_notifications.notify_rejected(callback.bot, session, sub, db_user, reason=None, silent=True)
    await topics.finalize_submission_card(callback.bot, session, sub)
    await topics.request_topic_title_sync(session, sub.user.id)
    await edit_lock.release_lock(session, "submission", str(sub_id), db_user.id)

    logger.info("Пост #%d тихо отклонён.", sub_id)

    await _render_queue(callback.bot, session)
    if dropped_pub_id is not None:
        await _render_schedule(callback.bot, session)
    request_dashboard()
    request_author_card(sub.user.id)

    data = await state.get_data()
    await _delete_tracked_messages(callback.bot, callback.message.chat.id, data)

    await state.clear()
    await callback.message.answer(msg.REJECTED_SILENT.format(sub_id=sub_id))
    await callback.answer()
