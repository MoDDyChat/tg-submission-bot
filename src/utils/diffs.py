"""Diff helpers for topic-card change notifications."""

import html

from utils.tags import strip_html_for_length


def caption_diff(old: str | None, new: str | None, *, max_len: int = 500) -> str:
    """Return an HTML-safe before/after block for a caption change notification.

    HTML in *old* and *new* is stripped before display to keep the diff readable.
    Each side is capped at *max_len* characters.
    """
    old_plain = strip_html_for_length(old) if old else "(пусто)"
    new_plain = strip_html_for_length(new) if new else "(пусто)"

    if len(old_plain) > max_len:
        old_plain = old_plain[:max_len] + "…"
    if len(new_plain) > max_len:
        new_plain = new_plain[:max_len] + "…"

    return (
        f"<b>Было:</b>\n{html.escape(old_plain)}"
        f"\n\n<b>Стало:</b>\n{html.escape(new_plain)}"
    )


def tags_diff(old_tags: list[str] | None, new_tags: list[str] | None) -> str:
    """Return a human-readable +/− diff for tag changes in a notification.

    Added tags are shown as ``+#tag``, removed as ``−#tag``.
    Returns ``(без изменений)`` when the sets are identical.
    """
    old_set = set(old_tags or [])
    new_set = set(new_tags or [])
    added = sorted(new_set - old_set)
    removed = sorted(old_set - new_set)

    parts: list[str] = []
    if added:
        parts.append("  ".join(f"+#{html.escape(t)}" for t in added))
    if removed:
        parts.append("  ".join(f"−#{html.escape(t)}" for t in removed))
    return "\n".join(parts) if parts else "(без изменений)"
