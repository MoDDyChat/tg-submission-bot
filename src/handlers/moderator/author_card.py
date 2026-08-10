"""Moderator: author card actions (ban/unban, note, contact) and the /user lookup."""

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.config import config
from core.exceptions import CannotBanModeratorError
from core.logging import get_logger, fmt_user
from db.models import User
from db.queries import (
    ban_user,
    get_user_by_id,
    get_user_by_telegram_id,
    get_user_topic,
    get_users_by_username,
    set_moderator_note,
    unban_user,
)
from handlers.contact import deliver_direct_message
from filters.not_command import NotCommand
from keyboards.callbacks import AuthorCardCB
from keyboards.moderator import author_card_cancel_kb, author_card_kb
from services import admin_notifications, topic_notifications, topics
from services.author_card import build_author_card, request_author_card
from states.moderator import AuthorCard
from utils.formatting import user_mention
from utils.html_entities import get_html_text

logger = get_logger(__name__)

router = Router()

# Ровно та же формула, что и `_chat_short()` в services/topics_queue.py — приватную
# функцию чужого модуля не тащим, дублируем однострочник.
_chat_short = lambda group_id: str(abs(group_id))[3:]  # noqa: E731


async def _load_target(session: AsyncSession, callback: CallbackQuery, callback_data: AuthorCardCB) -> User | None:
    """Resolve the author card's target user, alerting if it's gone."""
    target = await get_user_by_id(session, callback_data.user_id)
    if target is None:
        await callback.answer(msg.USER_LOOKUP_NOT_FOUND, show_alert=True)
        return None
    return target


# ── Note ─────────────────────────────────────────────────────────────

@router.callback_query(AuthorCardCB.filter(F.action == "note"))
async def handle_note_start(
    callback: CallbackQuery,
    callback_data: AuthorCardCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    target = await _load_target(session, callback, callback_data)
    if target is None:
        return

    await state.set_state(AuthorCard.entering_note)
    prompt = await callback.message.answer(
        msg.AUTHOR_CARD_NOTE_PROMPT.format(user=user_mention(target)),
        reply_markup=author_card_cancel_kb(target.id),
    )
    await state.update_data(author_card_user_id=target.id, prompt_message_id=prompt.message_id)
    await callback.answer()


@router.message(AuthorCard.entering_note, F.text, NotCommand())
async def handle_note_text(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    user_id = data.get("author_card_user_id")
    if user_id is None:
        await state.clear()
        return

    note = message.text.strip()
    if note == "-":
        await set_moderator_note(session, user_id, None)
        await message.answer(msg.AUTHOR_CARD_NOTE_CLEARED)
    else:
        # Stored as plain text — services/author_card.py escapes once at render
        # time; storing HTML here would double-escape it there.
        await set_moderator_note(session, user_id, note)
        await message.answer(msg.AUTHOR_CARD_NOTE_SAVED)

    if prompt_id := data.get("prompt_message_id"):
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass

    request_author_card(user_id)
    await state.clear()


# ── Ban ──────────────────────────────────────────────────────────────

@router.callback_query(AuthorCardCB.filter(F.action == "ban"))
async def handle_ban_start(
    callback: CallbackQuery,
    callback_data: AuthorCardCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    target = await _load_target(session, callback, callback_data)
    if target is None:
        return
    if target.is_moderator or target.is_admin:
        await callback.answer(msg.AUTHOR_CARD_CANNOT_BAN_MODERATOR, show_alert=True)
        return
    if target.is_banned:
        await callback.answer(msg.USER_ALREADY_BANNED, show_alert=True)
        return

    await state.set_state(AuthorCard.entering_ban_reason)
    prompt = await callback.message.answer(
        msg.AUTHOR_CARD_BAN_PROMPT.format(user=user_mention(target)),
        reply_markup=author_card_cancel_kb(target.id),
    )
    await state.update_data(author_card_user_id=target.id, prompt_message_id=prompt.message_id)
    await callback.answer()


@router.message(AuthorCard.entering_ban_reason, F.text, NotCommand())
async def handle_ban_reason(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    data = await state.get_data()
    user_id = data.get("author_card_user_id")
    if user_id is None:
        await state.clear()
        return

    reason_html = get_html_text(message)
    try:
        await ban_user(session, user_id, reason_html)
    except CannotBanModeratorError:
        # Race with a concurrent role grant — same handling as handlers/moderator/ban.py.
        await message.answer(msg.AUTHOR_CARD_CANNOT_BAN_MODERATOR)
        await state.clear()
        return

    target = await get_user_by_id(session, user_id)
    display = user_mention(target) if target else f"id:{user_id}"
    logger.info("Пользователь id:%d заблокирован с карточки автора. Причина: %s", user_id, reason_html)

    await admin_notifications.notify_admins(
        message.bot, session,
        actor=db_user,
        action_text=msg.ADMIN_NOTIFY_USER_BANNED.format(
            actor=admin_notifications.actor_display(db_user),
            user=display,
            reason=reason_html,
        ),
    )
    await topics.request_topic_title_sync(session, user_id)
    request_author_card(user_id)

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
        msg.USER_BANNED.format(user=display, reason=reason_html),
        parse_mode="HTML",
    )
    await state.clear()


# ── Unban ────────────────────────────────────────────────────────────

@router.callback_query(AuthorCardCB.filter(F.action == "unban"))
async def handle_unban(
    callback: CallbackQuery,
    callback_data: AuthorCardCB,
    session: AsyncSession,
    db_user: User,
) -> None:
    target = await _load_target(session, callback, callback_data)
    if target is None:
        return

    await unban_user(session, target.id)
    logger.info("Пользователь id:%d разблокирован с карточки автора %s", target.id, fmt_user(db_user))

    await admin_notifications.notify_admins(
        callback.bot, session,
        actor=db_user,
        action_text=msg.ADMIN_NOTIFY_USER_UNBANNED.format(
            actor=admin_notifications.actor_display(db_user),
            user=user_mention(target),
        ),
    )
    await topic_notifications.notify_unbanned(callback.bot, session, target, db_user)
    await topics.request_topic_title_sync(session, target.id)
    request_author_card(target.id)

    await callback.answer(msg.AUTHOR_CARD_UNBANNED.format(user=user_mention(target)), show_alert=True)


# ── Contact (direct message) ────────────────────────────────────────

@router.callback_query(AuthorCardCB.filter(F.action == "contact"))
async def handle_contact_start(
    callback: CallbackQuery,
    callback_data: AuthorCardCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    target = await _load_target(session, callback, callback_data)
    if target is None:
        return

    await state.set_state(AuthorCard.writing_direct_message)
    prompt = await callback.message.answer(
        msg.AUTHOR_CARD_CONTACT_PROMPT.format(user=user_mention(target)),
        reply_markup=author_card_cancel_kb(target.id),
    )
    await state.update_data(author_card_user_id=target.id, prompt_message_id=prompt.message_id)
    await callback.answer()


@router.message(AuthorCard.writing_direct_message, F.text, NotCommand())
async def handle_contact_text(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    data = await state.get_data()
    user_id = data.get("author_card_user_id")
    if user_id is None:
        await state.clear()
        return

    target = await get_user_by_id(session, user_id)
    if target is None:
        await message.answer(msg.USER_LOOKUP_NOT_FOUND)
        await state.clear()
        return

    sent_ok = await deliver_direct_message(
        message.bot, session,
        target=target,
        moderator=db_user,
        text_html=get_html_text(message),
    )
    await message.answer(msg.DIRECT_MESSAGE_SENT if sent_ok else msg.DIRECT_MESSAGE_FAILED)
    await state.clear()


# ── Cancel ───────────────────────────────────────────────────────────

@router.callback_query(AuthorCardCB.filter(F.action == "cancel"))
async def handle_cancel(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        await callback.message.delete()
    except Exception:
        pass
    await state.clear()
    await callback.answer(msg.AUTHOR_CARD_CANCELLED)


# ── /user lookup ─────────────────────────────────────────────────────

@router.message(Command("user"))
async def handle_user_lookup(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
) -> None:
    """Look up an author by Telegram id or @username and post a copy of their card.

    IMPORTANT: the Bot API cannot resolve a bare ``@username`` to a Telegram id,
    so username lookup only works for people who already have a row in ``users``
    (i.e. have written to the bot before). Telegram handles are reused — a
    ``@nick`` can point at two rows in our DB (the old owner and the new one)
    and nothing in the schema says which is current. Rather than guess and risk
    banning/messaging the wrong person, an ambiguous nick is refused and the
    moderator is asked for the numeric Telegram id instead.
    """
    if message.chat.id != config.moderator_group_id:
        return

    arg = (command.args or "").strip()
    if not arg:
        await message.answer(msg.USER_LOOKUP_USAGE, parse_mode="HTML")
        return

    if arg.startswith("@"):
        matches = await get_users_by_username(session, arg.lstrip("@"))
        if len(matches) > 1:
            await message.answer(msg.USER_LOOKUP_AMBIGUOUS, parse_mode="HTML")
            return
        target = matches[0] if matches else None
    elif arg.lstrip("-").isdigit():
        target = await get_user_by_telegram_id(session, int(arg))
    else:
        target = None

    if target is None:
        await message.answer(msg.USER_LOOKUP_NOT_FOUND)
        return

    topic = await get_user_topic(session, target.id)
    if topic is None:
        await message.answer(msg.USER_LOOKUP_NO_TOPIC)
        return

    text = await build_author_card(session, target)
    chat_short = _chat_short(config.moderator_group_id)
    topic_link = msg.AUTHOR_CARD_TOPIC_LINK.format(
        url=f"https://t.me/c/{chat_short}/{topic.topic_id}"
    )
    await message.answer(
        text + topic_link,
        parse_mode="HTML",
        reply_markup=author_card_kb(target),
        disable_web_page_preview=True,
    )
    request_author_card(target.id)
