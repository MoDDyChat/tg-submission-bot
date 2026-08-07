"""Tag parsing and matching for author-suggested tags — pure functions only.

No I/O, no state: suggested tags are extracted from the viewer's plain-text
caption via Telegram entities and fuzzy-matched against ``tag_presets`` at
intake time. ``strip_hashtag_lines`` optionally trims bare hashtag lines from
the HTML caption before it is stored.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from aiogram.enums import MessageEntityType
from aiogram.types import MessageEntity

FUZZY_THRESHOLD = 0.8

_BARE_HASHTAG_LINE_RE = re.compile(r"\s*#\S+(\s*[|,]?\s*#\S+)*\s*")


@dataclass(frozen=True)
class SuggestedTag:
    raw: str  # как прислал автор, без '#'
    tag: str | None  # канонический tag пресета или None
    exact: bool  # True — точное совпадение, False — fuzzy/нет совпадения


def extract_hashtags(text: str | None, entities: list[MessageEntity] | None) -> list[str]:
    if not text or not entities:
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for entity in entities:
        if entity.type != MessageEntityType.HASHTAG:
            continue
        raw = entity.extract_from(text)
        tag = raw[1:] if raw.startswith("#") else raw
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    return tags


def match_suggested_tags(raw_tags: list[str], presets: list[tuple[str, str]]) -> list[SuggestedTag]:
    folded_candidates = [
        (tag, text.casefold()) for tag, label in presets for text in (tag, label)
    ]
    results: list[SuggestedTag] = []
    matched_tags: set[str] = set()
    for raw in raw_tags:
        raw_folded = raw.casefold()
        exact_tag = next((tag for tag, text in folded_candidates if text == raw_folded), None)
        if exact_tag is not None:
            item = SuggestedTag(raw=raw, tag=exact_tag, exact=True)
        else:
            best_tag: str | None = None
            best_ratio = 0.0
            ambiguous = False
            for tag, text in folded_candidates:
                ratio = difflib.SequenceMatcher(None, raw_folded, text).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_tag = tag
                    ambiguous = False
                elif ratio == best_ratio and tag != best_tag:
                    ambiguous = True
            if best_ratio >= FUZZY_THRESHOLD and not ambiguous:
                item = SuggestedTag(raw=raw, tag=best_tag, exact=False)
            else:
                item = SuggestedTag(raw=raw, tag=None, exact=False)
        if item.tag is not None and item.tag in matched_tags:
            continue
        if item.tag is not None:
            matched_tags.add(item.tag)
        results.append(item)
    return results


def strip_hashtag_lines(html_caption: str | None) -> str | None:
    if not html_caption:
        return html_caption
    lines = html_caption.split("\n")

    def is_bare_tag_line(line: str) -> bool:
        return "<" not in line and _BARE_HASHTAG_LINE_RE.fullmatch(line) is not None

    start = 0
    while start < len(lines) and is_bare_tag_line(lines[start]):
        start += 1
    end = len(lines)
    while end > start and is_bare_tag_line(lines[end - 1]):
        end -= 1

    remainder = lines[start:end]
    while remainder and not remainder[0].strip():
        remainder.pop(0)
    while remainder and not remainder[-1].strip():
        remainder.pop()
    if not remainder:
        return html_caption
    return "\n".join(remainder)


def serialize_suggested(items: list[SuggestedTag]) -> list[dict[str, str | bool | None]]:
    return [{"raw": item.raw, "tag": item.tag, "exact": item.exact} for item in items]


def deserialize_suggested(data: list[dict] | None) -> list[SuggestedTag]:
    if not data:
        return []
    return [
        SuggestedTag(raw=entry.get("raw", ""), tag=entry.get("tag"), exact=bool(entry.get("exact", False)))
        for entry in data
    ]
