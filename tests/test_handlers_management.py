"""Unit tests for handlers/moderator/management.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

import core.messages as msg
from handlers.moderator import management
from tests.helpers import FakeState, make_callback, make_user


# ── handle_presets_menu — lock acquisition ────────────────────────────

async def test_presets_menu_acquires_lock_and_renders(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    db_user.is_admin = False
    callback = make_callback(from_user_id=202)
    render = AsyncMock()

    monkeypatch.setattr(management.edit_lock, "acquire_lock", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(management, "_render_preset_sections", render)

    await management.handle_presets_menu(callback, session, state, db_user)

    render.assert_awaited_once()
    callback.answer.assert_awaited()


async def test_presets_menu_shows_alert_when_locked(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    callback = make_callback()

    owner = make_user(user_id=3, telegram_id=303, username="other_mod")
    monkeypatch.setattr(management.edit_lock, "acquire_lock", AsyncMock(return_value=(False, 303)))
    monkeypatch.setattr(management, "get_user_by_telegram_id", AsyncMock(return_value=owner))

    await management.handle_presets_menu(callback, session, state, db_user)

    callback.answer.assert_awaited_once()
    call = callback.answer.await_args
    assert call.kwargs.get("show_alert") is True


# ── handle_banned_users — lock acquisition ────────────────────────────

async def test_banned_users_acquires_lock_and_renders(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    callback = make_callback()
    render = AsyncMock()

    monkeypatch.setattr(management.edit_lock, "acquire_lock", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(management, "_render_banned_users", render)

    await management.handle_banned_users(callback, session, state, db_user)

    render.assert_awaited_once()
    callback.answer.assert_awaited()


# ── handle_recover — is_admin guard ──────────────────────────────────

async def test_handle_recover_rejected_for_non_admin(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    db_user.is_admin = False
    callback = make_callback()

    await management.handle_recover(callback, session, state, db_user)

    callback.answer.assert_awaited_once_with(msg.RECOVER_ADMIN_ONLY, show_alert=True)


async def test_handle_recover_runs_for_admin(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    db_user.is_admin = True
    callback = make_callback()

    monkeypatch.setattr(management, "get_active_submissions", AsyncMock(return_value=[]))
    monkeypatch.setattr(management, "recover_missing_posts", AsyncMock(return_value=(0, 0)))
    monkeypatch.setattr(management, "_render_management_message", AsyncMock())

    await management.handle_recover(callback, session, state, db_user)

    management.recover_missing_posts.assert_awaited_once()


# ── handle_home — releases management locks ──────────────────────────

async def test_handle_home_releases_management_locks(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    callback = make_callback()
    release = AsyncMock()

    monkeypatch.setattr(management.edit_lock, "release_lock", release)
    monkeypatch.setattr(management, "_render_home", AsyncMock())

    await management.handle_home(callback, session, state, db_user)

    assert release.await_count == 2  # presets + banned
    callback.answer.assert_awaited()
