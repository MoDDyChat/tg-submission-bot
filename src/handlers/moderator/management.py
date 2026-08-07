"""Moderator home screen and global management UI."""

import asyncio
import html

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.logging import get_logger
from db.models import TagPresetEntry, TagPresetSection, User
from db.queries import (
    create_tag_preset,
    create_tag_preset_section,
    delete_tag_preset,
    delete_tag_preset_section,
    find_tag_preset_conflicts,
    get_active_submissions,
    get_banned_users,
    get_tag_preset,
    get_tag_preset_section,
    get_tag_preset_section_by_label,
    get_user_by_id,
    get_user_by_telegram_id,
    list_tag_preset_sections,
    list_tag_presets,
    list_tag_presets_grouped,
    unban_user,
    update_tag_preset,
    update_tag_preset_section,
)
from keyboards.callbacks import ManagementCB, TagPresetCB, UnbanCB
from keyboards.moderator import (
    banned_users_kb,
    management_back_kb,
    management_menu_kb,
    moderator_home_kb,
    tag_preset_input_kb,
    tag_preset_item_kb,
    tag_preset_section_delete_kb,
    tag_preset_sections_kb,
    tag_presets_list_kb,
)
from services import admin_notifications, edit_lock, topic_notifications, topics
from services.author_card import request_author_card
from states.moderator import ModeratorReview
from db.session import session_factory
from utils.formatting import user_mention

from ._helpers import _delete_tracked_messages
from .recover import recover_missing_posts

logger = get_logger(__name__)

router = Router()

# ── Recover background task lifecycle ──────────────────────────────
# A Recover run is a long background task; only one may be active at a time,
# otherwise two admins or two fast clicks could race and corrupt card IDs.
_recover_lock: asyncio.Lock | None = None
_recover_task: asyncio.Task | None = None


def _get_recover_lock() -> asyncio.Lock:
    """Return the Recover lock, creating it lazily on the current event loop."""
    global _recover_lock
    if _recover_lock is None:
        _recover_lock = asyncio.Lock()
    return _recover_lock


def _on_recover_done(task: asyncio.Task) -> None:
    """Log the outcome of a Recover task and free the module-level slot."""
    global _recover_task
    if _recover_task is task:
        _recover_task = None
    if task.cancelled():
        logger.info("Recover постов отменён")
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Recover постов завершился с ошибкой", exc_info=exc)


async def cancel_recover_task() -> None:
    """Cancel a running Recover background task and await its unwinding.

    The task may be mid-transaction: only after it has actually finished
    unwinding can the DB engine and bot session be shut down safely.
    """
    task = _recover_task
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


_PRESETS_LOCK_TTL = 600  # 10 minutes
_BANNED_LOCK_TTL = 600


async def _guard_management_lock(
    session: AsyncSession,
    resource_id: str,
    moderator_id: int,
    ttl: int,
) -> bool:
    """Extend a management lock. Returns True if still held, False if expired/lost."""
    return await edit_lock.extend_lock(
        session, "management", resource_id, moderator_id, ttl_seconds=ttl,
    )


def _with_notice(text: str, notice: str | None = None) -> str:
    if not notice:
        return text
    return f"{notice}\n\n{text}"


def _normalize_label(raw_text: str) -> str:
    return " ".join(raw_text.split())


def _normalize_section_label(raw_text: str) -> str:
    return _normalize_label(raw_text)


def _normalize_tag(raw_text: str) -> str:
    return raw_text.strip().lstrip("#")


def _is_valid_tag(tag: str) -> bool:
    return bool(tag) and "|" not in tag and not any(char.isspace() for char in tag)


def _section_back_callback_data(section_key: str) -> str:
    return TagPresetCB(action="section", preset_type=section_key).pack()


def _preset_back_callback_data(section_key: str, preset_id: int) -> str:
    return TagPresetCB(action="view", preset_type=section_key, preset_id=preset_id).pack()


def _parse_new_preset(raw_text: str) -> tuple[str | None, str | None, str | None]:
    value = raw_text.strip()
    if not value:
        return None, None, msg.TAG_PRESET_INVALID_FORMAT

    if "|" in value:
        raw_label, raw_tag = value.split("|", 1)
        label = _normalize_label(raw_label)
        tag = _normalize_tag(raw_tag)
    else:
        tag = _normalize_tag(value)
        label = tag

    if not label:
        return None, None, msg.TAG_PRESET_EMPTY_LABEL
    if not tag:
        return None, None, msg.TAG_PRESET_EMPTY_TAG
    if not _is_valid_tag(tag):
        return None, None, msg.TAG_PRESET_INVALID_TAG
    return label, tag, None


def _format_preset_section_text(
    section: TagPresetSection,
    presets: list[TagPresetEntry],
    *,
    notice: str | None = None,
) -> str:
    base_text = (
        msg.TAG_PRESET_SECTION_TEXT.format(section=html.escape(section.label), count=len(presets))
        if presets
        else msg.TAG_PRESET_SECTION_EMPTY_TEXT.format(section=html.escape(section.label))
    )
    return _with_notice(base_text, notice)


def _format_preset_item_text(
    section: TagPresetSection,
    preset: TagPresetEntry,
    *,
    notice: str | None = None,
) -> str:
    base_text = msg.TAG_PRESET_ITEM_TEXT.format(
        section=html.escape(section.label),
        label=html.escape(preset.label),
        tag=html.escape(preset.tag),
    )
    return _with_notice(base_text, notice)


def _format_section_delete_text(
    section: TagPresetSection,
    presets_count: int,
    *,
    notice: str | None = None,
) -> str:
    base_text = msg.TAG_PRESET_SECTION_DELETE_CONFIRM_TEXT.format(
        section=html.escape(section.label),
        count=presets_count,
    )
    return _with_notice(base_text, notice)


async def _render_management_message(
    bot,
    chat_id: int,
    state: FSMContext,
    text: str,
    reply_markup,
    *,
    message_id: int | None = None,
) -> int:
    data = await state.get_data()
    target_message_id = message_id or data.get("management_message_id")

    if target_message_id is not None:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=target_message_id,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            await state.update_data(management_message_id=target_message_id)
            return target_message_id
        except TelegramBadRequest as exc:
            error_text = str(exc).lower()
            if "message is not modified" in error_text:
                await state.update_data(management_message_id=target_message_id)
                return target_message_id
            if "message to edit not found" not in error_text:
                logger.warning("Не удалось обновить management message: %s", exc)

    sent = await bot.send_message(
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
    await state.update_data(management_message_id=sent.message_id)
    return sent.message_id


async def _render_home(
    bot,
    chat_id: int,
    state: FSMContext,
    *,
    text: str | None = None,
    message_id: int | None = None,
) -> None:
    await state.set_state(ModeratorReview.management_home)
    await _render_management_message(
        bot,
        chat_id,
        state,
        text or msg.MODERATOR_HOME,
        moderator_home_kb(),
        message_id=message_id,
    )


async def _render_management_menu(
    bot,
    chat_id: int,
    state: FSMContext,
    *,
    is_admin: bool = False,
    message_id: int | None = None,
) -> None:
    await state.set_state(ModeratorReview.management_menu)
    await _render_management_message(
        bot,
        chat_id,
        state,
        msg.MANAGEMENT_MENU_TEXT,
        management_menu_kb(is_admin=is_admin),
        message_id=message_id,
    )


async def _render_preset_sections(
    bot,
    chat_id: int,
    state: FSMContext,
    session: AsyncSession,
    *,
    message_id: int | None = None,
    notice: str | None = None,
) -> None:
    sections = await list_tag_preset_sections(session)
    grouped = await list_tag_presets_grouped(session)
    counts = {section_key: len(items) for section_key, items in grouped.items()}

    await state.set_state(ModeratorReview.management_presets)
    await state.update_data(management_preset_type=None, management_preset_id=None)
    await _render_management_message(
        bot,
        chat_id,
        state,
        _with_notice(msg.TAG_PRESETS_MENU_TEXT, notice),
        tag_preset_sections_kb(sections, counts),
        message_id=message_id,
    )


async def _render_preset_list(
    bot,
    chat_id: int,
    state: FSMContext,
    session: AsyncSession,
    preset_type: str,
    *,
    message_id: int | None = None,
    notice: str | None = None,
) -> None:
    section = await get_tag_preset_section(session, preset_type)
    if section is None:
        await _render_preset_sections(
            bot,
            chat_id,
            state,
            session,
            message_id=message_id,
            notice=msg.TAG_PRESET_SECTION_NOT_FOUND,
        )
        return

    presets = await list_tag_presets(session, preset_type)
    await state.set_state(ModeratorReview.management_preset_section)
    await state.update_data(
        management_preset_type=preset_type,
        management_preset_id=None,
    )
    await _render_management_message(
        bot,
        chat_id,
        state,
        _format_preset_section_text(section, presets, notice=notice),
        tag_presets_list_kb(preset_type, presets),
        message_id=message_id,
    )


async def _render_preset_item(
    bot,
    chat_id: int,
    state: FSMContext,
    session: AsyncSession,
    preset_type: str,
    preset_id: int,
    *,
    message_id: int | None = None,
    notice: str | None = None,
    confirming_delete: bool = False,
) -> bool:
    preset = await get_tag_preset(session, preset_id)
    if preset is None or preset.preset_type != preset_type:
        await _render_preset_list(
            bot,
            chat_id,
            state,
            session,
            preset_type,
            message_id=message_id,
            notice=msg.TAG_PRESET_NOT_FOUND,
        )
        return False

    section = await get_tag_preset_section(session, preset_type)
    if section is None:
        await _render_preset_sections(
            bot,
            chat_id,
            state,
            session,
            message_id=message_id,
            notice=msg.TAG_PRESET_SECTION_NOT_FOUND,
        )
        return False

    await state.set_state(ModeratorReview.management_preset_detail)
    await state.update_data(
        management_preset_type=preset_type,
        management_preset_id=preset.id,
    )

    text = (
        msg.TAG_PRESET_DELETE_CONFIRM_TEXT.format(
            section=html.escape(section.label),
            label=html.escape(preset.label),
            tag=html.escape(preset.tag),
        )
        if confirming_delete
        else _format_preset_item_text(section, preset, notice=notice)
    )
    await _render_management_message(
        bot,
        chat_id,
        state,
        text,
        tag_preset_item_kb(
            preset_type,
            preset.id,
            confirming_delete=confirming_delete,
        ),
        message_id=message_id,
    )
    return True


async def _render_section_delete_prompt(
    bot,
    chat_id: int,
    state: FSMContext,
    session: AsyncSession,
    preset_type: str,
    *,
    message_id: int | None = None,
    notice: str | None = None,
) -> None:
    section = await get_tag_preset_section(session, preset_type)
    if section is None:
        await _render_preset_sections(
            bot,
            chat_id,
            state,
            session,
            message_id=message_id,
            notice=msg.TAG_PRESET_SECTION_NOT_FOUND,
        )
        return

    presets = await list_tag_presets(session, preset_type)
    await state.set_state(ModeratorReview.management_preset_section)
    await state.update_data(management_preset_type=preset_type, management_preset_id=None)
    await _render_management_message(
        bot,
        chat_id,
        state,
        _format_section_delete_text(section, len(presets), notice=notice),
        tag_preset_section_delete_kb(preset_type),
        message_id=message_id,
    )


async def _render_banned_users(
    bot,
    chat_id: int,
    state: FSMContext,
    session: AsyncSession,
    *,
    message_id: int | None = None,
    notice: str | None = None,
) -> None:
    users = await get_banned_users(session)
    await state.set_state(ModeratorReview.management_banned_users)
    if users:
        text = _with_notice(msg.MANAGEMENT_BANNED_USERS_TEXT, notice)
        keyboard = banned_users_kb(users)
    else:
        text = _with_notice(msg.MANAGEMENT_NO_BANNED_USERS_TEXT, notice)
        keyboard = management_back_kb("menu")

    await _render_management_message(
        bot,
        chat_id,
        state,
        text,
        keyboard,
        message_id=message_id,
    )


async def _render_add_label_prompt(
    bot,
    chat_id: int,
    state: FSMContext,
    section: TagPresetSection,
    *,
    message_id: int | None = None,
    notice: str | None = None,
) -> None:
    await state.set_state(ModeratorReview.management_add_preset_label)
    await state.update_data(management_preset_type=section.key, management_preset_id=None)
    await _render_management_message(
        bot,
        chat_id,
        state,
        _with_notice(
            msg.TAG_PRESET_ADD_PROMPT.format(section=html.escape(section.label)),
            notice,
        ),
        tag_preset_input_kb(_section_back_callback_data(section.key)),
        message_id=message_id,
    )


async def _render_add_section_prompt(
    bot,
    chat_id: int,
    state: FSMContext,
    *,
    message_id: int | None = None,
    notice: str | None = None,
) -> None:
    await state.set_state(ModeratorReview.management_add_section_label)
    await state.update_data(management_preset_type=None, management_preset_id=None)
    await _render_management_message(
        bot,
        chat_id,
        state,
        _with_notice(
            msg.TAG_PRESET_SECTION_ADD_PROMPT,
            notice,
        ),
        tag_preset_input_kb(ManagementCB(action="presets").pack()),
        message_id=message_id,
    )


async def _render_edit_section_prompt(
    bot,
    chat_id: int,
    state: FSMContext,
    section: TagPresetSection,
    *,
    message_id: int | None = None,
    notice: str | None = None,
) -> None:
    await state.set_state(ModeratorReview.management_edit_section_label)
    await state.update_data(management_preset_type=section.key, management_preset_id=None)
    await _render_management_message(
        bot,
        chat_id,
        state,
        _with_notice(
            msg.TAG_PRESET_SECTION_EDIT_PROMPT.format(section=html.escape(section.label)),
            notice,
        ),
        tag_preset_input_kb(_section_back_callback_data(section.key)),
        message_id=message_id,
    )


async def _render_edit_label_prompt(
    bot,
    chat_id: int,
    state: FSMContext,
    preset: TagPresetEntry,
    section: TagPresetSection,
    *,
    message_id: int | None = None,
    notice: str | None = None,
) -> None:
    await state.set_state(ModeratorReview.management_edit_preset_label)
    await state.update_data(
        management_preset_type=preset.preset_type,
        management_preset_id=preset.id,
    )
    await _render_management_message(
        bot,
        chat_id,
        state,
        _with_notice(
            msg.TAG_PRESET_EDIT_LABEL_PROMPT.format(
                section=html.escape(section.label),
                label=html.escape(preset.label),
            ),
            notice,
        ),
        tag_preset_input_kb(_preset_back_callback_data(preset.preset_type, preset.id)),
        message_id=message_id,
    )


async def _render_edit_tag_prompt(
    bot,
    chat_id: int,
    state: FSMContext,
    preset: TagPresetEntry,
    section: TagPresetSection,
    *,
    message_id: int | None = None,
    notice: str | None = None,
) -> None:
    await state.set_state(ModeratorReview.management_edit_preset_tag)
    await state.update_data(
        management_preset_type=preset.preset_type,
        management_preset_id=preset.id,
    )
    await _render_management_message(
        bot,
        chat_id,
        state,
        _with_notice(
            msg.TAG_PRESET_EDIT_TAG_PROMPT.format(
                section=html.escape(section.label),
                tag=html.escape(preset.tag),
            ),
            notice,
        ),
        tag_preset_input_kb(_preset_back_callback_data(preset.preset_type, preset.id)),
        message_id=message_id,
    )


async def _validate_preset_values(
    session: AsyncSession,
    preset_type: str,
    *,
    label: str,
    tag: str,
    exclude_id: int | None = None,
) -> str | None:
    conflicts = await find_tag_preset_conflicts(
        session,
        preset_type,
        label=label,
        tag=tag,
        exclude_id=exclude_id,
    )
    if any(item.label == label for item in conflicts):
        return msg.TAG_PRESET_DUPLICATE_LABEL
    if any(item.tag == tag for item in conflicts):
        return msg.TAG_PRESET_DUPLICATE_TAG
    return None


async def _delete_user_message(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def show_moderator_home(
    message: Message,
    state: FSMContext,
    *,
    text: str | None = None,
) -> None:
    data = await state.get_data()
    await _delete_tracked_messages(message.bot, message.chat.id, data)
    await state.clear()
    await _render_home(message.bot, message.chat.id, state, text=text)


@router.message(Command("help"))
async def cmd_moderator_help(message: Message, state: FSMContext) -> None:
    await show_moderator_home(message, state, text=msg.MODERATOR_HELP)


@router.callback_query(ManagementCB.filter(F.action == "home"))
async def handle_home(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    mid = db_user.telegram_id
    await edit_lock.release_lock(session, "management", "presets", mid)
    await edit_lock.release_lock(session, "management", "banned", mid)
    await edit_lock.release_lock(session, "management", "moderators", mid)
    await _render_home(
        callback.bot,
        callback.message.chat.id,
        state,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ManagementCB.filter(F.action == "menu"))
async def handle_management_menu(
    callback: CallbackQuery,
    state: FSMContext,
    db_user: User,
) -> None:
    await _render_management_menu(
        callback.bot,
        callback.message.chat.id,
        state,
        is_admin=db_user.is_admin,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ManagementCB.filter(F.action == "presets"))
async def handle_presets_menu(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    acquired, owner_id = await edit_lock.acquire_lock(
        session, "management", "presets",
        db_user.telegram_id, ttl_seconds=_PRESETS_LOCK_TTL,
    )
    if not acquired:
        owner = await get_user_by_telegram_id(session, owner_id)
        owner_display = user_mention(owner) if owner else f"id:{owner_id}"
        await callback.answer(
            msg.MANAGEMENT_PRESET_LOCKED.format(mod=owner_display),
            show_alert=True,
        )
        return
    await _render_preset_sections(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ManagementCB.filter(F.action == "banned"))
async def handle_banned_users(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    acquired, owner_id = await edit_lock.acquire_lock(
        session, "management", "banned",
        db_user.telegram_id, ttl_seconds=_BANNED_LOCK_TTL,
    )
    if not acquired:
        owner = await get_user_by_telegram_id(session, owner_id)
        owner_display = user_mention(owner) if owner else f"id:{owner_id}"
        await callback.answer(
            msg.MANAGEMENT_BANNED_LOCKED.format(mod=owner_display),
            show_alert=True,
        )
        return
    await _render_banned_users(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ManagementCB.filter(F.action == "recover"))
async def handle_recover(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    global _recover_task
    if not db_user.is_admin:
        await callback.answer(msg.RECOVER_ADMIN_ONLY, show_alert=True)
        return
    # Check-then-set must be atomic: two simultaneous callbacks could both see
    # a free slot and start two runs otherwise (the inner lock only serialises
    # the runs themselves, it would not prevent the double notification).
    async with _get_recover_lock():
        if _recover_task is not None and not _recover_task.done():
            await callback.answer(msg.RECOVER_ALREADY_RUNNING, show_alert=True)
            return
        await callback.answer()

        await state.set_state(ModeratorReview.management_menu)
        chat_id = callback.message.chat.id
        bot = callback.bot
        actor = db_user

        async def _do_recover() -> None:
            try:
                async with _get_recover_lock():
                    # Show "running" state immediately so the admin knows something
                    # is happening; the count query runs in its own short session.
                    async with session_factory() as count_session:
                        total_count = len(await get_active_submissions(count_session))
                    await _render_management_message(
                        bot,
                        chat_id,
                        state,
                        msg.MANAGEMENT_RECOVER_TEXT.format(
                            body=msg.RECOVER_CHECKING.format(count=total_count)
                        ),
                        management_back_kb("menu"),
                    )

                    r_total, r_recovered = await recover_missing_posts(bot, session_factory)
                    if r_total == 0:
                        r_body = msg.RECOVER_NO_ACTIVE
                    elif r_recovered == 0:
                        r_body = msg.RECOVER_ALL_OK.format(total=r_total)
                    else:
                        r_body = msg.RECOVER_DONE.format(recovered=r_recovered, total=r_total)
                    await _render_management_message(
                        bot,
                        chat_id,
                        state,
                        msg.MANAGEMENT_RECOVER_TEXT.format(body=r_body),
                        management_back_kb("menu"),
                    )
                    async with session_factory() as notify_session:
                        await admin_notifications.notify_admins(
                            bot, notify_session,
                            actor=actor,
                            action_text=msg.ADMIN_NOTIFY_RECOVER_USED.format(
                                actor=admin_notifications.actor_display(actor),
                            ),
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Recover постов завершился ошибкой")
                try:
                    await _render_management_message(
                        bot,
                        chat_id,
                        state,
                        msg.MANAGEMENT_RECOVER_TEXT.format(body=msg.SOMETHING_WENT_WRONG),
                        management_back_kb("menu"),
                    )
                except Exception:
                    logger.exception("Не удалось показать ошибку Recover")

        task = asyncio.create_task(_do_recover())
        _recover_task = task
        task.add_done_callback(_on_recover_done)


@router.callback_query(ManagementCB.filter(F.action == "submit"))
async def handle_enter_submit_mode(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.set_state(ModeratorReview.submitting_post)
    await _render_management_message(
        callback.bot,
        callback.message.chat.id,
        state,
        msg.MODERATOR_SUBMIT_PROMPT,
        management_back_kb("home"),
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ManagementCB.filter(F.action == "close"))
async def handle_close_management(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    mid = db_user.telegram_id
    await edit_lock.release_lock(session, "management", "presets", mid)
    await edit_lock.release_lock(session, "management", "banned", mid)
    await edit_lock.release_lock(session, "management", "moderators", mid)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await state.clear()
    await callback.answer()


@router.callback_query(TagPresetCB.filter(F.action == "section"))
async def handle_preset_section(
    callback: CallbackQuery,
    callback_data: TagPresetCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    await _render_preset_list(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        callback_data.preset_type,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(TagPresetCB.filter(F.action == "add_section"))
async def handle_add_section_start(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "presets", db_user.telegram_id, _PRESETS_LOCK_TTL):
        await callback.answer(msg.MANAGEMENT_LOCK_LOST, show_alert=True)
        return
    await _render_add_section_prompt(
        callback.bot,
        callback.message.chat.id,
        state,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.message(ModeratorReview.management_add_section_label, F.text)
async def handle_add_section_label_input(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "presets", db_user.telegram_id, _PRESETS_LOCK_TTL):
        await message.answer(msg.MANAGEMENT_LOCK_LOST)
        return
    label = _normalize_section_label(message.text or "")
    await _delete_user_message(message)

    if not label:
        await _render_add_section_prompt(
            message.bot,
            message.chat.id,
            state,
            notice=msg.TAG_PRESET_EMPTY_SECTION_LABEL,
        )
        return

    existing = await get_tag_preset_section_by_label(session, label)
    if existing is not None:
        await _render_add_section_prompt(
            message.bot,
            message.chat.id,
            state,
            notice=msg.TAG_PRESET_DUPLICATE_SECTION_LABEL,
        )
        return

    section = await create_tag_preset_section(session, label)
    logger.info("Добавлен раздел пресетов %s (%s)", section.key, section.label)
    await admin_notifications.notify_admins(
        message.bot, session,
        actor=db_user,
        action_text=msg.ADMIN_NOTIFY_SECTION_CREATED.format(
            actor=admin_notifications.actor_display(db_user),
            label=html.escape(label),
        ),
    )
    await _render_preset_list(
        message.bot,
        message.chat.id,
        state,
        session,
        section.key,
        notice=msg.TAG_PRESET_SECTION_CREATED,
    )


@router.callback_query(TagPresetCB.filter(F.action == "edit_section"))
async def handle_edit_section_start(
    callback: CallbackQuery,
    callback_data: TagPresetCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "presets", db_user.telegram_id, _PRESETS_LOCK_TTL):
        await callback.answer(msg.MANAGEMENT_LOCK_LOST, show_alert=True)
        return
    section = await get_tag_preset_section(session, callback_data.preset_type)
    if section is None:
        await _render_preset_sections(
            callback.bot,
            callback.message.chat.id,
            state,
            session,
            message_id=callback.message.message_id,
            notice=msg.TAG_PRESET_SECTION_NOT_FOUND,
        )
        await callback.answer()
        return

    await _render_edit_section_prompt(
        callback.bot,
        callback.message.chat.id,
        state,
        section,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.message(ModeratorReview.management_edit_section_label, F.text)
async def handle_edit_section_label_input(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "presets", db_user.telegram_id, _PRESETS_LOCK_TTL):
        await message.answer(msg.MANAGEMENT_LOCK_LOST)
        return
    data = await state.get_data()
    section = await get_tag_preset_section(session, data.get("management_preset_type", ""))
    await _delete_user_message(message)

    if section is None:
        await _render_preset_sections(
            message.bot,
            message.chat.id,
            state,
            session,
            notice=msg.TAG_PRESET_SECTION_NOT_FOUND,
        )
        return

    label = _normalize_section_label(message.text or "")
    if not label:
        await _render_edit_section_prompt(
            message.bot,
            message.chat.id,
            state,
            section,
            notice=msg.TAG_PRESET_EMPTY_SECTION_LABEL,
        )
        return

    existing = await get_tag_preset_section_by_label(session, label)
    if existing is not None and existing.key != section.key:
        await _render_edit_section_prompt(
            message.bot,
            message.chat.id,
            state,
            section,
            notice=msg.TAG_PRESET_DUPLICATE_SECTION_LABEL,
        )
        return

    await update_tag_preset_section(session, section.key, label=label)
    logger.info("Переименован раздел пресетов %s -> %s", section.key, label)
    await admin_notifications.notify_admins(
        message.bot, session,
        actor=db_user,
        action_text=msg.ADMIN_NOTIFY_SECTION_UPDATED.format(
            actor=admin_notifications.actor_display(db_user),
            label=html.escape(label),
        ),
    )
    await _render_preset_list(
        message.bot,
        message.chat.id,
        state,
        session,
        section.key,
        notice=msg.TAG_PRESET_SECTION_UPDATED,
    )


@router.callback_query(TagPresetCB.filter(F.action == "delete_section_prompt"))
async def handle_delete_section_prompt(
    callback: CallbackQuery,
    callback_data: TagPresetCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "presets", db_user.telegram_id, _PRESETS_LOCK_TTL):
        await callback.answer(msg.MANAGEMENT_LOCK_LOST, show_alert=True)
        return
    await _render_section_delete_prompt(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        callback_data.preset_type,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(TagPresetCB.filter(F.action == "delete_section_confirm"))
async def handle_delete_section_confirm(
    callback: CallbackQuery,
    callback_data: TagPresetCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "presets", db_user.telegram_id, _PRESETS_LOCK_TTL):
        await callback.answer(msg.MANAGEMENT_LOCK_LOST, show_alert=True)
        return
    section = await get_tag_preset_section(session, callback_data.preset_type)
    if section is None:
        await _render_preset_sections(
            callback.bot,
            callback.message.chat.id,
            state,
            session,
            message_id=callback.message.message_id,
            notice=msg.TAG_PRESET_SECTION_NOT_FOUND,
        )
        await callback.answer()
        return

    section_label = section.label
    await delete_tag_preset_section(session, section.key)
    logger.info("Удалён раздел пресетов %s (%s)", section.key, section_label)
    await admin_notifications.notify_admins(
        callback.bot, session,
        actor=db_user,
        action_text=msg.ADMIN_NOTIFY_SECTION_DELETED.format(
            actor=admin_notifications.actor_display(db_user),
            label=html.escape(section_label),
        ),
    )
    await _render_preset_sections(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        message_id=callback.message.message_id,
        notice=msg.TAG_PRESET_SECTION_DELETED,
    )
    await callback.answer()


@router.callback_query(TagPresetCB.filter(F.action == "view"))
async def handle_view_preset(
    callback: CallbackQuery,
    callback_data: TagPresetCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    await _render_preset_item(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        callback_data.preset_type,
        callback_data.preset_id,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(TagPresetCB.filter(F.action == "add"))
async def handle_add_preset_start(
    callback: CallbackQuery,
    callback_data: TagPresetCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "presets", db_user.telegram_id, _PRESETS_LOCK_TTL):
        await callback.answer(msg.MANAGEMENT_LOCK_LOST, show_alert=True)
        return
    section = await get_tag_preset_section(session, callback_data.preset_type)
    if section is None:
        await _render_preset_sections(
            callback.bot,
            callback.message.chat.id,
            state,
            session,
            message_id=callback.message.message_id,
            notice=msg.TAG_PRESET_SECTION_NOT_FOUND,
        )
        await callback.answer()
        return

    await _render_add_label_prompt(
        callback.bot,
        callback.message.chat.id,
        state,
        section,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.message(ModeratorReview.management_add_preset_label, F.text)
async def handle_add_preset_label_input(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "presets", db_user.telegram_id, _PRESETS_LOCK_TTL):
        await message.answer(msg.MANAGEMENT_LOCK_LOST)
        return
    data = await state.get_data()
    preset_type = data["management_preset_type"]
    section = await get_tag_preset_section(session, preset_type)
    await _delete_user_message(message)

    if section is None:
        await _render_preset_sections(
            message.bot,
            message.chat.id,
            state,
            session,
            notice=msg.TAG_PRESET_SECTION_NOT_FOUND,
        )
        return

    label, tag, parse_error = _parse_new_preset(message.text or "")
    if parse_error:
        await _render_add_label_prompt(
            message.bot,
            message.chat.id,
            state,
            section,
            notice=parse_error,
        )
        return

    validation_error = await _validate_preset_values(
        session,
        preset_type,
        label=label,
        tag=tag,
    )
    if validation_error:
        await _render_add_label_prompt(
            message.bot,
            message.chat.id,
            state,
            section,
            notice=validation_error,
        )
        return

    preset = await create_tag_preset(session, preset_type, label, tag)
    logger.info("Добавлен пресет %s #%d (%s / %s)", preset_type, preset.id, label, tag)
    await admin_notifications.notify_admins(
        message.bot, session,
        actor=db_user,
        action_text=msg.ADMIN_NOTIFY_PRESET_CREATED.format(
            actor=admin_notifications.actor_display(db_user),
            label=html.escape(label),
            tag=html.escape(tag),
            section=html.escape(section.label),
        ),
    )
    await _render_preset_list(
        message.bot,
        message.chat.id,
        state,
        session,
        preset_type,
        notice=msg.TAG_PRESET_CREATED,
    )


@router.callback_query(TagPresetCB.filter(F.action == "edit_label"))
async def handle_edit_label_start(
    callback: CallbackQuery,
    callback_data: TagPresetCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "presets", db_user.telegram_id, _PRESETS_LOCK_TTL):
        await callback.answer(msg.MANAGEMENT_LOCK_LOST, show_alert=True)
        return
    preset = await get_tag_preset(session, callback_data.preset_id)
    if preset is None or preset.preset_type != callback_data.preset_type:
        await _render_preset_list(
            callback.bot,
            callback.message.chat.id,
            state,
            session,
            callback_data.preset_type,
            message_id=callback.message.message_id,
            notice=msg.TAG_PRESET_NOT_FOUND,
        )
        await callback.answer()
        return

    section = await get_tag_preset_section(session, preset.preset_type)
    if section is None:
        await _render_preset_sections(
            callback.bot,
            callback.message.chat.id,
            state,
            session,
            message_id=callback.message.message_id,
            notice=msg.TAG_PRESET_SECTION_NOT_FOUND,
        )
        await callback.answer()
        return

    await _render_edit_label_prompt(
        callback.bot,
        callback.message.chat.id,
        state,
        preset,
        section,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.message(ModeratorReview.management_edit_preset_label, F.text)
async def handle_edit_label_input(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "presets", db_user.telegram_id, _PRESETS_LOCK_TTL):
        await message.answer(msg.MANAGEMENT_LOCK_LOST)
        return
    data = await state.get_data()
    preset = await get_tag_preset(session, data["management_preset_id"])
    await _delete_user_message(message)

    if preset is None:
        await _render_preset_list(
            message.bot,
            message.chat.id,
            state,
            session,
            data["management_preset_type"],
            notice=msg.TAG_PRESET_NOT_FOUND,
        )
        return

    section = await get_tag_preset_section(session, preset.preset_type)
    if section is None:
        await _render_preset_sections(
            message.bot,
            message.chat.id,
            state,
            session,
            notice=msg.TAG_PRESET_SECTION_NOT_FOUND,
        )
        return

    label = _normalize_label(message.text or "")
    if not label:
        await _render_edit_label_prompt(
            message.bot,
            message.chat.id,
            state,
            preset,
            section,
            notice=msg.TAG_PRESET_EMPTY_LABEL,
        )
        return

    validation_error = await _validate_preset_values(
        session,
        preset.preset_type,
        label=label,
        tag=preset.tag,
        exclude_id=preset.id,
    )
    if validation_error:
        await _render_edit_label_prompt(
            message.bot,
            message.chat.id,
            state,
            preset,
            section,
            notice=validation_error,
        )
        return

    await update_tag_preset(session, preset.id, label=label)
    logger.info("Обновлён label пресета %s #%d → %s", preset.preset_type, preset.id, label)
    await admin_notifications.notify_admins(
        message.bot, session,
        actor=db_user,
        action_text=msg.ADMIN_NOTIFY_PRESET_UPDATED.format(
            actor=admin_notifications.actor_display(db_user),
            label=html.escape(label),
            tag=html.escape(preset.tag),
            section=html.escape(section.label),
        ),
    )
    await _render_preset_item(
        message.bot,
        message.chat.id,
        state,
        session,
        preset.preset_type,
        preset.id,
        notice=msg.TAG_PRESET_UPDATED,
    )


@router.callback_query(TagPresetCB.filter(F.action == "edit_tag"))
async def handle_edit_tag_start(
    callback: CallbackQuery,
    callback_data: TagPresetCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "presets", db_user.telegram_id, _PRESETS_LOCK_TTL):
        await callback.answer(msg.MANAGEMENT_LOCK_LOST, show_alert=True)
        return
    preset = await get_tag_preset(session, callback_data.preset_id)
    if preset is None or preset.preset_type != callback_data.preset_type:
        await _render_preset_list(
            callback.bot,
            callback.message.chat.id,
            state,
            session,
            callback_data.preset_type,
            message_id=callback.message.message_id,
            notice=msg.TAG_PRESET_NOT_FOUND,
        )
        await callback.answer()
        return

    section = await get_tag_preset_section(session, preset.preset_type)
    if section is None:
        await _render_preset_sections(
            callback.bot,
            callback.message.chat.id,
            state,
            session,
            message_id=callback.message.message_id,
            notice=msg.TAG_PRESET_SECTION_NOT_FOUND,
        )
        await callback.answer()
        return

    await _render_edit_tag_prompt(
        callback.bot,
        callback.message.chat.id,
        state,
        preset,
        section,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.message(ModeratorReview.management_edit_preset_tag, F.text)
async def handle_edit_tag_input(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "presets", db_user.telegram_id, _PRESETS_LOCK_TTL):
        await message.answer(msg.MANAGEMENT_LOCK_LOST)
        return
    data = await state.get_data()
    preset = await get_tag_preset(session, data["management_preset_id"])
    await _delete_user_message(message)

    if preset is None:
        await _render_preset_list(
            message.bot,
            message.chat.id,
            state,
            session,
            data["management_preset_type"],
            notice=msg.TAG_PRESET_NOT_FOUND,
        )
        return

    section = await get_tag_preset_section(session, preset.preset_type)
    if section is None:
        await _render_preset_sections(
            message.bot,
            message.chat.id,
            state,
            session,
            notice=msg.TAG_PRESET_SECTION_NOT_FOUND,
        )
        return

    tag = _normalize_tag(message.text or "")
    if not tag:
        await _render_edit_tag_prompt(
            message.bot,
            message.chat.id,
            state,
            preset,
            section,
            notice=msg.TAG_PRESET_EMPTY_TAG,
        )
        return

    if not _is_valid_tag(tag):
        await _render_edit_tag_prompt(
            message.bot,
            message.chat.id,
            state,
            preset,
            section,
            notice=msg.TAG_PRESET_INVALID_TAG,
        )
        return

    validation_error = await _validate_preset_values(
        session,
        preset.preset_type,
        label=preset.label,
        tag=tag,
        exclude_id=preset.id,
    )
    if validation_error:
        await _render_edit_tag_prompt(
            message.bot,
            message.chat.id,
            state,
            preset,
            section,
            notice=validation_error,
        )
        return

    await update_tag_preset(session, preset.id, tag=tag)
    logger.info("Обновлён tag пресета %s #%d → %s", preset.preset_type, preset.id, tag)
    await admin_notifications.notify_admins(
        message.bot, session,
        actor=db_user,
        action_text=msg.ADMIN_NOTIFY_PRESET_UPDATED.format(
            actor=admin_notifications.actor_display(db_user),
            label=html.escape(preset.label),
            tag=html.escape(tag),
            section=html.escape(section.label),
        ),
    )
    await _render_preset_item(
        message.bot,
        message.chat.id,
        state,
        session,
        preset.preset_type,
        preset.id,
        notice=msg.TAG_PRESET_UPDATED,
    )


@router.callback_query(TagPresetCB.filter(F.action == "delete_prompt"))
async def handle_delete_prompt(
    callback: CallbackQuery,
    callback_data: TagPresetCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "presets", db_user.telegram_id, _PRESETS_LOCK_TTL):
        await callback.answer(msg.MANAGEMENT_LOCK_LOST, show_alert=True)
        return
    await _render_preset_item(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        callback_data.preset_type,
        callback_data.preset_id,
        message_id=callback.message.message_id,
        confirming_delete=True,
    )
    await callback.answer()


@router.callback_query(TagPresetCB.filter(F.action == "delete_confirm"))
async def handle_delete_confirm(
    callback: CallbackQuery,
    callback_data: TagPresetCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "presets", db_user.telegram_id, _PRESETS_LOCK_TTL):
        await callback.answer(msg.MANAGEMENT_LOCK_LOST, show_alert=True)
        return
    preset = await get_tag_preset(session, callback_data.preset_id)
    if preset is None or preset.preset_type != callback_data.preset_type:
        await _render_preset_list(
            callback.bot,
            callback.message.chat.id,
            state,
            session,
            callback_data.preset_type,
            message_id=callback.message.message_id,
            notice=msg.TAG_PRESET_NOT_FOUND,
        )
        await callback.answer()
        return

    section = await get_tag_preset_section(session, preset.preset_type)
    preset_label = preset.label
    preset_tag = preset.tag
    section_label = section.label if section else preset.preset_type

    await delete_tag_preset(session, preset.id)
    logger.info("Удалён пресет %s #%d (%s)", preset.preset_type, preset.id, preset.tag)
    await admin_notifications.notify_admins(
        callback.bot, session,
        actor=db_user,
        action_text=msg.ADMIN_NOTIFY_PRESET_DELETED.format(
            actor=admin_notifications.actor_display(db_user),
            label=html.escape(preset_label),
            tag=html.escape(preset_tag),
            section=html.escape(section_label),
        ),
    )
    await _render_preset_list(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        preset.preset_type,
        message_id=callback.message.message_id,
        notice=msg.TAG_PRESET_DELETED,
    )
    await callback.answer()


@router.callback_query(UnbanCB.filter())
async def handle_unban_select(
    callback: CallbackQuery,
    callback_data: UnbanCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _guard_management_lock(session, "banned", db_user.telegram_id, _BANNED_LOCK_TTL):
        await callback.answer(msg.MANAGEMENT_LOCK_LOST, show_alert=True)
        return
    target_user = await get_user_by_id(session, callback_data.user_id)
    await unban_user(session, callback_data.user_id)
    logger.info("Пользователь id:%d разбанен", callback_data.user_id)
    request_author_card(callback_data.user_id)
    notice = msg.USER_UNBANNED.format(user=html.escape(f"id:{callback_data.user_id}"))

    if target_user is not None:
        target_display = (
            f"@{html.escape(target_user.username)}" if target_user.username
            else html.escape(target_user.full_name)
        )
    else:
        target_display = html.escape(f"id:{callback_data.user_id}")

    await admin_notifications.notify_admins(
        callback.bot, session,
        actor=db_user,
        action_text=msg.ADMIN_NOTIFY_USER_UNBANNED.format(
            actor=admin_notifications.actor_display(db_user),
            user=target_display,
        ),
    )
    if target_user is not None:
        try:
            await topic_notifications.notify_unbanned(callback.bot, session, target_user, db_user)
        except Exception:
            logger.warning("Не удалось отправить notify_unbanned для user_id=%d", target_user.id)
        try:
            await topics.request_topic_title_sync(session, target_user.id)
        except Exception:
            logger.warning("Не удалось обновить заголовок темы для user_id=%d", target_user.id)
    await _render_banned_users(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        message_id=callback.message.message_id,
        notice=notice,
    )
    await callback.answer(msg.USER_UNBANNED.format(user=f"id:{callback_data.user_id}"))
