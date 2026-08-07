"""Public re-exports for ``db.queries``.

All callers use ``from db.queries import <function>``; this package init
re-exports every public symbol so existing imports continue to work unchanged.
"""

from db.queries.messages import create_message
from db.queries.moderators import count_admins, list_moderators, set_roles
from db.queries.publications import (
    count_recent_publications,
    create_publication,
    delete_publication,
    get_publication,
    get_publication_by_submission,
    get_unpublished_publications,
    list_dead_publications,
    mark_publication_dead,
    mark_published,
    update_publication_time,
)
from db.queries.submission_media import add_media, delete_media, get_submission_media
from db.queries.submissions import (
    count_recent_rejections,
    count_submissions_by_status,
    create_submission,
    get_active_submissions,
    get_submission,
    get_submission_by_topic_card_id,
    get_submission_with_user,
    update_submission_caption,
    update_submission_status,
    update_submission_tags,
)
from db.queries.system_messages import (
    delete_system_message,
    get_system_message,
    list_system_messages_by_prefix,
    upsert_system_message,
)
from db.queries.tag_presets import (
    create_tag_preset,
    create_tag_preset_section,
    delete_tag_preset,
    delete_tag_preset_section,
    find_tag_preset_conflicts,
    get_tag_preset,
    get_tag_preset_section,
    get_tag_preset_section_by_label,
    list_tag_preset_sections,
    list_tag_presets,
    list_tag_presets_grouped,
    update_tag_preset,
    update_tag_preset_section,
)
from db.queries.topics import (
    clear_topic_card_ids,
    delete_user_topic,
    enqueue_topic_title_sync,
    ensure_topic_title_sync_pending,
    get_user_topic,
    list_cards_for_reconcile,
    mark_card_rendered,
    mark_card_rendered_if_unchanged,
    mark_topic_title_externally_drifted,
    mark_topic_title_sync_applied,
    save_topic_card_ids,
    update_user_topic_status,
    upsert_user_topic,
)
from db.queries.users import (
    AuthorStats,
    ban_user,
    get_admin_users,
    get_author_stats,
    get_banned_users,
    get_or_create_user,
    get_user_by_id,
    get_user_by_telegram_id,
    get_users_by_username,
    set_moderator_note,
    unban_user,
)

__all__ = [
    # messages
    "create_message",
    # moderators
    "count_admins",
    "list_moderators",
    "set_roles",
    # publications
    "count_recent_publications",
    "create_publication",
    "delete_publication",
    "get_publication",
    "get_publication_by_submission",
    "get_unpublished_publications",
    "list_dead_publications",
    "mark_publication_dead",
    "mark_published",
    "update_publication_time",
    # submission_media
    "add_media",
    "delete_media",
    "get_submission_media",
    # submissions
    "count_recent_rejections",
    "count_submissions_by_status",
    "create_submission",
    "get_active_submissions",
    "get_submission",
    "get_submission_by_topic_card_id",
    "get_submission_with_user",
    "update_submission_caption",
    "update_submission_status",
    "update_submission_tags",
    # system_messages
    "delete_system_message",
    "get_system_message",
    "list_system_messages_by_prefix",
    "upsert_system_message",
    # tag_presets
    "create_tag_preset",
    "create_tag_preset_section",
    "delete_tag_preset",
    "delete_tag_preset_section",
    "find_tag_preset_conflicts",
    "get_tag_preset",
    "get_tag_preset_section",
    "get_tag_preset_section_by_label",
    "list_tag_preset_sections",
    "list_tag_presets",
    "list_tag_presets_grouped",
    "update_tag_preset",
    "update_tag_preset_section",
    # topics
    "clear_topic_card_ids",
    "delete_user_topic",
    "enqueue_topic_title_sync",
    "ensure_topic_title_sync_pending",
    "get_user_topic",
    "list_cards_for_reconcile",
    "mark_card_rendered",
    "mark_card_rendered_if_unchanged",
    "mark_topic_title_externally_drifted",
    "mark_topic_title_sync_applied",
    "save_topic_card_ids",
    "update_user_topic_status",
    "upsert_user_topic",
    # users
    "AuthorStats",
    "ban_user",
    "get_admin_users",
    "get_author_stats",
    "get_banned_users",
    "get_or_create_user",
    "get_user_by_id",
    "get_user_by_telegram_id",
    "get_users_by_username",
    "set_moderator_note",
    "unban_user",
]
