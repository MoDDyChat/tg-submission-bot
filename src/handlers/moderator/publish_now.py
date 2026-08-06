"""Moderator: publish now (immediate publication) handlers."""

import html
from datetime import datetime, timezone

from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.config import config
from core.exceptions import PublishFailedError, PublishStateUnknownError
from core.logging import get_logger
from db.models import User
from db.queries import (
    create_publication,
    delete_publication,
    get_publication,
    get_submission_with_user,
    update_submission_status,
)
from keyboards.callbacks import ConfirmCB, SubmissionCB
from keyboards.moderator import confirm_publish_now_kb
from services import edit_lock
from services.publisher import publish_post
from services.topics_queue import render_queue as _render_queue
from states.moderator import ModeratorReview
from db.session import session_factory
from utils.formatting import format_publication_summary

from ._helpers import _delete_tracked_messages

logger = get_logger(__name__)

router = Router()
PUBLISHABLE_STATUSES = {"pending"}


@router.callback_query(SubmissionCB.filter(F.action == "publish_now"))
async def handle_publish_now_start(
    callback: CallbackQuery,
    callback_data: SubmissionCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    sub = await get_submission_with_user(session, callback_data.sub_id)
    if sub is None or sub.status not in PUBLISHABLE_STATUSES:
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

    await state.set_state(ModeratorReview.confirm_publish_now)
    await state.update_data(sub_id=callback_data.sub_id)

    tz = ZoneInfo(config.timezone)
    now_local = datetime.now(tz)
    summary = format_publication_summary(sub.caption, now_local, tags=sub.tags)
    schedule_msg = await callback.message.answer(
        msg.CONFIRM_PUBLISH_NOW.format(sub_id=sub.id, summary=summary),
        reply_markup=confirm_publish_now_kb(),
        parse_mode="HTML",
    )
    await state.update_data(schedule_message_id=schedule_msg.message_id)
    await callback.answer()


@router.callback_query(ModeratorReview.confirm_publish_now, ConfirmCB.filter(F.action == "yes"))
async def handle_publish_now_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    data = await state.get_data()
    sub_id = data["sub_id"]

    sub = await get_submission_with_user(session, sub_id)
    if not sub or sub.status not in PUBLISHABLE_STATUSES:
        await callback.answer(msg.SUBMISSION_NOT_AVAILABLE, show_alert=True)
        await _delete_tracked_messages(callback.bot, callback.message.chat.id, data)
        await state.clear()
        return

    # Extend lock before publishing
    still_mine = await edit_lock.extend_lock(
        session, "submission", str(sub_id), db_user.id,
        ttl_seconds=config.edit_lock_ttl_seconds,
    )
    if not still_mine:
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return

    now_utc = datetime.now(timezone.utc)
    pub = await create_publication(session, sub_id, sub.caption, now_utc)
    await update_submission_status(session, sub_id, "scheduled")
    await session.commit()

    try:
        await callback.message.delete()
    except Exception:
        pass

    try:
        await publish_post(
            callback.bot, session_factory,
            pub.id, sub.id, sub.caption,
            actor=db_user,
        )
        logger.info("Пост #%d опубликован немедленно", sub_id)

        # Release lock after successful publish
        await edit_lock.release_lock(session, "submission", str(sub_id), db_user.id)

        await _render_queue(callback.bot, session)

        await _delete_tracked_messages(
            callback.bot, callback.message.chat.id, data,
            skip_keys=frozenset({"schedule_message_id"}),
        )

        await state.clear()
        await callback.message.answer(
            msg.PUBLISHED_NOW_OK.format(sub_id=sub_id),
        )
    except PublishFailedError as exc:
        logger.warning("Не удалось опубликовать пост #%d немедленно: %s", sub_id, exc)

        async with session_factory() as recovery_session:
            fresh_pub = await get_publication(recovery_session, pub.id)
            if fresh_pub and fresh_pub.published_at is None:
                await delete_publication(recovery_session, fresh_pub.id)
                await update_submission_status(recovery_session, sub_id, "pending")
                await recovery_session.commit()

        await state.set_state(ModeratorReview.viewing_post)
        await state.update_data(
            sub_id=sub_id,
            media_message_ids=data.get("media_message_ids", []),
            actions_message_id=data.get("actions_message_id"),
            schedule_message_id=None,
            prompt_message_id=None,
        )
        await callback.message.answer(
            msg.PUBLISH_NOW_FAILED.format(sub_id=sub_id, error=html.escape(str(exc))),
        )
    except PublishStateUnknownError as exc:
        logger.exception(
            "Не удалось надёжно синхронизировать публикацию поста #%d после отправки", sub_id
        )
        async with session_factory() as recovery_session:
            fresh_pub = await get_publication(recovery_session, pub.id)
            if fresh_pub and fresh_pub.published_at is None:
                await delete_publication(recovery_session, fresh_pub.id)
                await update_submission_status(recovery_session, sub_id, "pending")
                await recovery_session.commit()
        await state.set_state(ModeratorReview.viewing_post)
        await state.update_data(
            sub_id=sub_id,
            media_message_ids=data.get("media_message_ids", []),
            actions_message_id=data.get("actions_message_id"),
            schedule_message_id=None,
            prompt_message_id=None,
        )
        await callback.message.answer(
            msg.PUBLISH_NOW_FAILED.format(sub_id=sub_id, error=html.escape(str(exc))),
        )
    except Exception as exc:
        logger.exception("Не удалось опубликовать пост #%d немедленно", sub_id)
        async with session_factory() as recovery_session:
            fresh_pub = await get_publication(recovery_session, pub.id)
            if fresh_pub and fresh_pub.published_at is None:
                await delete_publication(recovery_session, fresh_pub.id)
                await update_submission_status(recovery_session, sub_id, "pending")
                await recovery_session.commit()
        await state.set_state(ModeratorReview.viewing_post)
        await state.update_data(
            sub_id=sub_id,
            media_message_ids=data.get("media_message_ids", []),
            actions_message_id=data.get("actions_message_id"),
            schedule_message_id=None,
            prompt_message_id=None,
        )
        await callback.message.answer(
            msg.PUBLISH_NOW_FAILED.format(sub_id=sub_id, error=html.escape(str(exc))),
        )
    await callback.answer()


@router.callback_query(ModeratorReview.confirm_publish_now, ConfirmCB.filter(F.action == "no"))
async def handle_publish_now_cancel(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    try:
        await callback.message.delete()
    except Exception:
        pass

    data = await state.get_data()
    sub_id = data.get("sub_id")
    if sub_id:
        await edit_lock.extend_lock(
            session, "submission", str(sub_id), db_user.id,
            ttl_seconds=config.edit_lock_ttl_seconds,
        )
    await state.set_state(ModeratorReview.viewing_post)
    await state.update_data(
        sub_id=sub_id,
        media_message_ids=data.get("media_message_ids", []),
        actions_message_id=data.get("actions_message_id"),
        schedule_message_id=None,
        prompt_message_id=None,
    )
    await callback.answer("Отменено.")
