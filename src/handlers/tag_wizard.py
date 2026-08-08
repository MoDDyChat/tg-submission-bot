"""Dynamic tag editing wizard for moderators."""

import html

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.config import config
from core.logging import get_logger
from db.models import TagPresetEntry, TagPresetSection, User
from db.queries import (
    get_submission_with_user,
    list_tag_preset_sections,
    list_tag_presets_grouped,
    update_submission_tags,
)
from keyboards.callbacks import SubmissionCB, TagWizardCB
from keyboards.tags import tags_custom_kb, tags_preset_page_kb
from services import edit_lock, topic_notifications, topics
from services.tag_parsing import deserialize_suggested
from states.moderator import ModeratorReview
from utils.tags import (
    MAX_CUSTOM_TAG_LENGTH,
    MAX_CUSTOM_TAGS_COUNT,
    MAX_MEDIA_CAPTION,
    MAX_TEXT_CAPTION,
    compose_caption,
    dedupe_tags,
    format_tags_line,
    parse_tags_input,
    strip_html_for_length,
    validate_caption_length,
)
from handlers.moderator._helpers import _extend_submission_lock_from_state as _extend_lock_from_state

logger = get_logger(__name__)

router = Router()
TERMINAL_STATUSES = {"cancelled", "published", "rejected"}


def _assemble_wizard_tags(data: dict, sections: list[TagPresetSection]) -> list[str]:
    tags: list[str] = []
    selected_by_section: dict[str, list[str]] = data.get("wizard_sections", {})
    for section in sections:
        tags.extend(selected_by_section.get(section.key, []))
    tags.extend(data.get("wizard_custom", []))
    # Связка и одиночный пресет могут нести один и тот же тег — в подписи он не двоится.
    return dedupe_tags(tags)


def _build_preview_text(tags: list[str], caption: str | None, prompt: str) -> str:
    tags_line = format_tags_line(tags)
    preview_parts: list[str] = []
    if tags_line:
        preview_parts.append(tags_line)
    if caption:
        preview_parts.append(caption)
    preview = "\n\n".join(preview_parts) if preview_parts else "<i>Без подписи и тегов</i>"
    return f"{msg.TAGS_PREVIEW_HEADER.format(preview=preview)}\n{prompt}"


async def _load_presets(
    session: AsyncSession,
) -> tuple[list[TagPresetSection], dict[str, list[TagPresetEntry]]]:
    sections = await list_tag_preset_sections(session)
    grouped = await list_tag_presets_grouped(session)
    for section in sections:
        grouped.setdefault(section.key, [])
    return sections, grouped


def _normalize_selected_by_section(raw_value: object) -> dict[str, list[str]]:
    if not isinstance(raw_value, dict):
        return {}

    normalized: dict[str, list[str]] = {}
    for section_key, values in raw_value.items():
        if isinstance(section_key, str) and isinstance(values, list):
            normalized[section_key] = [value for value in values if isinstance(value, str)]
    return normalized


def _sync_wizard_data(
    data: dict,
    sections: list[TagPresetSection],
    grouped: dict[str, list[TagPresetEntry]],
) -> tuple[dict[str, list[str]], list[str], int]:
    selected_by_section = _normalize_selected_by_section(data.get("wizard_sections"))
    custom = [tag for tag in data.get("wizard_custom", []) if isinstance(tag, str)]

    section_keys = {section.key for section in sections}
    valid_tags_by_section = {
        section.key: {preset.tag for preset in grouped.get(section.key, [])}
        for section in sections
    }

    for section_key, tags in list(selected_by_section.items()):
        if section_key not in section_keys:
            for tag in tags:
                if tag not in custom:
                    custom.append(tag)
            selected_by_section.pop(section_key, None)
            continue

        valid_tags = valid_tags_by_section[section_key]
        kept: list[str] = []
        removed: list[str] = []
        for tag in tags:
            if tag in valid_tags:
                if tag not in kept:
                    kept.append(tag)
            else:
                removed.append(tag)
        selected_by_section[section_key] = kept
        for tag in removed:
            if tag not in custom:
                custom.append(tag)

    for section in sections:
        selected_by_section.setdefault(section.key, [])

    page_index = data.get("wizard_page_index", 0)
    if not isinstance(page_index, int):
        page_index = 0
    page_index = max(0, min(page_index, len(sections)))

    return selected_by_section, custom, page_index


def _detect_existing_presets(
    tags: list[str],
    sections: list[TagPresetSection],
    grouped: dict[str, list[TagPresetEntry]],
) -> tuple[dict[str, list[str]], list[str]]:
    tag_set = set(tags)
    selected_by_section: dict[str, list[str]] = {}
    known_tags: set[str] = set()

    for section in sections:
        selected = [preset.tag for preset in grouped.get(section.key, []) if preset.tag in tag_set]
        selected_by_section[section.key] = selected
        known_tags.update(selected)

    custom = [tag for tag in tags if tag not in known_tags]
    return selected_by_section, custom


def _format_suggested_hint(entries: list) -> list[str]:
    """Подсказка по непроставленным тегам автора: '#raw' или '#raw (похоже на #Tag)'.

    Принимает и старый формат FSM-данных (список строк), и новый (список dict).
    """
    parts: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            raw, guess = entry, None
        elif isinstance(entry, dict):
            raw = entry.get("raw")
            guess = entry.get("tag")
            if not isinstance(raw, str) or not raw:
                continue
            if not isinstance(guess, str) or not guess:
                guess = None
        else:
            continue
        if guess:
            parts.append(
                msg.TAGS_WIZARD_SUGGESTED_FUZZY.format(
                    raw=html.escape(raw), guess=html.escape(guess)
                )
            )
        else:
            parts.append(f"#{html.escape(raw)}")
    return parts


def _build_wizard_view(
    data: dict,
    sections: list[TagPresetSection],
    grouped: dict[str, list[TagPresetEntry]],
) -> tuple[State, str, object, dict[str, list[str]], list[str], int]:
    selected_by_section, custom, page_index = _sync_wizard_data(data, sections, grouped)
    hydrated_data = dict(data)
    hydrated_data["wizard_sections"] = selected_by_section
    hydrated_data["wizard_custom"] = custom

    tags = _assemble_wizard_tags(hydrated_data, sections)
    caption = hydrated_data.get("wizard_caption")

    if page_index < len(sections):
        section = sections[page_index]
        section_presets = grouped.get(section.key, [])
        prompt = msg.TAGS_WIZARD_SECTION.format(section=html.escape(section.label))
        if not section_presets:
            prompt += msg.TAGS_WIZARD_SECTION_EMPTY_HINT
        text = _build_preview_text(tags, caption, prompt)
        keyboard = tags_preset_page_kb(
            section,
            section_presets,
            set(selected_by_section.get(section.key, [])),
            can_go_back=page_index > 0,
            has_next=True,
        )
        state = ModeratorReview.editing_tags_presets
    else:
        text = _build_preview_text(tags, caption, msg.TAGS_WIZARD_CUSTOM)
        hint_parts = _format_suggested_hint(data.get("wizard_suggested_unknown", []))
        if hint_parts:
            text += "\n\n" + msg.TAGS_WIZARD_SUGGESTED_HINT.format(
                tags=", ".join(hint_parts)
            )
        keyboard = tags_custom_kb(can_go_back=bool(sections))
        state = ModeratorReview.editing_tags_custom

    return state, text, keyboard, selected_by_section, custom, page_index


async def _render_wizard_message(
    bot,
    chat_id: int,
    state: FSMContext,
    session: AsyncSession,
    *,
    message_id: int | None = None,
) -> int:
    data = await state.get_data()
    sections, grouped = await _load_presets(session)
    target_state, text, reply_markup, selected_by_section, custom, page_index = _build_wizard_view(
        data,
        sections,
        grouped,
    )
    await state.update_data(
        wizard_sections=selected_by_section,
        wizard_custom=custom,
        wizard_page_index=page_index,
    )
    await state.set_state(target_state)

    target_message_id = message_id or data.get("wizard_message_id")
    if target_message_id is not None:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=target_message_id,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            await state.update_data(wizard_message_id=target_message_id)
            return target_message_id
        except TelegramBadRequest as exc:
            error_text = str(exc).lower()
            if "message is not modified" in error_text:
                await state.update_data(wizard_message_id=target_message_id)
                return target_message_id
            if "message to edit not found" not in error_text:
                logger.warning("Ошибка edit wizard message: %s", exc)

    sent = await bot.send_message(
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
    await state.update_data(wizard_message_id=sent.message_id)
    return sent.message_id


@router.callback_query(SubmissionCB.filter(F.action == "edit_tags"))
async def handle_edit_tags_start(
    callback: CallbackQuery,
    callback_data: SubmissionCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    logger.debug("tag_wizard: start, sub_id=%d", callback_data.sub_id)
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

    sections, grouped = await _load_presets(session)
    selected_by_section, custom = _detect_existing_presets(sub.tags or [], sections, grouped)

    suggested_unknown: list[dict[str, str | None]] = []
    if not sub.tags and sub.suggested_tags:
        suggested = deserialize_suggested(sub.suggested_tags)
        # Предвыбираем только точные совпадения: fuzzy-догадка становится подсказкой,
        # иначе модератор молча сохраняет тег, которого автор не писал.
        preselect, orphaned = _detect_existing_presets(
            [item.tag for item in suggested if item.tag is not None and item.exact],
            sections,
            grouped,
        )
        orphaned_set = set(orphaned)
        selected_by_section = preselect
        suggested_unknown = [
            {"raw": item.raw, "tag": item.tag}
            for item in suggested
            if item.tag is None or not item.exact or item.tag in orphaned_set
        ]

    await state.update_data(
        sub_id=callback_data.sub_id,
        wizard_caption=sub.caption,
        wizard_sections=selected_by_section,
        wizard_custom=custom,
        wizard_suggested_unknown=suggested_unknown,
        wizard_page_index=0,
        wizard_message_id=None,
    )
    await _render_wizard_message(callback.bot, callback.message.chat.id, state, session)
    await callback.answer()


@router.callback_query(ModeratorReview.editing_tags_presets, TagWizardCB.filter(F.action == "toggle"))
async def handle_toggle_preset(
    callback: CallbackQuery,
    callback_data: TagWizardCB,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _extend_lock_from_state(session, state, db_user.id):
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    raw_value = callback_data.value or ""
    try:
        preset_id = int(raw_value)
    except ValueError:
        await callback.answer(msg.SOMETHING_WENT_WRONG, show_alert=True)
        return

    _, grouped = await _load_presets(session)
    preset = next(
        (item for entries in grouped.values() for item in entries if item.id == preset_id),
        None,
    )
    if preset is None:
        # Пресет удалили, пока клавиатура висела на экране — перерисуем актуальную.
        await _render_wizard_message(
            callback.bot,
            callback.message.chat.id,
            state,
            session,
            message_id=callback.message.message_id,
        )
        await callback.answer(msg.TAG_PRESET_NOT_FOUND, show_alert=True)
        return

    section_key, tag = preset.preset_type, preset.tag
    data = await state.get_data()
    selected_by_section = _normalize_selected_by_section(data.get("wizard_sections"))
    selected_tags = list(selected_by_section.get(section_key, []))
    if tag in selected_tags:
        selected_tags.remove(tag)
    else:
        selected_tags.append(tag)
    selected_by_section[section_key] = selected_tags

    await state.update_data(wizard_sections=selected_by_section)
    await _render_wizard_message(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ModeratorReview.editing_tags_presets, TagWizardCB.filter(F.action == "page_next"))
async def handle_next_page(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _extend_lock_from_state(session, state, db_user.id):
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    sections = await list_tag_preset_sections(session)
    data = await state.get_data()
    current_index = data.get("wizard_page_index", 0)
    if not isinstance(current_index, int):
        current_index = 0

    await state.update_data(wizard_page_index=min(current_index + 1, len(sections)))
    await _render_wizard_message(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ModeratorReview.editing_tags_presets, TagWizardCB.filter(F.action == "page_prev"))
async def handle_prev_page(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _extend_lock_from_state(session, state, db_user.id):
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    data = await state.get_data()
    current_index = data.get("wizard_page_index", 0)
    if not isinstance(current_index, int):
        current_index = 0

    await state.update_data(wizard_page_index=max(current_index - 1, 0))
    await _render_wizard_message(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.message(ModeratorReview.editing_tags_custom, F.text)
async def handle_custom_tags_text(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _extend_lock_from_state(session, state, db_user.id):
        await state.clear()
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(msg.MODERATOR_LOCK_LOST)
        return
    logger.debug("tag_wizard: custom text=%s", message.text)
    # Пробел разделяет теги, разделитель — держит связку: «A | #B» останется одним тегом.
    new_tags = parse_tags_input(message.text or "")

    # Validate individual tag length
    for tag in new_tags:
        if tag and len(tag) > MAX_CUSTOM_TAG_LENGTH:
            await message.answer(
                msg.CUSTOM_TAG_TOO_LONG.format(tag=html.escape(tag[:20] + "..."), max=MAX_CUSTOM_TAG_LENGTH)
            )
            return

    data = await state.get_data()
    custom = [tag for tag in data.get("wizard_custom", []) if isinstance(tag, str)]
    new_unique = [tag for tag in new_tags if tag and tag not in custom]

    # Validate total custom tags limit
    if len(custom) + len(new_unique) > MAX_CUSTOM_TAGS_COUNT:
        await message.answer(msg.CUSTOM_TAGS_LIMIT.format(max=MAX_CUSTOM_TAGS_COUNT))
        return

    for tag in new_unique:
        custom.append(tag)
    await state.update_data(wizard_custom=custom)

    try:
        await message.delete()
    except Exception:
        pass

    await _render_wizard_message(message.bot, message.chat.id, state, session)


@router.callback_query(ModeratorReview.editing_tags_custom, TagWizardCB.filter(F.action == "back_presets"))
async def handle_back_to_presets(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    if not await _extend_lock_from_state(session, state, db_user.id):
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return
    sections = await list_tag_preset_sections(session)
    if not sections:
        await callback.answer()
        return

    await state.update_data(wizard_page_index=len(sections) - 1)
    await _render_wizard_message(
        callback.bot,
        callback.message.chat.id,
        state,
        session,
        message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(ModeratorReview.editing_tags_custom, TagWizardCB.filter(F.action == "custom_finish"))
async def handle_custom_finish(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    data = await state.get_data()
    logger.debug("tag_wizard: finish, sub_id=%d", data.get("sub_id", 0))

    sub_id = data["sub_id"]

    # Extend lock before committing changes
    still_mine = await edit_lock.extend_lock(
        session, "submission", str(sub_id), db_user.id,
        ttl_seconds=config.edit_lock_ttl_seconds,
    )
    if not still_mine:
        await state.clear()
        await callback.answer(msg.MODERATOR_LOCK_LOST, show_alert=True)
        return

    sub = await get_submission_with_user(session, sub_id)
    if sub is None or sub.status in TERMINAL_STATUSES:
        await state.clear()
        await callback.answer(msg.SUBMISSION_NOT_AVAILABLE, show_alert=True)
        return

    sections, grouped = await _load_presets(session)
    selected_by_section, custom, page_index = _sync_wizard_data(data, sections, grouped)
    await state.update_data(
        wizard_sections=selected_by_section,
        wizard_custom=custom,
        wizard_page_index=page_index,
    )

    final_data = dict(data)
    final_data["wizard_sections"] = selected_by_section
    final_data["wizard_custom"] = custom
    final_tags = _assemble_wizard_tags(final_data, sections)
    old_tags = list(sub.tags or [])
    caption = sub.caption

    has_media = bool(sub.media)
    cap_limit = MAX_MEDIA_CAPTION if has_media else MAX_TEXT_CAPTION
    if not validate_caption_length(final_tags, caption, has_media=has_media):
        composed = compose_caption(final_tags, caption)
        await callback.answer(
            msg.TAGS_TOO_LONG.format(length=len(strip_html_for_length(composed)), max_length=cap_limit),
            show_alert=True,
        )
        return

    tags_changed = old_tags != list(final_tags)
    if tags_changed:
        await update_submission_tags(session, sub_id, final_tags)
    # Commit unconditionally: the lock extension above is a write too, and the
    # transaction must be closed before the Telegram calls below.
    await session.commit()

    if tags_changed:
        logger.info("Теги поста #%d обновлены: %s", sub_id, final_tags)
    else:
        logger.info("Теги поста #%d не изменились — уведомление пропущено", sub_id)

    try:
        try:
            await callback.message.delete()
        except Exception:
            pass

        from handlers.moderator._helpers import _send_submission_view

        sub = await get_submission_with_user(session, sub_id)
        if sub:
            await _send_submission_view(callback.message, session, sub_id, state)
            if tags_changed:
                await topic_notifications.notify_tags_changed(
                    callback.bot, session, sub, db_user, old_tags, final_tags
                )
                await topics.update_submission_card(callback.bot, session, sub)
                await topics.request_topic_title_sync(session, sub.user.id)
        else:
            await state.clear()
            await callback.answer(msg.SOMETHING_WENT_WRONG, show_alert=True)
            return
    except Exception:
        logger.exception("Ошибка при завершении tag wizard для заявки #%d", sub_id)
        await state.clear()
        try:
            await callback.answer(msg.SOMETHING_WENT_WRONG, show_alert=True)
        except Exception:
            pass
        return

    await callback.answer(msg.TAGS_UPDATED)
