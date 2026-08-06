"""Unit tests for filters/is_admin.py."""

from __future__ import annotations

import os


# Ensure config admin_ids are set for testing
os.environ.setdefault("ADMIN_IDS", "1")


from filters.is_admin import IsAdmin
from tests.helpers import make_callback, make_message


# ── IsAdmin filter ────────────────────────────────────────────────

async def test_is_admin_returns_true_for_admin_user(monkeypatch) -> None:
    from core import config as cfg_module
    monkeypatch.setattr(cfg_module.config, "admin_ids", [42])

    msg = make_message(from_user_id=42)
    assert await IsAdmin()(msg) is True


async def test_is_admin_returns_false_for_non_admin(monkeypatch) -> None:
    from core import config as cfg_module
    monkeypatch.setattr(cfg_module.config, "admin_ids", [42])

    msg = make_message(from_user_id=99)
    assert await IsAdmin()(msg) is False


async def test_is_admin_works_with_callback(monkeypatch) -> None:
    from core import config as cfg_module
    monkeypatch.setattr(cfg_module.config, "admin_ids", [7])

    cb = make_callback(from_user_id=7)
    assert await IsAdmin()(cb) is True


async def test_is_admin_returns_false_when_no_from_user(monkeypatch) -> None:
    from core import config as cfg_module
    monkeypatch.setattr(cfg_module.config, "admin_ids", [1])

    from types import SimpleNamespace
    event = SimpleNamespace(from_user=None)
    assert await IsAdmin()(event) is False


async def test_is_admin_empty_admin_ids(monkeypatch) -> None:
    from core import config as cfg_module
    monkeypatch.setattr(cfg_module.config, "admin_ids", [])

    msg = make_message(from_user_id=1)
    assert await IsAdmin()(msg) is False
