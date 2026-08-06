from __future__ import annotations

from pathlib import Path
from string import Formatter

import pytest
import yaml

import core.messages as messages
from core.config import config
from core.exceptions import MessagesConfigError


def _dummy_format(template: str) -> str:
    fields = {f for _, f, _, _ in Formatter().parse(template) if f}
    return template.format(**{f: "x" for f in fields})


def test_all_defaults_are_format_safe() -> None:
    """Every plain-string default must .format() cleanly with its own
    placeholders filled in — catches stray/unbalanced braces."""
    for key, value in messages._DEFAULTS.items():
        if isinstance(value, str):
            _dummy_format(value)
        else:
            for sub_key, sub_value in value.items():
                assert isinstance(sub_value, str), f"{key}.{sub_key} is not a string"


def test_shipped_yaml_loads_and_validates() -> None:
    """config/messages.yaml (the file bot owners actually edit) must pass the
    same validation the loader runs at startup, and every value it defines
    must remain .format()-safe."""
    merged = messages._load(config.messages_path)
    for key, value in merged.items():
        if isinstance(value, str):
            _dummy_format(value)


def test_module_attributes_reflect_loaded_yaml() -> None:
    merged = messages._load(config.messages_path)
    for key, value in merged.items():
        assert getattr(messages, key) == value


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(MessagesConfigError, match="неизвестные ключи"):
        messages._validate_and_merge(
            messages._DEFAULTS, {"NOT_A_REAL_KEY": "x"}, source="test"
        )


def test_missing_placeholder_is_rejected() -> None:
    with pytest.raises(MessagesConfigError, match="не хватает"):
        messages._validate_and_merge(
            messages._DEFAULTS,
            {"REJECT_REASON_PROMPT": "Укажите причину отклонения.\nИли /cancel."},
            source="test",
        )


def test_extra_placeholder_is_rejected() -> None:
    with pytest.raises(MessagesConfigError, match="лишние"):
        messages._validate_and_merge(
            messages._DEFAULTS,
            {"CANCEL_OK": "Действие отменено {oops}."},
            source="test",
        )


def test_dict_value_wrong_type_is_rejected() -> None:
    with pytest.raises(MessagesConfigError, match="должен быть словарём"):
        messages._validate_and_merge(
            messages._DEFAULTS, {"MEDIA_TYPE_LABELS": "not a dict"}, source="test"
        )


def test_dict_unknown_subkey_is_rejected() -> None:
    with pytest.raises(MessagesConfigError, match="под-ключи"):
        messages._validate_and_merge(
            messages._DEFAULTS,
            {"MEDIA_TYPE_LABELS": {"sticker": "🌟 Стикер"}},
            source="test",
        )


def test_scalar_key_with_non_string_value_is_rejected() -> None:
    with pytest.raises(MessagesConfigError, match="должен быть строкой"):
        messages._validate_and_merge(
            messages._DEFAULTS, {"CANCEL_OK": 123}, source="test"
        )


def test_valid_override_merges_and_keeps_other_defaults() -> None:
    merged = messages._validate_and_merge(
        messages._DEFAULTS, {"CANCEL_OK": "Отменено!"}, source="test"
    )
    assert merged["CANCEL_OK"] == "Отменено!"
    assert merged["CANCEL_NOTHING"] == messages._DEFAULTS["CANCEL_NOTHING"]


def test_valid_dict_subkey_override_keeps_other_subkeys() -> None:
    merged = messages._validate_and_merge(
        messages._DEFAULTS,
        {"MEDIA_TYPE_LABELS": {"photo": "📷 Изображение"}},
        source="test",
    )
    assert merged["MEDIA_TYPE_LABELS"]["photo"] == "📷 Изображение"
    assert (
        merged["MEDIA_TYPE_LABELS"]["video"]
        == messages._DEFAULTS["MEDIA_TYPE_LABELS"]["video"]
    )


def test_missing_file_falls_back_to_defaults(tmp_path: Path) -> None:
    assert messages._load(str(tmp_path / "nope.yaml")) == messages._DEFAULTS


def test_non_dict_yaml_root_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "messages.yaml"
    p.write_text(yaml.safe_dump(["not", "a", "dict"]), encoding="utf-8")
    with pytest.raises(MessagesConfigError, match="словарь"):
        messages._load(str(p))
