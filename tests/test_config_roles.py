"""Unit tests for the MODERATOR_IDS / ADMIN_IDS relationship in core/config.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import Config
from filters.is_moderator import IsModerator
from tests.helpers import make_message


def _build(**overrides: object) -> Config:
    """Build a Config from explicit values, bypassing the ambient environment."""
    values: dict[str, object] = {
        "bot": {"token": "123456:TEST"},
        "channel_id": -100111,
        "moderator_group_id": -100333,
        "moderator_ids": [],
        "admin_ids": [],
    }
    values.update(overrides)
    return Config(**values)  # type: ignore[arg-type]


# ── admins are moderators implicitly ──────────────────────────────

def test_admin_not_listed_as_moderator_gains_moderator_rights() -> None:
    cfg = _build(moderator_ids=[111, 222], admin_ids=[999])

    assert cfg.moderator_ids == [111, 222, 999]
    assert cfg.admin_ids == [999]


def test_admin_already_listed_is_not_duplicated() -> None:
    cfg = _build(moderator_ids=[111, 222], admin_ids=[222])

    assert cfg.moderator_ids == [111, 222]


def test_repeated_admin_id_is_added_once() -> None:
    cfg = _build(moderator_ids=[111], admin_ids=[999, 999])

    assert cfg.moderator_ids == [111, 999]


def test_admins_only_config_is_valid() -> None:
    cfg = _build(admin_ids=[555])

    assert cfg.moderator_ids == [555]
    assert cfg.moderator_id == 555


def test_moderator_id_keeps_the_first_explicit_moderator() -> None:
    cfg = _build(moderator_ids=[111, 222], admin_ids=[999])

    assert cfg.moderator_id == 111


def test_config_without_any_ids_is_rejected() -> None:
    with pytest.raises(ValidationError, match="MODERATOR_IDS или ADMIN_IDS"):
        _build(moderator_ids=[], admin_ids=[])


# ── the filter sees the merged list ───────────────────────────────

async def test_is_moderator_passes_for_admin_absent_from_moderator_ids(monkeypatch) -> None:
    cfg = _build(moderator_ids=[111], admin_ids=[999])

    from core import config as cfg_module
    monkeypatch.setattr(cfg_module.config, "moderator_ids", cfg.moderator_ids)

    assert await IsModerator()(make_message(from_user_id=999)) is True
