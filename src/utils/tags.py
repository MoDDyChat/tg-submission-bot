"""Tag extraction, formatting, and composition utilities."""

import html
import re

_HTML_TAG_RE = re.compile(r"<[^>]+>")
MAX_MEDIA_CAPTION = 1024
MAX_TEXT_CAPTION = 4096
MAX_CUSTOM_TAG_LENGTH = 64
MAX_CUSTOM_TAGS_COUNT = 20


def strip_html_for_length(text: str) -> str:
    """Strip HTML tags to get plain text length (as Telegram counts it)."""
    return html.unescape(_HTML_TAG_RE.sub("", text))



def format_tags_line(tags: list[str]) -> str:
    """Format tags for display: '#Tag1 | #Tag2 | #Tag3'."""
    if not tags:
        return ""
    return " | ".join(f"#{html.escape(tag)}" for tag in tags)


def compose_caption(tags: list[str], description: str | None) -> str:
    """Combine tags + description into final caption text."""
    tags_line = format_tags_line(tags)
    if tags_line and description:
        return f"{tags_line}\n\n{description}"
    return tags_line or description or ""


def validate_caption_length(tags: list[str], description: str | None, *, has_media: bool = True) -> bool:
    """Check if composed caption fits Telegram's limit (1024 for media, 4096 for text-only)."""
    limit = MAX_MEDIA_CAPTION if has_media else MAX_TEXT_CAPTION
    return len(strip_html_for_length(compose_caption(tags, description))) <= limit


def available_caption_length(tags: list[str], *, has_media: bool = True) -> int:
    """Сколько plain-text символов осталось под описание с учётом тегов."""
    limit = MAX_MEDIA_CAPTION if has_media else MAX_TEXT_CAPTION
    if not tags:
        return limit
    occupied = len(strip_html_for_length(compose_caption(tags, "x"))) - 1
    return max(0, limit - occupied)
