"""Unit-tests for core/topic_status_config.py."""

import json
from pathlib import Path

import pytest

from core.topic_status_config import (
    VALID_ICON_COLORS,
    TopicStatusConfig,
    TopicStatusStyle,
    _load,
    get_style,
)


class TestTopicStatusStyle:
    def test_basic_fields(self) -> None:
        s = TopicStatusStyle(label="TEST", icon_color=7322096, icon_custom_emoji_id=None)
        assert s.label == "TEST"
        assert s.icon_color == 7322096
        assert s.icon_custom_emoji_id is None

    def test_optional_fields_default_none(self) -> None:
        s = TopicStatusStyle(label="X")
        assert s.icon_color is None
        assert s.icon_custom_emoji_id is None

    def test_default_emoji(self) -> None:
        s = TopicStatusStyle(label="X")
        assert s.emoji == "❓"


class TestTopicStatusConfig:
    def _make(self, data: dict) -> TopicStatusConfig:
        return TopicStatusConfig(data)

    def test_get_known_status(self) -> None:
        cfg = self._make({"pending": {"label": "🆕 NEW", "icon_color": 16766590}})
        s = cfg.get_style("pending")
        assert s.label == "🆕 NEW"
        assert s.icon_color == 16766590

    def test_get_unknown_status_fallback(self) -> None:
        cfg = self._make({})
        s = cfg.get_style("nonexistent")
        assert s.label == "NONEXISTENT"
        assert s.icon_color is None

    def test_all_status_keys(self) -> None:
        cfg = self._make({"a": {"label": "A"}, "b": {"label": "B"}})
        assert set(cfg.all_status_keys()) == {"a", "b"}


class TestLoad:
    def test_load_defaults_when_no_file(self) -> None:
        cfg = _load(path=None)
        style = cfg.get_style("pending")
        assert "НОВОЕ" in style.label or "NEW" in style.label or style.label  # non-empty

    def test_load_defaults_cover_all_statuses(self) -> None:
        cfg = _load(path=None)
        for key in ("pending", "editing", "scheduled", "published", "rejected", "cancelled"):
            s = cfg.get_style(key)
            assert s.label, f"label missing for {key}"

    def test_defaults_have_emoji(self) -> None:
        cfg = _load(path=None)
        for key in ("pending", "editing", "scheduled", "published", "rejected", "cancelled"):
            s = cfg.get_style(key)
            assert s.emoji, f"emoji missing or empty for {key}"
        assert cfg.get_style("pending").emoji == "⏳"
        assert cfg.get_style("editing").emoji == "✏️"
        assert cfg.get_style("scheduled").emoji == "📅"

    def test_load_from_json_file(self, tmp_path: Path) -> None:
        data = {
            "pending": {"label": "PENDING_LABEL", "icon_color": 9367192, "icon_custom_emoji_id": None}
        }
        f = tmp_path / "statuses.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        cfg = _load(path=str(f))
        s = cfg.get_style("pending")
        assert s.label == "PENDING_LABEL"
        assert s.icon_color == 9367192

    def test_file_overrides_default_but_others_kept(self, tmp_path: Path) -> None:
        data = {"pending": {"label": "OVERRIDE"}}
        f = tmp_path / "s.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        cfg = _load(path=str(f))
        # overridden
        assert cfg.get_style("pending").label == "OVERRIDE"
        # non-overridden still present from defaults
        assert cfg.get_style("published").label  # non-empty

    def test_missing_file_uses_defaults(self) -> None:
        cfg = _load(path="/nonexistent/path/statuses.json")
        assert cfg.get_style("scheduled").label  # non-empty


class TestGetStyle:
    def test_module_get_style_returns_style(self) -> None:
        s = get_style("pending")
        assert isinstance(s, TopicStatusStyle)
        assert s.label

    def test_valid_icon_colors_palette(self) -> None:
        # All default styles should use valid palette colors or None
        cfg = _load(path=None)
        for key in cfg.all_status_keys():
            s = cfg.get_style(key)
            if s.icon_color is not None:
                assert s.icon_color in VALID_ICON_COLORS, (
                    f"Status '{key}' has invalid icon_color {s.icon_color}"
                )

    def test_unknown_status_fallback_emoji(self) -> None:
        s = get_style("nonexistent")
        assert s.emoji == "❓"


class TestPaletteValidatorAndDeepMerge:
    def test_invalid_icon_color_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="icon_color"):
            TopicStatusStyle(label="X", icon_color=999999)

    def test_partial_override_preserves_default_icon_color(self, tmp_path: Path) -> None:
        """Overriding only `label` should keep the default icon_color and icon_custom_emoji_id."""
        # Find a default status that has an icon_color set
        from core.topic_status_config import _DEFAULTS

        status_key, defaults = next(
            (k, v) for k, v in _DEFAULTS.items() if v.get("icon_color") is not None
        )
        original_color = defaults["icon_color"]

        data = {status_key: {"label": "OVERRIDE_ONLY_LABEL"}}
        f = tmp_path / "s.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        cfg = _load(path=str(f))
        s = cfg.get_style(status_key)

        assert s.label == "OVERRIDE_ONLY_LABEL"
        assert s.icon_color == original_color
