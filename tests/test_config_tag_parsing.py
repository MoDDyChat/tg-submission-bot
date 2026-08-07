"""Unit tests for the tag parsing fields in core/config.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import Config


def _build(**overrides: object) -> Config:
    """Build a Config from explicit values, bypassing the ambient environment."""
    values: dict[str, object] = {
        "bot": {"token": "123456:TEST"},
        "channel_id": -100111,
        "moderator_group_id": -100333,
        "moderator_ids": [111],
        "admin_ids": [],
    }
    values.update(overrides)
    return Config(**values)  # type: ignore[arg-type]


def test_tag_parsing_mode_defaults_to_suggest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAG_PARSING_MODE", raising=False)

    assert _build().tag_parsing_mode == "suggest"


def test_tag_parsing_strip_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAG_PARSING_STRIP_FROM_CAPTION", raising=False)

    assert _build().tag_parsing_strip_from_caption is False


@pytest.mark.parametrize("mode", ["off", "suggest", "auto"])
def test_tag_parsing_mode_accepts_valid_values(mode: str) -> None:
    cfg = _build(tag_parsing_mode=mode)

    assert cfg.tag_parsing_mode == mode


def test_tag_parsing_fields_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAG_PARSING_MODE", "auto")
    monkeypatch.setenv("TAG_PARSING_STRIP_FROM_CAPTION", "true")

    cfg = _build()

    assert cfg.tag_parsing_mode == "auto"
    assert cfg.tag_parsing_strip_from_caption is True


def test_invalid_tag_parsing_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _build(tag_parsing_mode="always")
