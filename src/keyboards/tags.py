"""Inline keyboards for the dynamic tag editing wizard."""

import hashlib
from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.models import TagPresetEntry, TagPresetSection
from keyboards.callbacks import TagWizardCB


def custom_tag_token(tag: str) -> str:
    """Короткий отпечаток кастомного тега для callback_data.

    Сам тег (до 64 символов, кириллица — по два байта) в лимит callback_data
    в 64 байта не влезает, а индекс сдвигается после каждого удаления.
    """
    return hashlib.sha1(tag.encode("utf-8")).hexdigest()[:10]


def tags_preset_page_kb(
    section: TagPresetSection,
    presets: Sequence[TagPresetEntry],
    selected: set[str] | None = None,
    *,
    can_go_back: bool,
    has_next: bool,
) -> InlineKeyboardMarkup:
    if selected is None:
        selected = set()

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    columns = max(1, section.columns)

    for preset in presets:
        check = "\u2705 " if preset.tag in selected else ""
        row.append(
            InlineKeyboardButton(
                text=f"{check}{preset.label}",
                # Значение — id пресета: тег любой длины (а связка длинная всегда)
                # не должен упираться в лимит callback_data в 64 байта.
                callback_data=TagWizardCB(
                    action="toggle",
                    value=str(preset.id),
                ).pack(),
            )
        )
        if len(row) == columns:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    nav_row: list[InlineKeyboardButton] = []
    if can_go_back:
        nav_row.append(
            InlineKeyboardButton(
                text="\u2190 Назад",
                callback_data=TagWizardCB(action="page_prev").pack(),
            )
        )
    if has_next:
        nav_row.append(
            InlineKeyboardButton(
                text="Далее \u2192",
                callback_data=TagWizardCB(action="page_next").pack(),
            )
        )
    if nav_row:
        rows.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def tags_custom_kb(
    custom: Sequence[str] = (),
    *,
    can_go_back: bool = True,
) -> InlineKeyboardMarkup:
    # По кнопке на строку: тег бывает длинной связкой, в две колонки он обрежется.
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"✖ #{tag}",
                callback_data=TagWizardCB(
                    action="remove_custom",
                    value=custom_tag_token(tag),
                ).pack(),
            )
        ]
        for tag in custom
    ]
    if can_go_back:
        rows.append([
            InlineKeyboardButton(
                text="\u2190 Назад",
                callback_data=TagWizardCB(action="back_presets").pack(),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="Завершить редактирование",
            callback_data=TagWizardCB(action="custom_finish").pack(),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
