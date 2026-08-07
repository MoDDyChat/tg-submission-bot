"""Moderator: review start (deep link /start) and close handlers."""

import asyncio
import time

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.config import config
from core.logging import get_logger
from db.models import User
from db.queries import get_submission_with_user, get_user_by_id
from keyboards.callbacks import SubmissionCB
from services import edit_lock, topics
from states.moderator import ModeratorReview

from ._helpers import TERMINAL_STATUSES, _delete_tracked_messages, _send_submission_view
from .management import show_moderator_home

logger = get_logger(__name__)

router = Router()

# Rendering a submission view is "delete the previously tracked messages, then send
# new ones and record their ids" — and the ids are only known once Telegram has
# accepted an album that may take seconds to upload. Two concurrent renders in the
# same chat would therefore both read an id-less state and leave a duplicate view
# behind, so the whole cleanup+render section is serialized per chat.
_render_locks: dict[int, asyncio.Lock] = {}

# A second deep link that lands within this window of a finished render is a double
# tap on the topic card's lock indicator, not a request to redraw: skip it so the
# view does not visibly flicker. Later re-entries still redraw — that is how a
# moderator recovers a view whose messages are gone.
_RENDER_DEDUP_SECONDS = 10.0


@router.message(CommandStart())
async def cmd_start_review(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    args = message.text.split(maxsplit=1)
    param = args[1] if len(args) > 1 else ""

    if not param.startswith("review_"):
        await show_moderator_home(message, state)
        return

    try:
        sub_id = int(param.removeprefix("review_"))
    except ValueError:
        await show_moderator_home(message, state)
        return

    logger.debug("Модератор открыл пост #%d для ревью", sub_id)
    sub = await get_submission_with_user(session, sub_id)
    if sub is None:
        await message.answer(msg.SUBMISSION_NOT_FOUND)
        return
    if sub.status in TERMINAL_STATUSES:
        await message.answer(msg.SUBMISSION_NOT_AVAILABLE)
        return

    # Acquire edit lock FIRST; only then mutate status
    acquired, lock_owner_id = await edit_lock.acquire_lock(
        session, "submission", str(sub_id), db_user.id,
        ttl_seconds=config.edit_lock_ttl_seconds,
    )
    if not acquired:
        owner = await get_user_by_id(session, lock_owner_id)
        owner_display = (
            f"@{owner.username}" if owner and owner.username
            else (owner.full_name if owner else f"id:{lock_owner_id}")
        )
        await message.answer(
            msg.MODERATOR_POST_LOCKED.format(sub_id=sub_id, mod=owner_display)
        )
        return

    await session.commit()
    await topics.update_submission_card(message.bot, session, sub)
    # Reflect the active lock in the topic title ([РЕДАКТИРУЕТСЯ]) right away.
    await topics.request_topic_title_sync(session, sub.user.id)
    await session.commit()

    lock = _render_locks.setdefault(message.chat.id, asyncio.Lock())
    async with lock:
        data = await state.get_data()
        rendered_at = data.get("view_rendered_at") or 0
        if (
            data.get("sub_id") == sub_id
            and await state.get_state() == ModeratorReview.viewing_post.state
            and time.time() - rendered_at < _RENDER_DEDUP_SECONDS
        ):
            # The lock indicator on the topic card sends its holder back here, and
            # Telegram re-fires the deep link on a double tap — don't redraw.
            logger.debug("Пост #%d только что отрисован — повторный рендер пропущен", sub_id)
            return

        await _delete_tracked_messages(message.bot, message.chat.id, data)
        await state.clear()
        await _send_submission_view(message, session, sub_id, state)
        await state.update_data(view_rendered_at=time.time())


@router.callback_query(SubmissionCB.filter(F.action == "close"))
async def handle_close(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    """Delete all DM preview messages and clear FSM state."""
    data = await state.get_data()
    chat_id = callback.message.chat.id

    # Release the edit lock and notify the topic
    if sub_id := data.get("sub_id"):
        await edit_lock.release_lock(session, "submission", str(sub_id), db_user.id)
        await session.commit()
        sub = await get_submission_with_user(session, sub_id)
        if sub is not None:
            await topics.update_submission_card(callback.bot, session, sub)
            await topics.request_topic_title_sync(session, sub.user.id)

    # Delete all tracked messages (media, prompt, schedule picker)
    # Skip actions_message_id — we delete it via callback.message.delete() below
    await _delete_tracked_messages(
        callback.bot, chat_id, data,
        skip_keys=frozenset({"actions_message_id"}),
    )

    # Always delete the actions message (this very message)
    try:
        await callback.message.delete()
    except Exception:
        pass

    await state.clear()
    await callback.answer()
