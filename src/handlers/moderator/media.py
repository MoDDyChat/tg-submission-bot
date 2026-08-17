"""Moderator: media editing handlers (add/delete media)."""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.config import config
from core.logging import get_logger
from db.models import User
from db.queries import get_submission_with_user, get_submission_media, delete_media
from filters.not_command import NotCommand
from keyboards.callbacks import SubmissionCB, MediaCB
from keyboards.moderator import media_manager_kb
from services import edit_lock, topics, media_append
from services.media_append import AppendResult
from services import topic_notifications
from states.moderator import ModeratorReview
from utils.media import extract_media_info
from utils.formatting import format_media_manager_text
from ._helpers import TERMINAL_STATUSES, render_submission_view

logger = get_logger(__name__)

router = Router()


def _media_signature(media: list) -> list[int]:
    return sorted(m.id for m in media)


@router.callback_query(SubmissionCB.filter(F.action == "edit_media"))
async def handle_edit_media_open(
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
    media_append.clear_append_cancel(sub_id)
    still_mine = await edit_lock.extend_lock(
        session, "submission", str(sub_id), db_user.id,
        ttl_seconds=config.edit_lock_ttl_seconds,
    )
    if not still_mine:
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    if await state.get_state() == ModeratorReview.editing_media:
        data = await state.get_data()
        old_manager_id = data.get("media_manager_message_id")
        if old_manager_id:
            try:
                await callback.bot.delete_message(callback.message.chat.id, old_manager_id)
            except Exception:
                pass
            await state.update_data(media_manager_message_id=None)
    media = sub.media
    await state.update_data(
        sub_id=sub_id,
        media_sig_open=_media_signature(media),
        media_added_message_ids=[],
    )
    manager_msg = await callback.message.answer(
        format_media_manager_text(sub_id, media),
        reply_markup=media_manager_kb(sub_id, media),
        parse_mode="HTML",
    )
    await state.update_data(media_manager_message_id=manager_msg.message_id)
    await state.set_state(ModeratorReview.editing_media)
    await callback.answer()


@router.callback_query(MediaCB.filter(F.action == "delete"))
async def handle_media_delete(
    callback: CallbackQuery,
    callback_data: MediaCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    sub_id = callback_data.sub_id
    still_mine = await edit_lock.extend_lock(
        session, "submission", str(sub_id), db_user.id,
        ttl_seconds=config.edit_lock_ttl_seconds,
    )
    if not still_mine:
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    media = await get_submission_media(session, sub_id)
    if len(media) <= 1:
        await callback.answer(msg.MEDIA_DELETE_LAST_FORBIDDEN, show_alert=True)
        return
    await delete_media(session, callback_data.media_id, sub_id)
    await session.commit()
    media = await get_submission_media(session, sub_id)
    await callback.message.edit_text(
        format_media_manager_text(sub_id, media),
        reply_markup=media_manager_kb(sub_id, media),
        parse_mode="HTML",
    )
    await callback.answer(msg.MEDIA_DELETED)


@router.callback_query(MediaCB.filter(F.action == "add"))
async def handle_media_add_start(
    callback: CallbackQuery,
    callback_data: MediaCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    sub_id = callback_data.sub_id
    still_mine = await edit_lock.extend_lock(
        session, "submission", str(sub_id), db_user.id,
        ttl_seconds=config.edit_lock_ttl_seconds,
    )
    if not still_mine:
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    await state.set_state(ModeratorReview.adding_media)
    await state.update_data(sub_id=sub_id)
    prompt = await callback.message.answer(msg.MEDIA_ADD_PROMPT)
    await state.update_data(prompt_message_id=prompt.message_id)
    await callback.answer()


@router.message(
    F.media_group_id,
    F.photo | F.video | F.animation | F.document,
    ModeratorReview.adding_media,
)
async def handle_adding_media_group(
    message: Message,
    state: FSMContext,
    db_user: User,
) -> None:
    data = await state.get_data()
    sub_id = data["sub_id"]
    await media_append.buffer_append_media_group(message, sub_id, db_user.id)


@router.message(
    ~F.media_group_id,
    F.photo | F.video | F.animation | F.document,
    ModeratorReview.adding_media,
)
async def handle_adding_single_media(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    data = await state.get_data()
    sub_id = data["sub_id"]
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
        await message.answer(msg.POST_NOT_FOUND_OR_CANCELLED)
        return
    info = extract_media_info(message)
    if info is None:
        await message.answer(msg.MEDIA_UNSUPPORTED)
        return
    result, count = await media_append.append_media_to_submission(session, sub, [info])
    if result == AppendResult.OK:
        await session.commit()
        resp = await message.answer(msg.MEDIA_ADDED.format(count=count))
        ids = data.get("media_added_message_ids") or []
        ids.append(resp.message_id)
        await state.update_data(media_added_message_ids=ids)
    elif result == AppendResult.INVALID_COMPOSITION:
        await message.answer(msg.MEDIA_COMPOSITION_INVALID)
    elif result == AppendResult.CAPTION_TOO_LONG:
        await message.answer(msg.MEDIA_CAPTION_TOO_LONG_FOR_MEDIA)
    elif result == AppendResult.UNSUPPORTED:
        await message.answer(msg.MEDIA_UNSUPPORTED)


@router.message(ModeratorReview.adding_media, NotCommand())
async def handle_adding_media_unexpected(message: Message) -> None:
    await message.answer(msg.MEDIA_ADDING_EXPECT_MEDIA)


@router.callback_query(
    StateFilter(ModeratorReview.editing_media, ModeratorReview.adding_media),
    MediaCB.filter(F.action == "done"),
)
async def handle_media_done(
    callback: CallbackQuery,
    callback_data: MediaCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    sub_id = callback_data.sub_id
    still_mine = await edit_lock.extend_lock(
        session, "submission", str(sub_id), db_user.id,
        ttl_seconds=config.edit_lock_ttl_seconds,
    )
    if not still_mine:
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    await session.commit()
    data = await state.get_data()
    if "media_sig_open" not in data:
        await callback.answer()
        return
    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        try:
            await callback.bot.delete_message(callback.message.chat.id, prompt_id)
        except Exception:
            pass
        await state.update_data(prompt_message_id=None)
    await media_append.wait_for_pending_append(sub_id)
    sub = await get_submission_with_user(session, sub_id)
    if sub is None or sub.status in TERMINAL_STATUSES:
        await render_submission_view(callback.message, session, sub_id, state)
        await callback.answer()
        return
    changed = _media_signature(sub.media) != data.get("media_sig_open", [])
    if changed:
        try:
            media_ids, card_id = await topics.repost_submission_card(
                callback.bot, session, sub
            )
            # Тот же хелпер, что и на приёме/восстановлении: если коммит ID
            # замены упадёт, живой блок в теме удаляется, иначе recover
            # прислал бы дубль.
            await topics.commit_or_delete_delivered(
                session, callback.bot, [*media_ids, card_id], sub_id
            )
            await topics.update_submission_card(callback.bot, session, sub)
            await topic_notifications.notify_media_changed(callback.bot, session, sub, db_user)
        except Exception:
            logger.exception("Ошибка при repost карточки (пост #%d)", sub_id)
            await session.rollback()
        try:
            await topics.request_topic_title_sync(session, sub.user.id)
        except Exception:
            logger.exception("Ошибка при постановке title sync в очередь (пост #%d)", sub_id)
    await render_submission_view(callback.message, session, sub_id, state)
    await callback.answer()
