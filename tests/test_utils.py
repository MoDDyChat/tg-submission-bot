from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import core.messages as msg
from utils import formatting, html_entities, media, tags

from tests.helpers import make_photo_sizes


def test_strip_html_removes_tags_and_unescapes_entities() -> None:
    assert tags.strip_html_for_length("<b>Hello</b> &amp; <i>world</i>") == "Hello & world"


def test_format_tags_line_escapes_html() -> None:
    assert tags.format_tags_line(["A&B", "<tag>"]) == "#A&amp;B | #&lt;tag&gt;"


def test_compose_caption_combines_tags_and_description() -> None:
    assert tags.compose_caption(["One", "Two"], "Описание") == "#One | #Two\n\nОписание"


def test_compose_caption_handles_missing_parts() -> None:
    assert tags.compose_caption([], "Описание") == "Описание"
    assert tags.compose_caption(["One"], None) == "#One"
    assert tags.compose_caption([], None) == ""


def test_validate_caption_length_ignores_html_markup_for_media() -> None:
    assert tags.validate_caption_length([], f"<b>{'x' * 1024}</b>") is True
    assert tags.validate_caption_length([], f"<b>{'x' * 1025}</b>") is False


def test_validate_caption_length_uses_text_limit_for_text_only_posts() -> None:
    assert tags.validate_caption_length([], "x" * 4096, has_media=False) is True
    assert tags.validate_caption_length([], "x" * 4097, has_media=False) is False


def test_extract_media_info_uses_largest_photo() -> None:
    message = SimpleNamespace(
        photo=make_photo_sizes(("small", "small-uid"), ("large", "large-uid")),
        video=None,
        animation=None,
        document=None,
    )

    assert media.extract_media_info(message) == ("large", "large-uid", "photo")


def test_extract_media_info_supports_video_animation_and_document() -> None:
    video_message = SimpleNamespace(
        photo=None,
        video=SimpleNamespace(file_id="video", file_unique_id="video-uid"),
        animation=None,
        document=None,
    )
    animation_message = SimpleNamespace(
        photo=None,
        video=None,
        animation=SimpleNamespace(file_id="gif", file_unique_id="gif-uid"),
        document=None,
    )
    document_message = SimpleNamespace(
        photo=None,
        video=None,
        animation=None,
        document=SimpleNamespace(file_id="doc", file_unique_id="doc-uid"),
    )

    assert media.extract_media_info(video_message) == ("video", "video-uid", "video")
    assert media.extract_media_info(animation_message) == ("gif", "gif-uid", "animation")
    assert media.extract_media_info(document_message) == ("doc", "doc-uid", "document")


def test_extract_media_info_returns_none_for_unsupported_message() -> None:
    message = SimpleNamespace(photo=None, video=None, animation=None, document=None)

    assert media.extract_media_info(message) is None
    assert media.has_supported_media(message) is False


def test_get_html_caption_escapes_plain_caption() -> None:
    message = SimpleNamespace(caption="<b>unsafe</b>", caption_entities=None)

    assert html_entities.get_html_caption(message) == "&lt;b&gt;unsafe&lt;/b&gt;"


def test_get_html_text_escapes_plain_text() -> None:
    message = SimpleNamespace(text="<script>", entities=None)

    assert html_entities.get_html_text(message) == "&lt;script&gt;"


def test_format_submission_preview_includes_tags_and_caption() -> None:
    preview = formatting.format_submission_preview(
        caption="Описание",
        user_full_name="Full Name",
        username="name<admin>",
        media_count=2,
        status="pending",
        tags=["MineShield", "Art"],
    )

    assert "<b>От:</b> @name&lt;admin&gt;" in preview
    assert "<b>Медиа:</b> 2 файл(ов)" in preview
    assert "<b>Теги:</b> #MineShield | #Art" in preview
    assert "<b>Описание:</b>\nОписание" in preview


def test_format_author_name_keeps_short_names_untouched() -> None:
    assert formatting.format_author_name("Автор") == "Автор"


def test_format_author_name_caps_long_names_with_ellipsis() -> None:
    result = formatting.format_author_name("Очень длинное имя автора с хвостом")

    assert formatting._display_width(result) <= formatting.AUTHOR_NAME_MAX_WIDTH
    assert result.endswith("…")


def test_format_author_name_caps_wide_glyphs_by_display_width() -> None:
    """Emoji and CJK take two columns, so fewer of them fit than latin letters."""
    for wide in ("🌸🌟🍀" * 10, "文字" * 10, "🇷🇺" * 12):
        result = formatting.format_author_name(wide)

        assert formatting._display_width(result) <= formatting.AUTHOR_NAME_MAX_WIDTH
        assert result.endswith("…")


def test_format_author_name_collapses_whitespace() -> None:
    assert formatting.format_author_name("  multi\nline   name  ") == "multi line name"


def test_format_author_name_drops_invisible_padding_around_visible_text() -> None:
    assert formatting.format_author_name("ㅤㅤИмя🎩🌼ㅤㅤㅤㅤ") == "Имя🎩🌼"


def test_format_author_name_does_not_split_emoji_sequences() -> None:
    result = formatting.format_author_name("👨‍👩‍👧‍👦" * 4)

    assert formatting._display_width(result) <= formatting.AUTHOR_NAME_MAX_WIDTH
    assert result.count("👨") == result.count("👦")  # no half-built family left over


def test_format_author_name_keeps_flags_paired() -> None:
    result = formatting.format_author_name("🇷🇺" * 12)

    assert len(result.rstrip("…")) % 2 == 0  # regional indicators cut in pairs


def test_format_author_name_isolates_rtl_names() -> None:
    """RTL names must not reorder the entry number and link around them."""
    for rtl in ("סוכר [сахар]", "Nick٩( ᐛ )و"):
        result = formatting.format_author_name(rtl)

        # LRM outside the isolate: Telegram Desktop ignores FSI…PDI.
        assert result.startswith("‎⁨")
        assert result.endswith("⁩‎")


def test_format_author_name_leaves_ltr_names_unwrapped() -> None:
    result = formatting.format_author_name("Имя автора🎀")

    assert "⁨" not in result
    assert "‎" not in result


def test_format_author_name_falls_back_for_blank_names() -> None:
    for blank in ("", "   ", "ㅤ​"):
        assert formatting.format_author_name(blank) == msg.AUTHOR_NAME_FALLBACK
        assert formatting.format_author_name(blank, "nick") == "@nick"


def test_format_publication_summary_without_caption_uses_placeholder() -> None:
    summary = formatting.format_publication_summary(
        caption=None,
        publish_at=datetime(2026, 4, 15, 12, 30),
        tags=["One"],
    )

    assert "<b>Запланировано на:</b> 15.04.2026 12:30" in summary
    assert "<b>Теги:</b> #One" in summary
    assert "<i>Без описания</i>" in summary
