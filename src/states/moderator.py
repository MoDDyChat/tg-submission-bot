from typing import Literal

from aiogram.fsm.state import State, StatesGroup
from states.contact import ContactViewer


class ModeratorReview(StatesGroup):
    management_home = State()
    management_menu = State()
    management_presets = State()
    management_preset_section = State()
    management_preset_detail = State()
    management_banned_users = State()
    management_moderators = State()
    management_moderator_detail = State()
    management_enter_moderator_id = State()
    management_add_section_label = State()
    management_edit_section_label = State()
    management_add_preset_label = State()
    management_edit_preset_label = State()
    management_edit_preset_tag = State()
    viewing_post = State()
    editing_caption = State()
    editing_tags_presets = State()
    editing_tags_custom = State()
    editing_media = State()
    adding_media = State()
    entering_reject_reason = State()
    entering_ban_reason = State()
    picking_date = State()
    picking_hour = State()
    picking_minute = State()
    confirm_schedule = State()
    confirm_publish_now = State()
    submitting_post = State()


# Maps each FSM state string → its cancel-handling category.
# "management" — release management locks, return home.
# "viewing"    — full close: release submission lock, clear FSM.
# "sub"        — sub-state: extend lock, revert to viewing_post.
STATE_CATEGORY: dict[str, Literal["management", "viewing", "sub"]] = {
    ModeratorReview.management_home.state: "management",
    ModeratorReview.management_menu.state: "management",
    ModeratorReview.management_presets.state: "management",
    ModeratorReview.management_preset_section.state: "management",
    ModeratorReview.management_preset_detail.state: "management",
    ModeratorReview.management_banned_users.state: "management",
    ModeratorReview.management_moderators.state: "management",
    ModeratorReview.management_moderator_detail.state: "management",
    ModeratorReview.management_enter_moderator_id.state: "management",
    ModeratorReview.management_add_section_label.state: "management",
    ModeratorReview.management_edit_section_label.state: "management",
    ModeratorReview.management_add_preset_label.state: "management",
    ModeratorReview.management_edit_preset_label.state: "management",
    ModeratorReview.management_edit_preset_tag.state: "management",
    ModeratorReview.viewing_post.state: "viewing",
    ModeratorReview.editing_caption.state: "sub",
    ModeratorReview.editing_tags_presets.state: "sub",
    ModeratorReview.editing_tags_custom.state: "sub",
    ModeratorReview.editing_media.state: "sub",
    ModeratorReview.adding_media.state: "sub",
    ModeratorReview.entering_reject_reason.state: "sub",
    ModeratorReview.entering_ban_reason.state: "sub",
    ModeratorReview.picking_date.state: "sub",
    ModeratorReview.picking_hour.state: "sub",
    ModeratorReview.picking_minute.state: "sub",
    ModeratorReview.confirm_schedule.state: "sub",
    ModeratorReview.confirm_publish_now.state: "sub",
    ContactViewer.writing_message.state: "sub",
}
