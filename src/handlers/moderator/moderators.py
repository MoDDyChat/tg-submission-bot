"""Moderator roster management UI — admin-only section.

Entry is gated by ``db_user.is_admin`` inside handlers (the router only
filters ``IsModerator``), mirroring ``handle_recover`` in management.py.
"""

import html
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.config import config
from core.exceptions import (
    CannotChangeOwnRoleError,
    CannotRemoveLastAdminError,
    ConfigProtectedRoleError,
    RoleAlreadyGrantedError,
    RoleChangeError,
    RoleTargetBannedError,
)
from core.logging import get_logger
from db.models import User
from db.queries import (
    get_or_create_user,
    get_user_by_id,
    get_user_by_telegram_id,
    list_moderators,
)
from db.session import session_factory
from keyboards.callbacks import ManagementCB, ModeratorCB
from keyboards.moderator import (
    moderator_add_kb,
    moderator_card_kb,
    moderator_remove_confirm_kb,
    moderator_revoke_confirm_kb,
    moderators_list_kb,
    tag_preset_input_kb,
)
from services import edit_lock, moderator_invites, roles
from states.moderator import ModeratorReview
from utils.formatting import user_mention

from .management import (
    _delete_user_message,
    _guard_management_lock,
    _render_management_message,
    _with_notice,
)

logger = get_logger(__name__)

router = Router()

_INVITE_TTL_HOURS = 24


def _role_error_text(exc: RoleChangeError, target: User) -> str:
    if isinstance(exc, CannotChangeOwnRoleError):
        return msg.MODERATOR_SELF_ACTION_FORBIDDEN
    if isinstance(exc, CannotRemoveLastAdminError):
        return msg.MODERATOR_LAST_ADMIN_FORBIDDEN
    if isinstance(exc, ConfigProtectedRoleError):
        return msg.MODERATOR_CONFIG_PROTECTED.format(user=user_mention(target))
    if isinstance(exc, RoleAlreadyGrantedError):
        return msg.MODERATOR_ALREADY_MODERATOR.format(user=user_mention(target))
    if isinstance(exc, RoleTargetBannedError):
        return msg.MODERATOR_TARGET_BANNED.format(user=user_mention(target))
    return msg.SOMETHING_WENT_WRONG


async def _guard_moderators(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> bool:
    """Admin gate + management-lock guard shared by every roster callback."""
    if not db_user.is_admin:
        await callback.answer(msg.MODERATORS_ADMIN_ONLY, show_alert=True)
        return False
    if not await _guard_management_lock(
        session, "moderators", db_user.telegram_id, config.edit_lock_ttl_seconds,
    ):
        await callback.answer(msg.MANAGEMENT_LOCK_LOST, show_alert=True)
        await state.clear()
        return False
    return True

async def _guard_moderators_message(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> bool:
    """Admin gate + management-lock guard for the roster FSM message handlers.

    The edit lock is not a substitute for authorization: a user whose admin
    rights were revoked while their FSM session stayed alive must not be able
    to finish a privileged action from a text input.
    """
    if not db_user.is_admin:
        await message.answer(msg.MODERATORS_ADMIN_ONLY)
        return False
    if not await _guard_management_lock(
        session, "moderators", db_user.telegram_id, config.edit_lock_ttl_seconds,
    ):
        await message.answer(msg.MANAGEMENT_LOCK_LOST)
        return False
    return True


def _format_moderator_line(user: User) -> str:
    name = html.escape(f"@{user.username}") if user.username else html.escape(user.full_name)
    role = msg.ROLE_LABEL_ADMIN if user.is_admin else msg.ROLE_LABEL_MODERATOR
    badge = ""
    if roles.is_moderator_protected(user) or roles.is_admin_protected(user):
        badge = f" {msg.ROLE_FROM_ENV_BADGE}"
    return f"{name} — {role}{badge}"


def _format_moderators_list_text(users: list[User], *, notice: str | None = None) -> str:
    if not users:
        return _with_notice(msg.MODERATORS_EMPTY_TEXT, notice)
    lines = "\n".join(f"• {_format_moderator_line(user)}" for user in users)
    return _with_notice(f"{msg.MODERATORS_MENU_TEXT}\n\n{lines}", notice)


async def _format_granted_info(session: AsyncSession, user: User) -> str:
    if user.role_granted_at is None and user.role_granted_by is None:
        return msg.MODERATOR_GRANTED_FROM_ENV
    when = ""
    if user.role_granted_at is not None:
        when = user.role_granted_at.astimezone(ZoneInfo(config.timezone)).strftime(
            "%d.%m.%Y %H:%M"
        )
    by = ""
    if user.role_granted_by is not None:
        granter = await get_user_by_id(session, user.role_granted_by)
        by = user_mention(granter) if granter is not None else f"id:{user.role_granted_by}"
    if when and by:
        return msg.MODERATOR_GRANTED_INFO.format(when=when, by=by)
    return msg.MODERATOR_GRANTED_FROM_ENV


async def _format_moderator_card_text(session: AsyncSession, user: User) -> str:
    username = f"@{html.escape(user.username)}" if user.username else "—"
    role = msg.ROLE_LABEL_ADMIN if user.is_admin else msg.ROLE_LABEL_MODERATOR
    granted_info = await _format_granted_info(session, user)
    return msg.MODERATOR_CARD_TEXT.format(
        name=html.escape(user.full_name),
        role=role,
        username=username,
        telegram_id=user.telegram_id,
        granted_info=granted_info,
    )


async def _render_moderators_list(
    bot,
    chat_id: int,
    state: FSMContext,
    session: AsyncSession,
    *,
    message_id: int | None = None,
    notice: str | None = None,
) -> None:
    users = await list_moderators(session)
    await state.set_state(ModeratorReview.management_moderators)
    await _render_management_message(
        bot,
        chat_id,
        state,
        _format_moderators_list_text(users, notice=notice),
        moderators_list_kb(users),
        message_id=message_id,
    )


async def _render_moderator_card(
    bot,
    chat_id: int,
    state: FSMContext,
    session: AsyncSession,
    actor: User,
    user_id: int,
    *,
    message_id: int | None = None,
    confirming: str | None = None,
) -> bool:
    """Render a moderator card, or fall back to the list when the user is gone.

    ``confirming`` is ``None`` (card), ``"remove"`` or ``"revoke_admin"``
    (separate confirmation step, mirroring preset deletion).
    """
    target = await get_user_by_id(session, user_id)
    if target is None or not target.is_moderator:
        await _render_moderators_list(
            bot, chat_id, state, session, message_id=message_id,
        )
        return False

    await state.set_state(ModeratorReview.management_moderator_detail)

    if confirming == "remove":
        text = msg.MODERATOR_REMOVE_CONFIRM_TEXT.format(user=user_mention(target))
        keyboard = moderator_remove_confirm_kb(target.id)
    elif confirming == "revoke_admin":
        text = msg.MODERATOR_REVOKE_ADMIN_CONFIRM_TEXT.format(user=user_mention(target))
        keyboard = moderator_revoke_confirm_kb(target.id)
    else:
        is_self = actor.id == target.id
        keyboard = moderator_card_kb(
            target,
            can_revoke_admin=(
                target.is_admin
                and not roles.is_admin_protected(target)
                and not is_self
            ),
            can_remove=not roles.is_moderator_protected(target) and not is_self,
        )
        text = await _format_moderator_card_text(session, target)

    await _render_management_message(
        bot, chat_id, state, text, keyboard, message_id=message_id,
    )
    return True


async def _render_enter_id_prompt(
    bot,
    chat_id: int,
    state: FSMContext,
    *,
    error: bool = False,
    message_id: int | None = None,
) -> None:
    await state.set_state(ModeratorReview.management_enter_moderator_id)
    await _render_management_message(
        bot,
        chat_id,
        state,
        msg.MODERATOR_INVALID_ID if error else msg.MODERATOR_ENTER_ID_PROMPT,
        tag_preset_input_kb(ModeratorCB(action="add").pack()),
        message_id=message_id,
    )


async def _get_target_moderator(session: AsyncSession, user_id: int) -> User | None:
    target = await get_user_by_id(session, user_id)
    if target is None or not target.is_moderator:
        return None
    return target


@router.callback_query(ManagementCB.filter(F.action == "moderators"))
async def handle_moderators_menu(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not db_user.is_admin:
        await callback.answer(msg.MODERATORS_ADMIN_ONLY, show_alert=True)
        return
    acquired, owner_id = await edit_lock.acquire_lock(
        session, "management", "moderators",
        db_user.telegram_id, ttl_seconds=config.edit_lock_ttl_seconds,
    )
    if not acquired:
        owner = await get_user_by_telegram_id(session, owner_id)
        owner_display = user_mention(owner) if owner else f"id:{owner_id}"
        await callback.answer(
            msg.MANAGEMENT_MODERATORS_LOCKED.format(mod=owner_display),
            show_alert=True,
        )
        return
    await _render_moderators_list(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ModeratorCB.filter(F.action == "list"))
async def handle_moderator_list(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_moderators(callback, session, state, db_user):
        return
    await _render_moderators_list(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ModeratorCB.filter(F.action == "view"))
async def handle_moderator_view(
    callback: CallbackQuery,
    callback_data: ModeratorCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_moderators(callback, session, state, db_user):
        return
    await _render_moderator_card(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        db_user,
        callback_data.user_id,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ModeratorCB.filter(F.action == "add"))
async def handle_moderator_add(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_moderators(callback, session, state, db_user):
        return
    await state.set_state(ModeratorReview.management_moderators)
    await _render_management_message(
        callback.bot,
        callback.message.chat.id,
        state,
        msg.MODERATORS_MENU_TEXT,
        moderator_add_kb(),
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ModeratorCB.filter(F.action == "invite"))
async def handle_moderator_invite(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_moderators(callback, session, state, db_user):
        return
    invite = await moderator_invites.create_invite(
        session, created_by=db_user, ttl_hours=_INVITE_TTL_HOURS,
    )
    # Persist the invite before any Telegram call: the link must only be shown
    # once the token is committed, or a later failure would display a dead link.
    await session.commit()
    bot_username = (await callback.bot.me()).username
    link = moderator_invites.build_invite_link(invite.token, bot_username)
    logger.info("Создана ссылка-приглашение модератора актором id:%d", db_user.id)
    await _render_management_message(
        callback.bot,
        callback.message.chat.id,
        state,
        msg.MODERATOR_INVITE_LINK_TEXT.format(
            link=html.escape(link), ttl_hours=_INVITE_TTL_HOURS,
        ),
        moderator_add_kb(),
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ModeratorCB.filter(F.action == "enter_id"))
async def handle_moderator_enter_id(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_moderators(callback, session, state, db_user):
        return
    await _render_enter_id_prompt(
        callback.bot,
        callback.message.chat.id,
        state,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.message(ModeratorReview.management_enter_moderator_id, F.text)
async def handle_moderator_enter_id_input(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_moderators_message(message, session, state, db_user):
        return
    raw = (message.text or "").strip()
    await _delete_user_message(message)

    try:
        telegram_id = int(raw)
    except ValueError:
        telegram_id = 0
    if telegram_id <= 0:
        await _render_enter_id_prompt(
            message.bot, message.chat.id, state, error=True,
        )
        return

    target = await get_user_by_telegram_id(session, telegram_id)
    if target is None:
        target, _ = await get_or_create_user(
            session,
            telegram_id,
            username=None,
            full_name=msg.ROLE_BOOTSTRAP_PLACEHOLDER_NAME,
        )
    try:
        await roles.grant_moderator(session, message.bot, actor=db_user, target=target)
    except RoleChangeError as exc:
        await message.answer(_role_error_text(exc, target))
        await _render_moderators_list(message.bot, message.chat.id, state, session)
        return
    await session.commit()
    await roles.notify_role_change(
        message.bot, session, actor=db_user, target=target, action="moderator_granted",
    )
    logger.info("Модератор id:%d назначен вручную (telegram_id=%d)", target.id, telegram_id)
    await _render_moderators_list(message.bot, message.chat.id, state, session)


@router.callback_query(ModeratorCB.filter(F.action == "grant_admin"))
async def handle_moderator_grant_admin(
    callback: CallbackQuery,
    callback_data: ModeratorCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_moderators(callback, session, state, db_user):
        return
    target = await _get_target_moderator(session, callback_data.user_id)
    if target is None:
        await _render_moderators_list(
            callback.bot, callback.message.chat.id, state, session,
            message_id=callback.message.message_id,
        )
        await callback.answer()
        return
    try:
        await roles.grant_admin(session, callback.bot, actor=db_user, target=target)
    except RoleChangeError as exc:
        await callback.answer(_role_error_text(exc, target), show_alert=True)
        return
    await session.commit()
    await roles.notify_role_change(
        callback.bot, session, actor=db_user, target=target, action="admin_granted",
    )
    await session.refresh(target)
    logger.info("Права администратора выданы id:%d", target.id)
    await _render_moderator_card(
        callback.bot, callback.message.chat.id, state, session, db_user, target.id,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ModeratorCB.filter(F.action == "revoke_admin"))
async def handle_moderator_revoke_admin(
    callback: CallbackQuery,
    callback_data: ModeratorCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_moderators(callback, session, state, db_user):
        return
    await _render_moderator_card(
        callback.bot, callback.message.chat.id, state, session, db_user,
        callback_data.user_id, message_id=callback.message.message_id,
        confirming="revoke_admin",
    )
    await callback.answer()


@router.callback_query(ModeratorCB.filter(F.action == "confirm_revoke"))
async def handle_moderator_confirm_revoke(
    callback: CallbackQuery,
    callback_data: ModeratorCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_moderators(callback, session, state, db_user):
        return
    target = await _get_target_moderator(session, callback_data.user_id)
    if target is None:
        await _render_moderators_list(
            callback.bot, callback.message.chat.id, state, session,
            message_id=callback.message.message_id,
        )
        await callback.answer()
        return
    try:
        await roles.revoke_admin(session, callback.bot, actor=db_user, target=target)
    except RoleChangeError as exc:
        await callback.answer(_role_error_text(exc, target), show_alert=True)
        return
    await session.commit()
    await roles.notify_role_change(
        callback.bot, session, actor=db_user, target=target, action="admin_revoked",
    )
    await session.refresh(target)
    logger.info("Права администратора сняты с id:%d", target.id)
    await _render_moderator_card(
        callback.bot, callback.message.chat.id, state, session, db_user, target.id,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ModeratorCB.filter(F.action == "remove"))
async def handle_moderator_remove(
    callback: CallbackQuery,
    callback_data: ModeratorCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_moderators(callback, session, state, db_user):
        return
    await _render_moderator_card(
        callback.bot, callback.message.chat.id, state, session, db_user,
        callback_data.user_id, message_id=callback.message.message_id,
        confirming="remove",
    )
    await callback.answer()


@router.callback_query(ModeratorCB.filter(F.action == "confirm_remove"))
async def handle_moderator_confirm_remove(
    callback: CallbackQuery,
    callback_data: ModeratorCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_moderators(callback, session, state, db_user):
        return
    target = await _get_target_moderator(session, callback_data.user_id)
    if target is None:
        await _render_moderators_list(
            callback.bot, callback.message.chat.id, state, session,
            message_id=callback.message.message_id,
        )
        await callback.answer()
        return
    try:
        locks = await roles.remove_moderator(
            session, callback.bot, actor=db_user, target=target,
        )
    except RoleChangeError as exc:
        await callback.answer(_role_error_text(exc, target), show_alert=True)
        return
    await session.commit()
    await roles.repaint_released_cards(callback.bot, session_factory, locks)
    await roles.notify_role_change(
        callback.bot, session, actor=db_user, target=target, action="moderator_removed",
    )
    logger.info("Снята роль модератора с id:%d", target.id)
    await _render_moderators_list(
        callback.bot, callback.message.chat.id, state, session,
        message_id=callback.message.message_id,
    )
    await callback.answer()
