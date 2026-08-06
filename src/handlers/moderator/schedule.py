"""Moderator: schedule publication — calendar, time picker, confirm handlers."""

from datetime import datetime, timezone

from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.config import config
from core.logging import get_logger
from db.models import User
from db.queries import (
    create_publication,
    get_publication_by_submission,
    get_submission_with_user,
    update_publication_time,
    update_submission_status,
)
from keyboards.calendar import calendar_kb, hours_kb, minutes_kb
from keyboards.callbacks import CalendarCB, ConfirmCB, SubmissionCB, TimeCB
from keyboards.moderator import confirm_schedule_kb, submission_actions_kb
from services import edit_lock, topic_notifications, topics
from services.scheduler import cancel_scheduled, schedule_post
from services.topics_queue import render_queue as _render_queue
from services.topics_queue import render_schedule as _render_schedule
from states.moderator import ModeratorReview
from utils.formatting import format_publication_summary
from db.session import session_factory

from ._helpers import TERMINAL_STATUSES, _extend_submission_lock_from_state

logger = get_logger(__name__)

router = Router()


@router.callback_query(SubmissionCB.filter(F.action == "schedule"))
async def handle_schedule_start(
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

    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz)

    await state.set_state(ModeratorReview.picking_date)
    await state.update_data(sub_id=callback_data.sub_id)

    schedule_msg = await callback.message.answer(
        msg.PICK_DATE,
        reply_markup=calendar_kb(now.year, now.month, tz),
    )
    await state.update_data(schedule_message_id=schedule_msg.message_id)
    await callback.answer()


@router.callback_query(ModeratorReview.picking_date, CalendarCB.filter(F.action == "day"))
async def handle_calendar_day(
    callback: CallbackQuery,
    callback_data: CalendarCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _extend_submission_lock_from_state(session, state, db_user.id):
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    await state.update_data(
        pub_year=callback_data.year,
        pub_month=callback_data.month,
        pub_day=callback_data.day,
    )
    await state.set_state(ModeratorReview.picking_hour)
    date_str = f"{callback_data.day:02d}.{callback_data.month:02d}.{callback_data.year}"
    await callback.message.edit_text(
        msg.PICK_HOUR.format(date=date_str),
        reply_markup=hours_kb(callback_data.year, callback_data.month, callback_data.day, ZoneInfo(config.timezone)),
    )
    await callback.answer()


@router.callback_query(ModeratorReview.picking_date, CalendarCB.filter(F.action == "prev_month"))
async def handle_calendar_prev(
    callback: CallbackQuery,
    callback_data: CalendarCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _extend_submission_lock_from_state(session, state, db_user.id):
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    year, month = callback_data.year, callback_data.month
    month -= 1
    if month < 1:
        month = 12
        year -= 1
    await callback.message.edit_reply_markup(reply_markup=calendar_kb(year, month, ZoneInfo(config.timezone)))
    await callback.answer()


@router.callback_query(ModeratorReview.picking_date, CalendarCB.filter(F.action == "next_month"))
async def handle_calendar_next(
    callback: CallbackQuery,
    callback_data: CalendarCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _extend_submission_lock_from_state(session, state, db_user.id):
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    year, month = callback_data.year, callback_data.month
    month += 1
    if month > 12:
        month = 1
        year += 1
    await callback.message.edit_reply_markup(reply_markup=calendar_kb(year, month, ZoneInfo(config.timezone)))
    await callback.answer()


@router.callback_query(CalendarCB.filter(F.action == "ignore"))
async def handle_calendar_ignore(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(ModeratorReview.picking_hour, CalendarCB.filter(F.action == "back_to_cal"))
async def handle_hours_back_to_calendar(
    callback: CallbackQuery,
    callback_data: CalendarCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _extend_submission_lock_from_state(session, state, db_user.id):
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    await state.set_state(ModeratorReview.picking_date)
    await callback.message.edit_text(
        msg.PICK_DATE,
        reply_markup=calendar_kb(callback_data.year, callback_data.month, ZoneInfo(config.timezone)),
    )
    await callback.answer()


@router.callback_query(ModeratorReview.picking_minute, CalendarCB.filter(F.action == "back_to_hours"))
async def handle_minutes_back_to_hours(
    callback: CallbackQuery,
    callback_data: CalendarCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _extend_submission_lock_from_state(session, state, db_user.id):
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    date_str = f"{callback_data.day:02d}.{callback_data.month:02d}.{callback_data.year}"
    await state.set_state(ModeratorReview.picking_hour)
    await callback.message.edit_text(
        msg.PICK_HOUR.format(date=date_str),
        reply_markup=hours_kb(callback_data.year, callback_data.month, callback_data.day, ZoneInfo(config.timezone)),
    )
    await callback.answer()


@router.callback_query(ModeratorReview.picking_hour, TimeCB.filter())
async def handle_pick_hour(
    callback: CallbackQuery,
    callback_data: TimeCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _extend_submission_lock_from_state(session, state, db_user.id):
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    await state.update_data(pub_hour=callback_data.hour)
    await state.set_state(ModeratorReview.picking_minute)

    data = await state.get_data()
    date_str = f"{data['pub_day']:02d}.{data['pub_month']:02d}.{data['pub_year']}"
    await callback.message.edit_text(
        msg.PICK_MINUTE.format(date=date_str),
        reply_markup=minutes_kb(callback_data.hour, data["pub_year"], data["pub_month"], data["pub_day"], ZoneInfo(config.timezone)),
    )
    await callback.answer()


@router.callback_query(ModeratorReview.picking_minute, TimeCB.filter())
async def handle_pick_minute(
    callback: CallbackQuery,
    callback_data: TimeCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    data = await state.get_data()
    tz = ZoneInfo(config.timezone)
    publish_at_local = datetime(
        data["pub_year"], data["pub_month"], data["pub_day"],
        callback_data.hour, callback_data.minute,
        tzinfo=tz,
    )

    if not await _extend_submission_lock_from_state(session, state, db_user.id):
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return

    sub = await get_submission_with_user(session, data["sub_id"])
    if sub is None or sub.status in TERMINAL_STATUSES:
        alert_text = (
            msg.POST_CANCELLED_BY_AUTHOR
            if sub and sub.status == "cancelled"
            else msg.SUBMISSION_NOT_AVAILABLE
        )
        await callback.message.edit_text(alert_text)
        await state.clear()
        return

    await state.update_data(pub_minute=callback_data.minute)
    await state.set_state(ModeratorReview.confirm_schedule)

    summary = format_publication_summary(sub.caption, publish_at_local, tags=sub.tags)
    await callback.message.edit_text(
        msg.CONFIRM_SCHEDULE.format(sub_id=sub.id, summary=summary),
        reply_markup=confirm_schedule_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(ModeratorReview.confirm_schedule, ConfirmCB.filter(F.action == "yes"))
async def handle_confirm_yes(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    data = await state.get_data()
    sub_id = data["sub_id"]

    tz = ZoneInfo(config.timezone)
    publish_at_local = datetime(
        data["pub_year"], data["pub_month"], data["pub_day"],
        data["pub_hour"], data["pub_minute"],
        tzinfo=tz,
    )

    now_local = datetime.now(tz)
    if publish_at_local <= now_local:
        await callback.answer(msg.SCHEDULE_TIME_PAST, show_alert=True)
        await state.set_state(ModeratorReview.picking_minute)
        date_str = f"{data['pub_day']:02d}.{data['pub_month']:02d}.{data['pub_year']}"
        await callback.message.edit_text(
            msg.PICK_MINUTE.format(date=date_str),
            reply_markup=minutes_kb(data["pub_hour"], data["pub_year"], data["pub_month"], data["pub_day"], tz),
        )
        return

    # Store and schedule in UTC for consistency
    publish_at_utc = publish_at_local.astimezone(timezone.utc)

    sub = await get_submission_with_user(session, sub_id)
    if sub is None:
        await callback.message.edit_text(msg.SUBMISSION_NOT_AVAILABLE)
        await state.clear()
        await callback.answer()
        return
    if sub.status in TERMINAL_STATUSES:
        alert_text = (
            msg.POST_CANCELLED_BY_AUTHOR
            if sub.status == "cancelled"
            else msg.SUBMISSION_NOT_AVAILABLE
        )
        await callback.message.edit_text(alert_text)
        await state.clear()
        await callback.answer()
        return

    # Reschedule or create publication
    existing_pub = await get_publication_by_submission(session, sub_id)
    is_reschedule = existing_pub is not None
    if existing_pub:
        cancel_scheduled(existing_pub.id)  # Cancel old job before DB update to eliminate race window
        await update_publication_time(session, existing_pub.id, publish_at_utc)
        existing_pub.edited_caption = sub.caption
        pub = existing_pub
    else:
        pub = await create_publication(session, sub_id, sub.caption, publish_at_utc)
    await update_submission_status(session, sub_id, "scheduled")
    sub.status = "scheduled"
    await session.commit()

    # Schedule (or replace) the APScheduler job
    schedule_post(
        pub.id, publish_at_utc, callback.bot, session_factory,
        submission_id=sub.id, edited_caption=sub.caption,
    )

    if is_reschedule:
        await topic_notifications.notify_rescheduled(callback.bot, session, sub, db_user, publish_at_utc)
    else:
        await topic_notifications.notify_scheduled(callback.bot, session, sub, db_user, publish_at_utc)
    await topics.update_submission_card(callback.bot, session, sub)
    await topics.request_topic_title_sync(session, sub.user.id)

    await _render_queue(callback.bot, session)
    await _render_schedule(callback.bot, session)

    time_str = publish_at_local.strftime("%d.%m.%Y %H:%M")
    logger.info("Пост #%d запланирован на %s", sub_id, time_str)

    # Delete the calendar/confirm message
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Update actions keyboard in-place to show scheduled-state buttons
    if actions_id := data.get("actions_message_id"):
        try:
            await callback.bot.edit_message_reply_markup(
                chat_id=callback.message.chat.id,
                message_id=actions_id,
                reply_markup=submission_actions_kb(sub_id, "scheduled"),
            )
        except Exception:
            pass

    # Stay in viewing state so other buttons keep working
    await state.set_state(ModeratorReview.viewing_post)
    await state.update_data(
        sub_id=sub_id,
        media_message_ids=data.get("media_message_ids", []),
        actions_message_id=data.get("actions_message_id"),
        schedule_message_id=None,
        prompt_message_id=None,
    )
    await callback.answer(
        msg.SCHEDULED_OK.format(sub_id=sub_id, time=time_str),
        show_alert=True,
    )


@router.callback_query(ModeratorReview.confirm_schedule, ConfirmCB.filter(F.action == "back"))
async def handle_confirm_back(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    """Go back to minute picking."""
    if not await _extend_submission_lock_from_state(session, state, db_user.id):
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    data = await state.get_data()
    year = data.get("pub_year")
    month = data.get("pub_month")
    day = data.get("pub_day")
    hour = data.get("pub_hour")

    if not all((year, month, day, hour is not None)):
        # Fallback: state data lost, restart from date
        await state.set_state(ModeratorReview.picking_date)
        _tz = ZoneInfo(config.timezone)
        now = datetime.now(_tz)
        await callback.message.edit_text(
            msg.PICK_DATE, reply_markup=calendar_kb(now.year, now.month, _tz)
        )
    else:
        await state.set_state(ModeratorReview.picking_minute)
        date_str = f"{day:02d}.{month:02d}.{year}"
        await callback.message.edit_text(
            msg.PICK_MINUTE.format(date=date_str),
            reply_markup=minutes_kb(hour, year, month, day, ZoneInfo(config.timezone)),
        )
    await callback.answer()


@router.callback_query(ModeratorReview.confirm_schedule, ConfirmCB.filter(F.action == "no"))
async def handle_confirm_no(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    # Delete the calendar/confirm message, extend lock, return to viewing
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
