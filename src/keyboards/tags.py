"""Inline keyboards for the dynamic tag editing wizard."""

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.models import TagPresetEntry, TagPresetSection
from keyboards.callbacks import TagWizardCB


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
                callback_data=TagWizardCB(
                    action="toggle",
                    value=f"{section.key}|{preset.tag}",
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


def tags_custom_kb(*, can_go_back: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
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
