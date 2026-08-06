"""Central submission view renderer for moderator handlers."""

from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InputMediaAnimation,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from db.queries import get_submission_with_user
from keyboards.moderator import submission_actions_kb
from states.moderator import ModeratorReview
from utils.formatting import format_submission_preview

from ._helpers import TERMINAL_STATUSES


async def render_submission_view(
    message: Message,
    session: AsyncSession,
    sub_id: int,
    state: FSMContext,
) -> bool:
    """Send media preview + action buttons, update FSM state.

    Cleans up any previously tracked media messages before sending new ones.
    Returns False if the submission is not found or in a terminal state.
    """
    # Remove old media and action-buttons messages before re-rendering
    data = await state.get_data()
    ids_to_delete = list(data.get("media_message_ids", []))
    if actions_id := data.get("actions_message_id"):
        ids_to_delete.append(actions_id)
    if manager_id := data.get("media_manager_message_id"):
        ids_to_delete.append(manager_id)
    ids_to_delete.extend(data.get("media_added_message_ids") or [])
    for old_id in ids_to_delete:
        try:
            await message.bot.delete_message(message.chat.id, old_id)
        except Exception:
            pass

    sub = await get_submission_with_user(session, sub_id)
    if sub is None or sub.status in TERMINAL_STATUSES:
        await message.answer(msg.POST_NOT_FOUND_OR_CANCELLED)
        return False

    media_list = sub.media
    text = format_submission_preview(
        caption=sub.caption,
        user_full_name=sub.user.full_name,
        username=sub.user.username,
        media_count=len(media_list),
        status=sub.status,
        tags=sub.tags,
    )

    await state.set_state(ModeratorReview.viewing_post)
    await state.update_data(sub_id=sub_id)

    media_message_ids: list[int] = []
    if media_list:
        if len(media_list) == 1:
            m = media_list[0]
            send_fn = {
                "photo": message.answer_photo,
                "video": message.answer_video,
                "animation": message.answer_animation,
                "document": message.answer_document,
            }.get(m.media_type, message.answer_document)
            sent = await send_fn(m.file_id, caption=text, parse_mode="HTML")
            media_message_ids.append(sent.message_id)
        else:
            media_map = {
                "photo": InputMediaPhoto,
                "video": InputMediaVideo,
                "animation": InputMediaAnimation,
                "document": InputMediaDocument,
            }
            group = [
                media_map.get(item.media_type, InputMediaDocument)(
                    media=item.file_id,
                    caption=text if i == 0 else None,
                    parse_mode="HTML" if i == 0 else None,
                )
                for i, item in enumerate(media_list)
            ]
            sent_group = await message.answer_media_group(group)
            media_message_ids.extend(m.message_id for m in sent_group)
    else:
        # Text-only submission: send the preview info as a regular message
        sent = await message.answer(text, parse_mode="HTML")
        media_message_ids.append(sent.message_id)

    await state.update_data(media_message_ids=media_message_ids)
    actions_msg = await message.answer(msg.CHOOSE_ACTION, reply_markup=submission_actions_kb(sub_id, sub.status))
    await state.update_data(
        actions_message_id=actions_msg.message_id,
        media_manager_message_id=None,
        media_added_message_ids=[],
    )
    return True
