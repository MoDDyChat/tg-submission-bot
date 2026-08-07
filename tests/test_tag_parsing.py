from __future__ import annotations

from aiogram.types import MessageEntity

from services.tag_parsing import (
    FUZZY_THRESHOLD,
    SuggestedTag,
    deserialize_suggested,
    extract_hashtags,
    match_suggested_tags,
    serialize_suggested,
    strip_hashtag_lines,
)


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _hashtag(text: str, offset: int) -> MessageEntity:
    return MessageEntity(type="hashtag", offset=offset, length=_utf16_len(text))


def test_extract_hashtags_uses_entity_offsets_over_plain_text() -> None:
    text = "Смотрите #Art и #Арт"
    entities = [_hashtag("#Art", _utf16_len("Смотрите ")), _hashtag("#Арт", _utf16_len("Смотрите #Art и "))]

    assert extract_hashtags(text, entities) == ["Art", "Арт"]


def test_extract_hashtags_counts_utf16_units_before_emoji() -> None:
    # 🎨 is a surrogate pair: 2 UTF-16 units, so the offset is 3, not 2.
    text = "🎨 #Art"
    entities = [_hashtag("#Art", _utf16_len("🎨 "))]

    assert extract_hashtags(text, entities) == ["Art"]


def test_extract_hashtags_deduplicates_by_casefold_preserving_order() -> None:
    text = "#Art #art #Арт"
    entities = [
        _hashtag("#Art", 0),
        _hashtag("#art", _utf16_len("#Art ")),
        _hashtag("#Арт", _utf16_len("#Art #art ")),
    ]

    assert extract_hashtags(text, entities) == ["Art", "Арт"]


def test_extract_hashtags_returns_empty_for_missing_text_or_entities() -> None:
    assert extract_hashtags(None, None) == []
    assert extract_hashtags("", []) == []
    assert extract_hashtags("text", None) == []
    assert extract_hashtags(None, [_hashtag("#Art", 0)]) == []


def test_extract_hashtags_ignores_non_hashtag_entities() -> None:
    text = "text"
    entities = [MessageEntity(type="bold", offset=0, length=_utf16_len(text))]

    assert extract_hashtags(text, entities) == []


def test_match_suggested_tags_exact_by_tag() -> None:
    result = match_suggested_tags(["art"], [("art", "Художественное")])

    assert result == [SuggestedTag(raw="art", tag="art", exact=True)]


def test_match_suggested_tags_exact_by_label() -> None:
    result = match_suggested_tags(["Художественное"], [("art", "Художественное")])

    assert result == [SuggestedTag(raw="Художественное", tag="art", exact=True)]


def test_match_suggested_tags_exact_is_case_insensitive() -> None:
    result = match_suggested_tags(["ART"], [("art", "Art")])

    assert result == [SuggestedTag(raw="ART", tag="art", exact=True)]


def test_match_suggested_tags_fuzzy_by_tag() -> None:
    result = match_suggested_tags(["minecrafft"], [("minecraft", "Minecraft")])

    assert result == [SuggestedTag(raw="minecrafft", tag="minecraft", exact=False)]


def test_match_suggested_tags_fuzzy_by_label() -> None:
    result = match_suggested_tags(["Художественноео"], [("art", "Художественное")])

    assert result == [SuggestedTag(raw="Художественноео", tag="art", exact=False)]


def test_match_suggested_tags_below_threshold_is_unknown() -> None:
    result = match_suggested_tags(["zzzz"], [("art", "Художественное")])

    assert result == [SuggestedTag(raw="zzzz", tag=None, exact=False)]


def test_match_suggested_tags_ambiguous_fuzzy_returns_none() -> None:
    presets = [("abcd", "wxyz"), ("abce", "wxyz")]

    result = match_suggested_tags(["abc"], presets)

    assert result == [SuggestedTag(raw="abc", tag=None, exact=False)]


def test_match_suggested_tags_deduplicates_by_canonical_tag() -> None:
    result = match_suggested_tags(["Art", "ART", "Арт"], [("art", "Художественное")])

    assert result == [
        SuggestedTag(raw="Art", tag="art", exact=True),
        SuggestedTag(raw="Арт", tag=None, exact=False),
    ]


def test_match_suggested_tags_does_not_deduplicate_unknowns() -> None:
    result = match_suggested_tags(["zz", "zz"], [("art", "Художественное")])

    assert result == [
        SuggestedTag(raw="zz", tag=None, exact=False),
        SuggestedTag(raw="zz", tag=None, exact=False),
    ]


def test_match_suggested_tags_fuzzy_threshold_is_exported() -> None:
    assert FUZZY_THRESHOLD == 0.8


def test_strip_hashtag_lines_removes_leading_tag_block() -> None:
    assert strip_hashtag_lines("#One | #Two\n\nОписание") == "Описание"
    assert strip_hashtag_lines("#One,#Two\nОписание") == "Описание"
    assert strip_hashtag_lines("#One #Two\nОписание") == "Описание"


def test_strip_hashtag_lines_removes_trailing_tag_block() -> None:
    assert strip_hashtag_lines("Описание\n\n#One | #Two") == "Описание"


def test_strip_hashtag_lines_removes_leading_and_trailing_blocks() -> None:
    assert strip_hashtag_lines("#One\n\nОписание\n\n#Two") == "Описание"


def test_strip_hashtag_lines_keeps_tags_inside_sentence() -> None:
    assert strip_hashtag_lines("Крутая #работа ребята") == "Крутая #работа ребята"


def test_strip_hashtag_lines_keeps_tags_only_caption_unchanged() -> None:
    assert strip_hashtag_lines("#One\n#Two") == "#One\n#Two"
    assert strip_hashtag_lines("#One\n\n#Two") == "#One\n\n#Two"


def test_strip_hashtag_lines_keeps_lines_with_markup() -> None:
    assert strip_hashtag_lines("<b>#One</b>\nОписание") == "<b>#One</b>\nОписание"
    assert strip_hashtag_lines("#One <b>#Two</b>\nОписание") == "#One <b>#Two</b>\nОписание"


def test_strip_hashtag_lines_keeps_middle_tag_block() -> None:
    assert strip_hashtag_lines("Описание\n#One\nЕщё") == "Описание\n#One\nЕщё"


def test_strip_hashtag_lines_collapses_leftover_empty_lines() -> None:
    assert strip_hashtag_lines("#One\n\n\nОписание") == "Описание"
    assert strip_hashtag_lines("Описание\n\n\n#One") == "Описание"


def test_strip_hashtag_lines_handles_none_and_empty() -> None:
    assert strip_hashtag_lines(None) is None
    assert strip_hashtag_lines("") == ""


def test_serialize_suggested_round_trip() -> None:
    items = [
        SuggestedTag(raw="art", tag="art", exact=True),
        SuggestedTag(raw="неизвестный", tag=None, exact=False),
    ]

    data = serialize_suggested(items)

    assert data == [
        {"raw": "art", "tag": "art", "exact": True},
        {"raw": "неизвестный", "tag": None, "exact": False},
    ]
    assert deserialize_suggested(data) == items


def test_deserialize_suggested_handles_none() -> None:
    assert deserialize_suggested(None) == []
