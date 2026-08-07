"""Moderator router package: aggregates all moderator sub-routers."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.config import config
from db.models import User
from db.queries import get_submission_by_topic_card_id, get_submission_with_user, get_user_by_id
from filters.is_moderator import IsModerator
from keyboards.moderator import submission_actions_kb
from services import edit_lock, topics
from services.media_append import cancel_append_for_sub
from states.moderator import ModeratorReview, STATE_CATEGORY

from ._helpers import _delete_tracked_messages
from .ban import router as ban_router
from .edit import router as edit_router
from .management import router as management_router, show_moderator_home
from .media import router as media_router
from .moderators import router as moderators_router
from .publish_now import router as publish_now_router
from .reject import router as reject_router
from .recover import router as recover_router
from .review import router as review_router
from .schedule import router as schedule_router
from .submit import router as submit_router
from .unschedule import router as unschedule_router

router = Router()
router.message.filter(IsModerator())
router.callback_query.filter(IsModerator())

# Include tag wizard sub-router (shares parent router filters)
from handlers.tag_wizard import router as tag_wizard_router  # noqa: E402

router.include_router(management_router)
router.include_router(moderators_router)
router.include_router(submit_router)
router.include_router(tag_wizard_router)
router.include_router(recover_router)
router.include_router(ban_router)
router.include_router(review_router)
router.include_router(edit_router)
router.include_router(media_router)
router.include_router(reject_router)
router.include_router(publish_now_router)
router.include_router(unschedule_router)
router.include_router(schedule_router)


@router.callback_query(F.data == "noop")
async def handle_noop(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Inform the moderator who holds the lock on the submission they clicked."""
    lock_owner_text = ""
    if callback.message:
        sub = await get_submission_by_topic_card_id(session, callback.message.message_id)
        if sub is not None:
            active = await edit_lock.get_active_lock(session, "submission", str(sub.id))
            if active:
                owner = await get_user_by_id(session, active.moderator_id)
                if owner and owner.username:
                    owner_name = f"@{owner.username}"
                elif owner:
                    owner_name = owner.full_name
                else:
                    owner_name = f"id:{active.moderator_id}"
                lock_owner_text = msg.LOCK_NOOP_HELD_BY.format(mod=owner_name)
    await callback.answer(lock_owner_text or msg.LOCK_NOOP_GENERIC, show_alert=True)


async def _cancel_management(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    """Release management locks and return to moderator home."""
    mid = db_user.telegram_id
    await edit_lock.release_lock(session, "management", "presets", mid)
    await edit_lock.release_lock(session, "management", "banned", mid)
    await edit_lock.release_lock(session, "management", "moderators", mid)
    await show_moderator_home(message, state)


async def _cancel_viewing_post(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
    sub_id: int | None,
    data: dict,
) -> None:
    """Full close: release submission lock and clear FSM."""
    if sub_id:
        await edit_lock.release_lock(session, "submission", str(sub_id), db_user.id)
        await session.commit()
        sub = await get_submission_with_user(session, sub_id)
        if sub is not None:
            await topics.update_submission_card(message.bot, session, sub)
            await topics.request_topic_title_sync(session, sub.user.id)
    await _delete_tracked_messages(message.bot, message.chat.id, data)
    await state.clear()
    await message.answer(msg.CANCEL_OK)


async def _cancel_substate(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
    sub_id: int,
    data: dict,
) -> None:
    """Revert from a sub-state to viewing_post without releasing the lock."""
    cancel_append_for_sub(sub_id)
    still_mine = await edit_lock.extend_lock(
        session, "submission", str(sub_id), db_user.id,
        ttl_seconds=config.edit_lock_ttl_seconds,
    )
    if not still_mine:
        # Lock was lost — clean up sub-state messages, clear FSM, notify
        await _delete_tracked_messages(
            message.bot, message.chat.id, data,
            skip_keys=frozenset({"media_message_ids", "actions_message_id"}),
        )
        try:
            await message.delete()
        except Exception:
            pass
        await state.clear()
        await message.answer(msg.MODERATOR_LOCK_LOST)
        return
    # Remove sub-state messages; keep media preview and action buttons
    await _delete_tracked_messages(
        message.bot, message.chat.id, data,
        skip_keys=frozenset({"media_message_ids", "actions_message_id"}),
    )
    try:
        await message.delete()
    except Exception:
        pass
    # Re-render the actions keyboard to reflect current submission state
    sub = await get_submission_with_user(session, sub_id)
    if sub is not None and data.get("actions_message_id"):
        try:
            await message.bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=data["actions_message_id"],
                reply_markup=submission_actions_kb(sub_id, sub.status),
            )
        except Exception:
            pass
    await state.set_state(ModeratorReview.viewing_post)
    await state.update_data(
        prompt_message_id=None,
        schedule_message_id=None,
        wizard_message_id=None,
        contact_sub_id=None,
        media_manager_message_id=None,
        media_added_message_ids=[],
    )
    await message.answer(msg.CANCEL_REVERTED)


@router.message(Command("cancel"))
async def cmd_moderator_cancel(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer(msg.CANCEL_NOTHING)
        return

    category = STATE_CATEGORY.get(current)
    data = await state.get_data()
    sub_id = data.get("sub_id")

    if category == "management":
        await _cancel_management(message, session, state, db_user)
        return

    if category == "viewing":
        await _cancel_viewing_post(message, session, state, db_user, sub_id, data)
        return

    if category == "sub" and sub_id:
        await _cancel_substate(message, session, state, db_user, sub_id, data)
        return

    # Fallback for any unrecognised state
    await _delete_tracked_messages(message.bot, message.chat.id, data)
    await state.clear()
    await message.answer(msg.CANCEL_OK)
