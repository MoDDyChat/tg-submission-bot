"""Tag extraction, formatting, and composition utilities."""

import html
import re

_HTML_TAG_RE = re.compile(r"<[^>]+>")
MAX_MEDIA_CAPTION = 1024
MAX_TEXT_CAPTION = 4096
MAX_CUSTOM_TAG_LENGTH = 64
MAX_CUSTOM_TAGS_COUNT = 20

# Разделители между тегами — и в строке, которую пишет автор, и внутри
# «связки»: одного пресета, который ставит несколько тегов сразу
# («MineShield4 | #МайнШилд4» — эти два тега всегда идут вместе).
TAG_GROUP_SEPARATORS = "|,;·•/–—"

_GROUP_SPLIT_RE = re.compile(rf"[{re.escape(TAG_GROUP_SEPARATORS)}]+")
# Пробелы вокруг разделителя не разрывают связку: «A | #B» — это одна связка.
_TIGHTEN_SEPARATORS_RE = re.compile(rf"\s*([{re.escape(TAG_GROUP_SEPARATORS)}])\s*")


def strip_html_for_length(text: str) -> str:
    """Strip HTML tags to get plain text length (as Telegram counts it)."""
    return html.unescape(_HTML_TAG_RE.sub("", text))


def split_tag_group(tag: str) -> list[str]:
    """Связка → её отдельные теги: ``'MineShield4 | #МайнШилд4'`` → ``['MineShield4', 'МайнШилд4']``.

    Одиночный тег возвращается списком из одного элемента.
    """
    parts: list[str] = []
    for part in _GROUP_SPLIT_RE.split(tag):
        cleaned = part.strip().lstrip("#").strip()
        if cleaned:
            parts.append(cleaned)
    return parts


def canonical_tag_group(parts: list[str]) -> str:
    """Отдельные теги → канонический вид связки: ``'MineShield4 | #МайнШилд4'``.

    Первый тег без «#», остальные с ним — ровно так, как связка выглядит
    в готовой подписи, где ``format_tags_line`` добавляет «#» только в начало.
    """
    if not parts:
        return ""
    head, *rest = parts
    return " | ".join([head, *(f"#{part}" for part in rest)])


def parse_tag_group(text: str) -> str:
    """Ввод одного пресета → канонический тег или связка.

    Внутри поля тега пробел **склеивает**: весь ввод — это один пресет,
    ``'#MineShield4 #МайнШилд4'`` → ``'MineShield4 | #МайнШилд4'``.
    """
    parts: list[str] = []
    for chunk in text.split():
        parts.extend(split_tag_group(chunk))
    return canonical_tag_group(parts)


def parse_tags_input(text: str) -> list[str]:
    """Ввод нескольких тегов (кастомная страница визарда) → список тегов.

    Здесь пробел, наоборот, **разделяет**, а связка держится на разделителе:
    ``'#один #два'`` → два тега, ``'A | #B #два'`` → связка ``'A | #B'`` и тег ``'два'``.
    """
    tightened = _TIGHTEN_SEPARATORS_RE.sub(r"\1", text.strip())
    tags: list[str] = []
    for chunk in tightened.split():
        group = canonical_tag_group(split_tag_group(chunk))
        if group:
            tags.append(group)
    return tags


def dedupe_tags(tags: list[str]) -> list[str]:
    """Убрать теги, все части которых уже присутствуют выше по списку.

    Ловит и точный повтор, и одиночный тег, который уже покрыт связкой.
    Частичное пересечение (связка, одна половина которой уже стоит отдельно)
    оставляем как есть: потерять второй тег хуже, чем показать повтор.
    """
    result: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        parts = split_tag_group(tag) or [tag]
        folded = {part.casefold() for part in parts}
        if folded and folded <= seen:
            continue
        seen |= folded
        result.append(tag)
    return result



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
