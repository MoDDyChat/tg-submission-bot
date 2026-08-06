"""Callback data factories for inline keyboards."""

from typing import Literal

from aiogram.filters.callback_data import CallbackData


class SubmissionCB(CallbackData, prefix="sub"):
    action: Literal[
        "edit_caption", "edit_tags", "edit_media", "schedule", "reject", "reject_silent", "cancel",
        "contact", "close", "ban_author", "publish_now", "unschedule",
    ]
    sub_id: int


class MediaCB(CallbackData, prefix="media"):
    action: Literal["delete", "add", "done"]
    sub_id: int
    media_id: int = 0


class CalendarCB(CallbackData, prefix="cal"):
    action: Literal["day", "prev_month", "next_month", "ignore", "back_to_cal", "back_to_hours"]
    year: int
    month: int
    day: int


class TimeCB(CallbackData, prefix="time"):
    hour: int
    minute: int


class ConfirmCB(CallbackData, prefix="confirm"):
    action: Literal["yes", "no", "back"]


class ViewerCancelCB(CallbackData, prefix="vcancel"):
    sub_id: int


class TagWizardCB(CallbackData, prefix="tagwiz"):
    action: Literal["toggle", "page_next", "page_prev", "custom_finish", "back_presets"]
    value: str | None = ""



class UnbanCB(CallbackData, prefix="unban"):
    user_id: int


class ManagementCB(CallbackData, prefix="mgmt"):
    action: Literal["home", "menu", "presets", "banned", "recover", "close", "submit"]


class TagPresetCB(CallbackData, prefix="tpreset"):
    action: Literal[
        "section", "add_section", "edit_section",
        "delete_section_prompt", "delete_section_confirm",
        "view", "add", "edit_label", "edit_tag",
        "delete_prompt", "delete_confirm",
    ]
    preset_type: str = ""
    preset_id: int = 0
