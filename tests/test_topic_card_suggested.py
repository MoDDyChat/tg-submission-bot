"""Unit tests for the author-suggested tags hint on topic cards."""

from __future__ import annotations

from types import SimpleNamespace

from services import topics


def _fake_submission(**overrides) -> SimpleNamespace:
    sub = SimpleNamespace(
        id=1,
        status="pending",
        tags=[],
        caption="Описание",
        suggested_tags=None,
    )
    for key, value in overrides.items():
        setattr(sub, key, value)
    return sub


def _card_text(sub) -> str:
    return topics._format_topic_card_text(sub)


def test_card_shows_suggested_line_when_tags_empty() -> None:
    sub = _fake_submission(
        tags=[],
        suggested_tags=[{"raw": "art", "tag": "art", "exact": True}],
    )

    text = _card_text(sub)

    assert "<i>Автор предложил теги: #art</i>" in text


def test_card_hides_suggested_line_when_tags_set() -> None:
    sub = _fake_submission(
        tags=["art"],
        suggested_tags=[{"raw": "art", "tag": "art", "exact": True}],
    )

    text = _card_text(sub)

    assert "Автор предложил" not in text
    assert "<b>Теги:</b> #art" in text


def test_card_hides_suggested_line_when_suggested_tags_none() -> None:
    sub = _fake_submission(tags=[], suggested_tags=None)

    text = _card_text(sub)

    assert "Автор предложил" not in text


def test_card_escapes_html_in_unmatched_raw_tag() -> None:
    sub = _fake_submission(
        tags=[],
        suggested_tags=[{"raw": "<b>evil</b>", "tag": None, "exact": False}],
    )

    text = _card_text(sub)

    assert "&lt;b&gt;evil&lt;/b&gt;" in text
    assert "<b>evil</b>" not in text


def test_card_mixes_canonical_and_unmatched_tags() -> None:
    sub = _fake_submission(
        tags=[],
        suggested_tags=[
            {"raw": "MineShieldArt", "tag": "MineShieldArt", "exact": True},
            {"raw": "weird-tag", "tag": None, "exact": False},
        ],
    )

    text = _card_text(sub)

    assert "#MineShieldArt" in text
    assert "#weird-tag(?)" in text


def test_card_marks_fuzzy_guess_with_author_raw_tag() -> None:
    sub = _fake_submission(
        tags=[],
        suggested_tags=[{"raw": "MineShield4", "tag": "MineShield3D", "exact": False}],
    )

    text = _card_text(sub)

    assert "#MineShield4(≈#MineShield3D)" in text


def test_card_escapes_html_in_fuzzy_guess() -> None:
    sub = _fake_submission(
        tags=[],
        suggested_tags=[{"raw": "<i>raw</i>", "tag": "<b>tag</b>", "exact": False}],
    )

    text = _card_text(sub)

    assert "&lt;i&gt;raw&lt;/i&gt;(≈#&lt;b&gt;tag&lt;/b&gt;)" in text
    assert "<b>tag</b>" not in text
