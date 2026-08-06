"""Moderator ↔ Viewer messaging through the bot."""

import re

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.logging import get_logger, fmt_user
from db.models import User
from db.queries import create_message, get_submission_with_user
from filters.is_moderator import IsModerator
from keyboards.callbacks import SubmissionCB
from services import topic_notifications
from states.contact import ContactViewer
from utils.html_entities import get_html_text

logger = get_logger(__name__)

router = Router()

_SUB_ID_PATTERN = re.compile(msg.VIEWER_REPLY_PATTERN)


class IsModeratorReply(BaseFilter):
    """True when the message is a reply to a bot contact message from the moderator."""

    async def __call__(self, message: Message) -> bool:
        replied = message.reply_to_message
        if not replied:
            return False
        text = replied.text or replied.caption or ""
        return bool(_SUB_ID_PATTERN.search(text))


# ── Moderator initiates contact ───────────────────────────────────

@router.callback_query(SubmissionCB.filter(F.action == "contact"), IsModerator())
async def handle_contact_start(
    callback: CallbackQuery,
    callback_data: SubmissionCB,
    state: FSMContext,
) -> None:
    await state.set_state(ContactViewer.writing_message)
    prompt = await callback.message.answer(
        msg.CONTACT_PROMPT.format(sub_id=callback_data.sub_id)
    )
    await state.update_data(contact_sub_id=callback_data.sub_id, prompt_message_id=prompt.message_id)
    await callback.answer()


@router.message(ContactViewer.writing_message, F.text, IsModerator())
async def handle_moderator_message(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    data = await state.get_data()
    sub_id = data.get("contact_sub_id")
    if sub_id is None:
        await state.clear()
        return

    sub = await get_submission_with_user(session, sub_id)
    if sub is None:
        await message.answer(msg.SUBMISSION_NOT_FOUND)
        await state.clear()
        return

    # Save message to DB
    message_html = get_html_text(message)
    await create_message(session, sub_id, message.from_user.id, message_html)

    # Forward to the viewer
    try:
        await message.bot.send_message(
            sub.user.telegram_id,
            msg.MODERATOR_MESSAGE_TO_VIEWER.format(sub_id=sub_id, text=message_html),
            parse_mode="HTML",
        )
        logger.info(
            "Модератор отправил сообщение автору поста #%d %s",
            sub_id, fmt_user(sub.user),
        )
        await topic_notifications.notify_contact_from_moderator(
            message.bot, session, sub, db_user, message.text
        )
        await message.answer(msg.MESSAGE_SENT)
    except Exception as e:
        logger.warning("Не удалось отправить сообщение пользователю %d: %s", sub.user.telegram_id, e)
        await message.answer(msg.MESSAGE_FAILED)

    await state.clear()


# ── Viewer replies to moderator ───────────────────────────────────

@router.message(IsModeratorReply(), F.text)
async def handle_viewer_reply(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Handle viewer replying with text to a moderator's message."""
    replied = message.reply_to_message
    text = replied.text or replied.caption or ""

    # Extract submission ID from the message
    match = _SUB_ID_PATTERN.search(text)
    if not match:
        return
    try:
        sub_id = int(match.group(1))
    except ValueError:
        return

    sub = await get_submission_with_user(session, sub_id)
    if sub is None or sub.user_id != db_user.id:
        return

    # Save message
    await create_message(session, sub_id, message.from_user.id, message.text)

    # Post to the author's forum topic (replaces DM broadcast to all moderators)
    await topic_notifications.notify_contact_from_viewer(
        message.bot, session, sub, get_html_text(message)
    )

    logger.info("%s ответил модератору по посту #%d", fmt_user(db_user), sub_id)
    await message.answer(msg.REPLY_SENT)


@router.message(IsModeratorReply(), F.photo | F.video | F.animation | F.document)
async def handle_viewer_reply_media(message: Message) -> None:
    """Viewer tried to reply with media — only text replies are supported."""
    await message.answer(msg.REPLY_TEXT_ONLY)
