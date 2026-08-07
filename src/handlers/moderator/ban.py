"""Moderator: ban and unban user handlers."""

import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.config import config
from core.exceptions import CannotBanModeratorError
from core.logging import get_logger
from db.models import User
from db.queries import ban_user, get_submission_with_user
from keyboards.callbacks import SubmissionCB
from services import admin_notifications, edit_lock, topic_notifications, topics
from states.moderator import ModeratorReview
from utils.html_entities import get_html_text

from ._helpers import _send_submission_view

logger = get_logger(__name__)

router = Router()


@router.callback_query(SubmissionCB.filter(F.action == "ban_author"))
async def handle_ban_author_start(
    callback: CallbackQuery,
    callback_data: SubmissionCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    sub = await get_submission_with_user(session, callback_data.sub_id)
    if sub is None:
        await callback.answer(msg.SUBMISSION_NOT_FOUND, show_alert=True)
        return
    if sub.user.is_moderator or sub.user.is_admin:
        await callback.answer(msg.CANNOT_BAN_MODERATOR, show_alert=True)
        return
    if sub.user.is_banned:
        await callback.answer(msg.USER_ALREADY_BANNED, show_alert=True)
        return

    await state.set_state(ModeratorReview.entering_ban_reason)
    prompt = await callback.message.answer(
        msg.BAN_REASON_PROMPT.format(sub_id=callback_data.sub_id)
    )
    await state.update_data(
        sub_id=callback_data.sub_id,
        ban_user_id=sub.user.id,
        ban_user_display=f"@{sub.user.username}" if sub.user.username else sub.user.full_name,
        prompt_message_id=prompt.message_id,
    )
    await callback.answer()


@router.message(ModeratorReview.entering_ban_reason, F.text)
async def handle_ban_reason(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    data = await state.get_data()
    user_id = data["ban_user_id"]
    user_display = data.get("ban_user_display", f"id:{user_id}")
    reason = message.text
    reason_html = get_html_text(message)

    sub_id = data.get("sub_id")
    sub = None
    if sub_id:
        ok = await edit_lock.extend_lock(
            session, "submission", str(sub_id),
            db_user.id, ttl_seconds=config.edit_lock_ttl_seconds,
        )
        if not ok:
            await message.answer(msg.MODERATOR_LOCK_LOST)
            await state.clear()
            return

        sub = await get_submission_with_user(session, sub_id)
        if sub is not None and (sub.user.is_moderator or sub.user.is_admin):
            await message.answer(msg.BAN_TARGET_IS_MODERATOR)
            return

    try:
        await ban_user(session, user_id, reason)
    except CannotBanModeratorError:
        # Lost the race against a concurrent role grant: the row lock re-check
        # inside ban_user caught a moderator/admin flag the pre-check missed.
        await message.answer(msg.BAN_TARGET_IS_MODERATOR)
        return
    logger.info("Пользователь id:%d заблокирован. Причина: %s", user_id, reason)

    await admin_notifications.notify_admins(
        message.bot, session,
        actor=db_user,
        action_text=msg.ADMIN_NOTIFY_USER_BANNED.format(
            actor=admin_notifications.actor_display(db_user),
            user=html.escape(user_display),
            reason=reason_html,
        ),
    )

    if sub is not None:
        await topic_notifications.notify_banned(message.bot, session, sub, db_user, reason)
        await topics.request_topic_title_sync(session, sub.user.id)

    if prompt_id := data.get("prompt_message_id"):
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass

    await message.answer(
        msg.USER_BANNED.format(user=html.escape(user_display), reason=reason_html),
        parse_mode="HTML",
    )

    if sub_id:
        await _send_submission_view(message, session, sub_id, state)
    else:
        await state.clear()
