"""Связки тегов: один пресет, который ставит несколько тегов сразу."""

from __future__ import annotations

import pytest

from db.queries.tag_presets import MAX_PRESET_TAG_LENGTH, _validate_tag_length
from handlers.moderator import management
from keyboards.callbacks import TagWizardCB
from keyboards.tags import tags_preset_page_kb
from tests.helpers import make_preset, make_section
from utils.tags import (
    canonical_tag_group,
    dedupe_tags,
    format_tags_line,
    parse_tag_group,
    parse_tags_input,
    split_tag_group,
)

GROUP = "MineShield4 | #МайнШилд4"


def test_split_tag_group_splits_on_separator_and_strips_hash() -> None:
    assert split_tag_group(GROUP) == ["MineShield4", "МайнШилд4"]
    assert split_tag_group("MineShieldArt") == ["MineShieldArt"]
    assert split_tag_group("A·#B/#C") == ["A", "B", "C"]


def test_canonical_tag_group_prefixes_every_tag_but_the_first() -> None:
    assert canonical_tag_group(["MineShield4", "МайнШилд4"]) == GROUP
    assert canonical_tag_group(["Solo"]) == "Solo"
    assert canonical_tag_group([]) == ""


def test_canonical_group_renders_exactly_like_separate_tags() -> None:
    """Инвариант: связка в подписи неотличима от тех же тегов по отдельности."""
    assert format_tags_line([GROUP]) == format_tags_line(["MineShield4", "МайнШилд4"])


def test_parse_tag_group_joins_everything_into_one_preset() -> None:
    # В поле тега пресета пробел склеивает: весь ввод — одна кнопка.
    assert parse_tag_group("#MineShield4 #МайнШилд4") == GROUP
    assert parse_tag_group("  #MineShieldArt  ") == "MineShieldArt"
    assert parse_tag_group("") == ""


def test_parse_tags_input_splits_on_space_but_keeps_groups() -> None:
    # В кастомном вводе пробел, наоборот, разделяет.
    assert parse_tags_input("#один #два") == ["один", "два"]
    assert parse_tags_input("MineShield4 | #МайнШилд4 #ещё") == [GROUP, "ещё"]
    assert parse_tags_input("A|B") == ["A | #B"]
    assert parse_tags_input("   ") == []


def test_parse_tags_input_round_trips_a_stored_group() -> None:
    """Связку, уехавшую в кастомные теги, можно ввести обратно как есть."""
    assert parse_tags_input(GROUP) == [GROUP]


def test_dedupe_tags_drops_fully_covered_tags() -> None:
    assert dedupe_tags([GROUP, "MineShield4"]) == [GROUP]
    assert dedupe_tags(["Art", "art"]) == ["Art"]
    assert dedupe_tags([GROUP, "Nerkin"]) == [GROUP, "Nerkin"]


def test_dedupe_tags_keeps_partially_overlapping_group() -> None:
    # Потерять второй тег хуже, чем показать повтор первого.
    assert dedupe_tags(["MineShield4", GROUP]) == ["MineShield4", GROUP]


def test_management_accepts_group_as_a_single_preset() -> None:
    label, tag, error = management._parse_new_preset("МайнШилд4 | #MineShield4 #МайнШилд4")

    assert error is None
    assert label == "МайнШилд4"
    assert tag == GROUP


def test_management_labels_group_by_its_first_tag_when_label_omitted() -> None:
    label, tag, error = management._parse_new_preset("#MineShield4 #МайнШилд4")

    assert error is None
    assert tag == GROUP
    assert label == "MineShield4"


def test_management_still_rejects_empty_tag() -> None:
    _, _, error = management._parse_new_preset("Метка | #")

    assert error is not None


def test_stored_groups_pass_validation() -> None:
    """4 связки, лежащие в проде, интерфейс раньше считал невалидными."""
    assert management._is_valid_tag(GROUP)
    assert management._normalize_tag(GROUP) == GROUP


def test_keyboard_callback_data_fits_telegram_limit_for_long_group() -> None:
    section = make_section(key="setting", label="Места действия", columns=3, sort_order=1)
    preset = make_preset(
        preset_id=42,
        preset_type="setting",
        label="МайнШилдАнимация",
        tag="MineShieldAnimation | #МайнШилдАнимация",
    )

    kb = tags_preset_page_kb(section, [preset], set(), can_go_back=False, has_next=True)
    callback_data = kb.inline_keyboard[0][0].callback_data

    assert len(callback_data.encode()) <= 64
    assert TagWizardCB.unpack(callback_data).value == "42"


def test_preset_tag_length_is_bounded_by_the_column_not_callback_data() -> None:
    _validate_tag_length("М" * MAX_PRESET_TAG_LENGTH)

    with pytest.raises(ValueError):
        _validate_tag_length("М" * (MAX_PRESET_TAG_LENGTH + 1))
