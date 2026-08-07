"""Tests for suggested-tag parsing at submission intake."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from aiogram.types import MessageEntity

from services import submission_intake
from services.tag_parsing import SuggestedTag, serialize_suggested
from tests.helpers import (
    FakeSessionFactory,
    make_message,
    make_preset,
    make_photo_sizes,
    make_submission,
    make_user,
)


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _hashtag(text: str, offset: int) -> MessageEntity:
    return MessageEntity(type="hashtag", offset=offset, length=_utf16_len(text))


def _presets():
    return {
        "category": [make_preset(preset_id=1, preset_type="category", label="Арт", tag="art")],
        "game": [make_preset(preset_id=2, preset_type="game", label="Minecraft", tag="minecraft")],
    }


# ── _parse_suggested_tags: режимы и strip ─────────────────────────────

async def test_parse_off_returns_none_and_skips_db(monkeypatch) -> None:
    monkeypatch.setattr(submission_intake.config, "tag_parsing_mode", "off")
    monkeypatch.setattr(submission_intake.config, "tag_parsing_strip_from_caption", True)
    presets_mock = AsyncMock()
    monkeypatch.setattr(submission_intake, "list_tag_presets_grouped", presets_mock)

    result = await submission_intake._parse_suggested_tags(
        AsyncMock(),
        plain_text="Смотрите #art",
        entities=[_hashtag("#art", _utf16_len("Смотрите "))],
        html_caption="Смотрите #art",
        has_media=True,
    )

    assert result == (None, "Смотрите #art", [])
    presets_mock.assert_not_awaited()


async def test_parse_suggest_fills_suggested_tags(monkeypatch) -> None:
    monkeypatch.setattr(submission_intake.config, "tag_parsing_mode", "suggest")
    monkeypatch.setattr(submission_intake.config, "tag_parsing_strip_from_caption", False)
    monkeypatch.setattr(
        submission_intake, "list_tag_presets_grouped", AsyncMock(return_value=_presets())
    )

    result = await submission_intake._parse_suggested_tags(
        AsyncMock(),
        plain_text="Смотрите #art и #неизвестный",
        entities=[
            _hashtag("#art", _utf16_len("Смотрите ")),
            _hashtag("#неизвестный", _utf16_len("Смотрите #art и ")),
        ],
        html_caption="Смотрите #art и #неизвестный",
        has_media=True,
    )

    assert result[0] == serialize_suggested([
        SuggestedTag(raw="art", tag="art", exact=True),
        SuggestedTag(raw="неизвестный", tag=None, exact=False),
    ])
    assert result[1] == "Смотрите #art и #неизвестный"
    assert result[2] == []


async def test_parse_without_hashtags_returns_none_and_skips_db(monkeypatch) -> None:
    monkeypatch.setattr(submission_intake.config, "tag_parsing_mode", "suggest")
    presets_mock = AsyncMock()
    monkeypatch.setattr(submission_intake, "list_tag_presets_grouped", presets_mock)

    result = await submission_intake._parse_suggested_tags(
        AsyncMock(),
        plain_text="Просто текст",
        entities=None,
        html_caption="Просто текст",
        has_media=True,
    )

    assert result == (None, "Просто текст", [])
    presets_mock.assert_not_awaited()


async def test_parse_strip_trims_bare_hashtag_lines_from_caption(monkeypatch) -> None:
    monkeypatch.setattr(submission_intake.config, "tag_parsing_mode", "suggest")
    monkeypatch.setattr(submission_intake.config, "tag_parsing_strip_from_caption", True)
    monkeypatch.setattr(
        submission_intake, "list_tag_presets_grouped", AsyncMock(return_value=_presets())
    )

    result = await submission_intake._parse_suggested_tags(
        AsyncMock(),
        plain_text="#art | #фан\n\nОписание",
        entities=[
            _hashtag("#art", 0),
            _hashtag("#фан", _utf16_len("#art | ")),
        ],
        html_caption="#art | #фан\n\nОписание",
        has_media=True,
    )

    assert result[1] == "Описание"
    assert result[0] == serialize_suggested([
        SuggestedTag(raw="art", tag="art", exact=True),
        SuggestedTag(raw="фан", tag=None, exact=False),
    ])


async def test_parse_strip_off_keeps_caption(monkeypatch) -> None:
    monkeypatch.setattr(submission_intake.config, "tag_parsing_mode", "suggest")
    monkeypatch.setattr(submission_intake.config, "tag_parsing_strip_from_caption", False)
    monkeypatch.setattr(
        submission_intake, "list_tag_presets_grouped", AsyncMock(return_value=_presets())
    )

    result = await submission_intake._parse_suggested_tags(
        AsyncMock(),
        plain_text="#art\n\nОписание",
        entities=[_hashtag("#art", 0)],
        html_caption="#art\n\nОписание",
        has_media=True,
    )

    assert result[1] == "#art\n\nОписание"


async def test_parse_auto_passes_only_exact_matches_to_tags(monkeypatch) -> None:
    monkeypatch.setattr(submission_intake.config, "tag_parsing_mode", "auto")
    monkeypatch.setattr(submission_intake.config, "tag_parsing_strip_from_caption", False)
    monkeypatch.setattr(
        submission_intake, "list_tag_presets_grouped", AsyncMock(return_value=_presets())
    )

    result = await submission_intake._parse_suggested_tags(
        AsyncMock(),
        plain_text="#art #minecrafft #неизвестный",
        entities=[
            _hashtag("#art", 0),
            _hashtag("#minecrafft", _utf16_len("#art ")),
            _hashtag("#неизвестный", _utf16_len("#art #minecrafft ")),
        ],
        html_caption="#art #minecrafft #неизвестный",
        has_media=False,
    )

    assert result[2] == ["art"]
    assert result[0] == serialize_suggested([
        SuggestedTag(raw="art", tag="art", exact=True),
        SuggestedTag(raw="minecrafft", tag="minecraft", exact=False),
        SuggestedTag(raw="неизвестный", tag=None, exact=False),
    ])


async def test_parse_auto_degrades_to_suggest_when_caption_overflows(monkeypatch) -> None:
    monkeypatch.setattr(submission_intake.config, "tag_parsing_mode", "auto")
    monkeypatch.setattr(submission_intake.config, "tag_parsing_strip_from_caption", False)
    monkeypatch.setattr(
        submission_intake, "list_tag_presets_grouped", AsyncMock(return_value=_presets())
    )
    monkeypatch.setattr(
        submission_intake, "validate_caption_length", lambda tags, caption, **kwargs: False
    )

    result = await submission_intake._parse_suggested_tags(
        AsyncMock(),
        plain_text="Описание #art",
        entities=[_hashtag("#art", _utf16_len("Описание "))],
        html_caption="Описание #art",
        has_media=True,
    )

    assert result[2] == []
    assert result[0] == serialize_suggested([SuggestedTag(raw="art", tag="art", exact=True)])


# ── Интеграция в три пути приёма поста ───────────────────────────────

async def test_submit_text_passes_suggested_tags(monkeypatch) -> None:
    session = AsyncMock()
    db_user = make_user()
    caption = "Смотрите #art"
    message = make_message(text=caption, entities=[_hashtag("#art", _utf16_len("Смотрите "))])
    monkeypatch.setattr(submission_intake, "get_html_text", lambda m: caption)
    monkeypatch.setattr(
        submission_intake, "list_tag_presets_grouped", AsyncMock(return_value=_presets())
    )
    monkeypatch.setattr(submission_intake.config, "tag_parsing_mode", "suggest")
    monkeypatch.setattr(submission_intake.config, "tag_parsing_strip_from_caption", False)
    monkeypatch.setattr(
        submission_intake,
        "create_submission",
        AsyncMock(return_value=make_submission(sub_id=21, user=db_user)),
    )
    monkeypatch.setattr(submission_intake, "get_submission_with_user", AsyncMock(return_value=None))

    await submission_intake.submit_text(message, session, db_user)

    kwargs = submission_intake.create_submission.await_args.kwargs
    assert kwargs["caption"] == caption
    assert kwargs["suggested_tags"] == serialize_suggested([
        SuggestedTag(raw="art", tag="art", exact=True),
    ])
    assert kwargs["tags"] == []


async def test_submit_single_media_auto_passes_exact_tags(monkeypatch) -> None:
    session = AsyncMock()
    db_user = make_user()
    caption = "Описание #art"
    message = make_message(
        photo=make_photo_sizes(("f1", "u1")),
        caption=caption,
        caption_entities=[_hashtag("#art", _utf16_len("Описание "))],
    )
    monkeypatch.setattr(submission_intake, "get_html_caption", lambda m: caption)
    monkeypatch.setattr(
        submission_intake, "list_tag_presets_grouped", AsyncMock(return_value=_presets())
    )
    monkeypatch.setattr(submission_intake.config, "tag_parsing_mode", "auto")
    monkeypatch.setattr(submission_intake.config, "tag_parsing_strip_from_caption", False)
    monkeypatch.setattr(
        submission_intake,
        "create_submission",
        AsyncMock(return_value=make_submission(sub_id=22, user=db_user)),
    )
    monkeypatch.setattr(submission_intake, "add_media", AsyncMock())
    monkeypatch.setattr(submission_intake, "get_submission_with_user", AsyncMock(return_value=None))

    await submission_intake.submit_single_media(message, session, db_user)

    kwargs = submission_intake.create_submission.await_args.kwargs
    assert kwargs["caption"] == caption
    assert kwargs["suggested_tags"] == serialize_suggested([
        SuggestedTag(raw="art", tag="art", exact=True),
    ])
    assert kwargs["tags"] == ["art"]


async def _no_sleep(_: float) -> None:
    return None


async def test_finalize_media_group_strips_caption_and_saves_suggested_tags(monkeypatch) -> None:
    group_id = "album-tags"
    db_user = make_user()
    session = AsyncMock()
    factory = FakeSessionFactory(session)
    caption = "#art | #фан\n\nОписание альбома"
    first = make_message(
        message_id=10,
        caption=caption,
        caption_entities=[
            _hashtag("#art", 0),
            _hashtag("#фан", _utf16_len("#art | ")),
        ],
        photo=make_photo_sizes(("f1", "u1")),
        media_group_id=group_id,
    )
    second = make_message(
        message_id=20,
        photo=make_photo_sizes(("f2", "u2")),
        media_group_id=group_id,
        bot=first.bot,
    )
    submission_intake._media_group_buffers[group_id] = [second, first]
    submission_intake._media_group_locks[group_id] = asyncio.Lock()
    submission_intake._media_group_timestamps[group_id] = submission_intake.time.monotonic()

    monkeypatch.setattr(submission_intake.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(submission_intake, "get_html_caption", lambda m: m.caption)
    monkeypatch.setattr(
        submission_intake, "list_tag_presets_grouped", AsyncMock(return_value=_presets())
    )
    monkeypatch.setattr(submission_intake.config, "tag_parsing_mode", "suggest")
    monkeypatch.setattr(submission_intake.config, "tag_parsing_strip_from_caption", True)
    monkeypatch.setattr(
        submission_intake,
        "create_submission",
        AsyncMock(return_value=make_submission(sub_id=23, user=db_user)),
    )
    monkeypatch.setattr(submission_intake, "add_media", AsyncMock())
    monkeypatch.setattr(submission_intake, "get_submission_with_user", AsyncMock(return_value=None))

    await submission_intake._finalize_media_group(group_id, factory, db_user)

    kwargs = submission_intake.create_submission.await_args.kwargs
    assert kwargs["caption"] == "Описание альбома"
    assert kwargs["suggested_tags"] == serialize_suggested([
        SuggestedTag(raw="art", tag="art", exact=True),
        SuggestedTag(raw="фан", tag=None, exact=False),
    ])
    assert kwargs["tags"] == []
