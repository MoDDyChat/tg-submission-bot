import core.messages as msg
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.models import TagPresetEntry
from db.models import TagPresetSection
from db.models import User
from keyboards.callbacks import ConfirmCB, ManagementCB, MediaCB, SubmissionCB, TagPresetCB, UnbanCB


def submission_actions_kb(sub_id: int, status: str = "pending") -> InlineKeyboardMarkup:
    """Action buttons when viewing a specific submission in DM."""
    is_scheduled = status == "scheduled"
    schedule_text = "Изменить время" if is_scheduled else "Запланировать публикацию"

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text="Редактировать описание",
            callback_data=SubmissionCB(action="edit_caption", sub_id=sub_id).pack(),
        )],
        [InlineKeyboardButton(
            text="Редактировать теги",
            callback_data=SubmissionCB(action="edit_tags", sub_id=sub_id).pack(),
        )],
        [InlineKeyboardButton(
            text=msg.BTN_EDIT_MEDIA,
            callback_data=SubmissionCB(action="edit_media", sub_id=sub_id).pack(),
        )],
        [InlineKeyboardButton(
            text=schedule_text,
            callback_data=SubmissionCB(action="schedule", sub_id=sub_id).pack(),
        )],
    ]

    if not is_scheduled:
        rows.append([InlineKeyboardButton(
            text="Опубликовать сейчас",
            callback_data=SubmissionCB(action="publish_now", sub_id=sub_id).pack(),
        )])

    if is_scheduled:
        rows.append([InlineKeyboardButton(
            text="Снять с расписания",
            callback_data=SubmissionCB(action="unschedule", sub_id=sub_id).pack(),
        )])

    rows.append([
        InlineKeyboardButton(
            text="Связаться с автором",
            callback_data=SubmissionCB(action="contact", sub_id=sub_id).pack(),
        ),
    ])
    rows.append([
        InlineKeyboardButton(
            text="Отклонить",
            callback_data=SubmissionCB(action="reject", sub_id=sub_id).pack(),
        ),
        InlineKeyboardButton(
            text="Отклонить тихо",
            callback_data=SubmissionCB(action="reject_silent", sub_id=sub_id).pack(),
        ),
    ])
    rows.append([InlineKeyboardButton(
        text="🚫 Забанить автора",
        callback_data=SubmissionCB(action="ban_author", sub_id=sub_id).pack(),
    )])
    rows.append([InlineKeyboardButton(
        text="Закрыть",
        callback_data=SubmissionCB(action="close", sub_id=sub_id).pack(),
    )])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_publish_now_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Да, опубликовать",
                callback_data=ConfirmCB(action="yes").pack(),
            ),
            InlineKeyboardButton(
                text="Отмена",
                callback_data=ConfirmCB(action="no").pack(),
            ),
        ],
    ])


def banned_users_kb(users: list[User]) -> InlineKeyboardMarkup:
    """One button per banned user; pressing unbans them."""
    rows = []
    for user in users:
        label = f"@{user.username}" if user.username else user.full_name
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=UnbanCB(user_id=user.id).pack(),
        )])
    rows.append([InlineKeyboardButton(
        text=msg.BTN_BACK,
        callback_data=ManagementCB(action="menu").pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_schedule_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Подтвердить",
                callback_data=ConfirmCB(action="yes").pack(),
            ),
            InlineKeyboardButton(
                text="Изменить время",
                callback_data=ConfirmCB(action="back").pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="Отмена",
                callback_data=ConfirmCB(action="no").pack(),
            ),
        ],
    ])


def moderator_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=msg.BTN_MANAGEMENT,
            callback_data=ManagementCB(action="menu").pack(),
        )],
        [InlineKeyboardButton(
            text=msg.BTN_SUBMIT_ART,
            callback_data=ManagementCB(action="submit").pack(),
        )],
        [InlineKeyboardButton(
            text=msg.BTN_CLOSE,
            callback_data=ManagementCB(action="close").pack(),
        )],
    ])


def management_menu_kb(*, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text=msg.BTN_TAG_PRESETS,
            callback_data=ManagementCB(action="presets").pack(),
        )],
        [InlineKeyboardButton(
            text=msg.BTN_BANNED_USERS,
            callback_data=ManagementCB(action="banned").pack(),
        )],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(
            text=msg.BTN_RECOVER_POSTS,
            callback_data=ManagementCB(action="recover").pack(),
        )])
    rows.append([InlineKeyboardButton(
        text=msg.BTN_BACK,
        callback_data=ManagementCB(action="home").pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def management_back_kb(action: str = "menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=msg.BTN_BACK,
            callback_data=ManagementCB(action=action).pack(),
        )],
    ])


def tag_preset_sections_kb(
    sections: list[TagPresetSection],
    preset_counts: dict[str, int] | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for section in sections:
        count = preset_counts.get(section.key, 0) if preset_counts is not None else None
        label = section.label if count is None else f"{section.label} ({count})"
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=TagPresetCB(action="section", preset_type=section.key).pack(),
        )])
    rows.append([InlineKeyboardButton(
        text=msg.BTN_ADD_SECTION,
        callback_data=TagPresetCB(action="add_section").pack(),
    )])
    rows.append([InlineKeyboardButton(
        text=msg.BTN_BACK,
        callback_data=ManagementCB(action="menu").pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tag_presets_list_kb(
    preset_type: str,
    presets: list[TagPresetEntry],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for preset in presets:
        rows.append([InlineKeyboardButton(
            text=preset.label,
            callback_data=TagPresetCB(
                action="view",
                preset_type=preset_type,
                preset_id=preset.id,
            ).pack(),
        )])
    rows.append([InlineKeyboardButton(
        text=msg.BTN_ADD,
        callback_data=TagPresetCB(action="add", preset_type=preset_type).pack(),
    )])
    rows.append([InlineKeyboardButton(
        text=msg.BTN_EDIT_SECTION,
        callback_data=TagPresetCB(action="edit_section", preset_type=preset_type).pack(),
    )])
    rows.append([InlineKeyboardButton(
        text=msg.BTN_DELETE_SECTION,
        callback_data=TagPresetCB(action="delete_section_prompt", preset_type=preset_type).pack(),
    )])
    rows.append([InlineKeyboardButton(
        text=msg.BTN_BACK,
        callback_data=ManagementCB(action="presets").pack(),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tag_preset_item_kb(
    preset_type: str,
    preset_id: int,
    *,
    confirming_delete: bool = False,
) -> InlineKeyboardMarkup:
    if confirming_delete:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=msg.BTN_DELETE_CONFIRM,
                callback_data=TagPresetCB(
                    action="delete_confirm",
                    preset_type=preset_type,
                    preset_id=preset_id,
                ).pack(),
            )],
            [InlineKeyboardButton(
                text=msg.BTN_BACK,
                callback_data=TagPresetCB(
                    action="view",
                    preset_type=preset_type,
                    preset_id=preset_id,
                ).pack(),
            )],
        ])

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=msg.BTN_EDIT_LABEL,
            callback_data=TagPresetCB(
                action="edit_label",
                preset_type=preset_type,
                preset_id=preset_id,
            ).pack(),
        )],
        [InlineKeyboardButton(
            text=msg.BTN_EDIT_TAG,
            callback_data=TagPresetCB(
                action="edit_tag",
                preset_type=preset_type,
                preset_id=preset_id,
            ).pack(),
        )],
        [InlineKeyboardButton(
            text=msg.BTN_DELETE,
            callback_data=TagPresetCB(
                action="delete_prompt",
                preset_type=preset_type,
                preset_id=preset_id,
            ).pack(),
        )],
        [InlineKeyboardButton(
            text=msg.BTN_BACK,
            callback_data=TagPresetCB(action="section", preset_type=preset_type).pack(),
        )],
    ])


def tag_preset_input_kb(
    back_callback_data: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=msg.BTN_BACK,
            callback_data=back_callback_data,
        )],
    ])


def tag_preset_section_delete_kb(preset_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=msg.BTN_DELETE_CONFIRM,
            callback_data=TagPresetCB(action="delete_section_confirm", preset_type=preset_type).pack(),
        )],
        [InlineKeyboardButton(
            text=msg.BTN_BACK,
            callback_data=TagPresetCB(action="section", preset_type=preset_type).pack(),
        )],
    ])


def topic_submission_card_kb(
    sub_id: int,
    status: str,
    *,
    locked_by: str | None = None,
    bot_username: str = "",
) -> InlineKeyboardMarkup:
    """Keyboard for the topic card of a submission.

    - If locked_by is given: show a non-clickable 'Editing by @mod' indicator.
    - If status is terminal: no buttons.
    - Otherwise: deep-link 'Edit' button.
    """
    terminal_statuses = {"published", "rejected", "cancelled"}

    if locked_by is not None:
        lock_label = f"✏️ Редактирует @{locked_by}" if locked_by else "✏️ Редактируется модератором"
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=lock_label, callback_data="noop"),
        ]])

    if status in terminal_statuses:
        return InlineKeyboardMarkup(inline_keyboard=[])

    edit_url = f"https://t.me/{bot_username}?start=review_{sub_id}" if bot_username else "https://t.me/"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✏️ Редактировать", url=edit_url),
    ]])


def media_manager_kb(sub_id: int, media: list) -> InlineKeyboardMarkup:
    rows = []
    for i, m in enumerate(media, start=1):
        label = msg.MEDIA_TYPE_LABELS.get(m.media_type, m.media_type)
        rows.append([InlineKeyboardButton(
            text=f"❌ {i}. {label}",
            callback_data=MediaCB(action="delete", sub_id=sub_id, media_id=m.id).pack(),
        )])
    rows.append([InlineKeyboardButton(text=msg.BTN_ADD_MEDIA, callback_data=MediaCB(action="add", sub_id=sub_id).pack())])
    rows.append([InlineKeyboardButton(text=msg.BTN_MEDIA_DONE, callback_data=MediaCB(action="done", sub_id=sub_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)
