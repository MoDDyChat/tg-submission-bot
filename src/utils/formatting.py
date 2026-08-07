import html
import re
import unicodedata
from datetime import datetime

import core.messages as msg
from db.models import User
from utils.tags import format_tags_line

# Author names come straight from Telegram profiles: they can be arbitrarily
# long, whitespace-only or padded with invisible filler characters. Board lines
# (queue/schedule) cap them so one author cannot break the layout. The cap is a
# *display width* budget, not a character count: emoji and CJK take two columns,
# so "22 characters" of emoji would render twice as wide as 22 latin letters.
AUTHOR_NAME_MAX_WIDTH = 18
_ELLIPSIS = "…"
_ZWJ = chr(0x200D)

# Bidi guards. A name carrying strong RTL text (Hebrew, Arabic) reorders
# everything the line puts after it — the entry number, the "(#id)" and the
# link all swap places. FSI…PDI is the correct isolate, but Telegram Desktop
# ignores it, so the name is additionally fenced with LRM: a strong LTR
# character every renderer honours, which stops the RTL run from swallowing
# the neutral text (brackets, digits, arrows) around it.
_FSI = chr(0x2068)
_PDI = chr(0x2069)
_LRM = chr(0x200E)
_RTL_BIDI_CLASSES = frozenset({"R", "AL", "AN"})

# Blank-looking characters outside the Cc/Cf/whitespace classes: Hangul
# fillers (U+115F, U+1160, U+3164, U+FFA0) and the Braille blank (U+2800).
_BLANK_CODEPOINTS = frozenset({0x115F, 0x1160, 0x2800, 0x3164, 0xFFA0})

_WHITESPACE_RUN = re.compile(r"\s+")

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


def _is_invisible_char(ch: str) -> bool:
    """True for characters that render as nothing but still count as text.

    ZWJ is excluded on purpose — dropping it would explode composite emoji
    (👨‍👩‍👧‍👦) into their separate parts.
    """
    if ch == _ZWJ:
        return False
    return unicodedata.category(ch) in ("Cc", "Cf") or ord(ch) in _BLANK_CODEPOINTS


def _char_width(ch: str) -> int:
    """Display width of a single character in terminal-ish columns (0, 1 or 2)."""
    if _is_invisible_char(ch) or ch == _ZWJ:
        return 0
    if unicodedata.category(ch) in ("Mn", "Mc", "Me"):
        return 0  # combining marks stack onto the previous glyph
    code = ord(ch)
    if 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF:
        return 0  # variation selectors
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    if _is_wide_emoji(ch):
        return 2
    return 1


def _is_wide_emoji(ch: str) -> bool:
    """True for pictographs Telegram renders at roughly two columns wide."""
    code = ord(ch)
    return (
        0x1F300 <= code <= 0x1FAFF
        or 0x1F000 <= code <= 0x1F0FF
        or 0x1F1E6 <= code <= 0x1F1FF  # regional indicators (flags)
        or 0x2600 <= code <= 0x27BF
        or 0x2B00 <= code <= 0x2BFF
    )


def _display_width(text: str) -> int:
    """Total display width, counting each grapheme once (a ZWJ family is one glyph)."""
    return sum(_cluster_width(cluster) for cluster in _grapheme_clusters(text))


def _is_grapheme_continuation(ch: str) -> bool:
    """True for characters that must stay glued to the one before them."""
    return (
        unicodedata.category(ch) in ("Mn", "Mc", "Me")
        or ch == _ZWJ
        or 0xFE00 <= ord(ch) <= 0xFE0F  # variation selectors
        or 0x1F3FB <= ord(ch) <= 0x1F3FF  # emoji skin tone modifiers
        or 0xE0100 <= ord(ch) <= 0xE01EF  # variation selectors supplement
    )


def _is_regional_indicator(ch: str) -> bool:
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF


def _grapheme_clusters(text: str) -> list[str]:
    """Split ``text`` into user-perceived characters.

    Good enough for display trimming: combining marks and ZWJ sequences stay
    with their base, and regional indicators pair up into single flags.
    """
    clusters: list[str] = []
    i = 0
    while i < len(text):
        start = i
        i += 1
        if _is_regional_indicator(text[start]) and i < len(text) and _is_regional_indicator(text[i]):
            i += 1
        while i < len(text) and (_is_grapheme_continuation(text[i]) or text[i - 1] == _ZWJ):
            i += 1
        clusters.append(text[start:i])
    return clusters


def _cluster_width(cluster: str) -> int:
    """Width of one grapheme: the widest part wins — it renders as one glyph."""
    return max((_char_width(ch) for ch in cluster), default=0)


def _truncate_name(text: str, max_width: int) -> str:
    """Cut ``text`` to ``max_width`` display columns, ellipsis included.

    Cuts land on grapheme boundaries so composite emoji and combining marks
    stay intact.
    """
    if _display_width(text) <= max_width:
        return text

    budget = max_width - 1  # leave a column for the ellipsis
    kept: list[str] = []
    width = 0
    for cluster in _grapheme_clusters(text):
        cluster_width = _cluster_width(cluster)
        if width + cluster_width > budget and kept:
            break
        kept.append(cluster)
        width += cluster_width
        if width >= budget:
            break

    trimmed = "".join(kept).rstrip() or "".join(kept)
    return f"{trimmed}{_ELLIPSIS}"


def _sanitize_name(text: str) -> str:
    """Drop invisible padding and collapse whitespace to a single line."""
    # Whitespace collapses first: newlines and tabs are Cc controls, and
    # dropping them outright would glue neighbouring words together.
    single_line = _WHITESPACE_RUN.sub(" ", text)
    stripped = "".join(ch for ch in single_line if not _is_invisible_char(ch))
    return _WHITESPACE_RUN.sub(" ", stripped).strip()


def format_author_name(
    full_name: str | None,
    username: str | None = None,
    *,
    max_width: int = AUTHOR_NAME_MAX_WIDTH,
) -> str:
    """Return a board-safe author name: single-line, width-capped, never blank.

    Invisible padding (zero-width characters, Hangul/Braille fillers, bidi
    controls) is dropped wherever it sits, not only when the whole name is made
    of it — a leading run of ``U+3164`` would otherwise shift the column.
    Names left with nothing visible fall back to ``@username`` and then to
    ``AUTHOR_NAME_FALLBACK``. Names containing strong RTL text are wrapped in a
    bidi isolate so they cannot reorder the rest of the line. The result is NOT
    HTML-escaped — callers do that.
    """
    name = _sanitize_name(full_name or "")
    if _display_width(name) == 0:
        name = f"@{username}" if username else msg.AUTHOR_NAME_FALLBACK
    return _isolate_bidi(_truncate_name(name, max_width))


def _isolate_bidi(text: str) -> str:
    """Fence ``text`` off if it could reorder its surroundings."""
    if not any(unicodedata.bidirectional(ch) in _RTL_BIDI_CLASSES for ch in text):
        return text
    return f"{_LRM}{_FSI}{text}{_PDI}{_LRM}"


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
