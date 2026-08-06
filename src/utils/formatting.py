import html
from datetime import datetime

import core.messages as msg
from db.models import User
from utils.tags import format_tags_line

HASHTAG_MAP: dict[str, str] = {
    "pending":    "#ожидает",
    "scheduled":  "#запланировано",
    "published":  "#опубликовано",
    "rejected":   "#отклонено",
    "cancelled":  "#отменено",
}


def user_mention(user: User) -> str:
    """Return an HTML-safe mention string for a user.

    Returns ``@username`` if available, otherwise the HTML-escaped full name.
    """
    if user.username:
        return f"@{html.escape(user.username)}"
    return html.escape(user.full_name)


def format_submission_preview(
    caption: str | None,
    user_full_name: str,
    username: str | None,
    media_count: int,
    status: str,
    tags: list[str] | None = None,
) -> str:
    """Format a submission for moderator preview."""
    user_display = html.escape(f"@{username}" if username else user_full_name)
    media_label = "Только текст" if media_count == 0 else f"{media_count} файл(ов)"
    lines = [
        f"<b>От:</b> {user_display}",
        f"<b>Медиа:</b> {media_label}",
        f"<b>Статус:</b> {status}",
    ]
    if tags:
        lines.append(f"\n<b>Теги:</b> {format_tags_line(tags)}")
    if caption:
        lines.append(f"\n<b>Описание:</b>\n{caption}")
    return "\n".join(lines)


def format_publication_summary(
    caption: str | None,
    publish_at: datetime,
    tags: list[str] | None = None,
) -> str:
    """Format a scheduled publication summary.

    ``publish_at`` must already be in the desired local timezone; no timezone
    conversion is performed here.
    """
    time_str = publish_at.strftime("%d.%m.%Y %H:%M")

    lines = [f"<b>Запланировано на:</b> {time_str}"]
    if tags:
        lines.append(f"\n<b>Теги:</b> {format_tags_line(tags)}")
    if caption:
        lines.append(f"\n<b>Описание:</b>\n{caption}")
    else:
        lines.append("\n<i>Без описания</i>")
    return "\n".join(lines)


def format_media_manager_text(sub_id: int, media: list) -> str:
    lines = [msg.MEDIA_MANAGER_TITLE.format(sub_id=sub_id, count=len(media))]
    for i, m in enumerate(media, start=1):
        label = msg.MEDIA_TYPE_LABELS.get(m.media_type, m.media_type)
        lines.append(f"{i}. {label}")
    return "\n".join(lines)
