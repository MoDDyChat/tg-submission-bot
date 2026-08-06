"""Unit tests for AuthMiddleware (middlewares/auth.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import middlewares.auth as auth_module
from middlewares.auth import AuthMiddleware, _user_cache, _CachedUserInfo
from tests.helpers import make_user


def _event_user(uid: int = 42, username: str = "alice", full_name: str = "Alice B") -> SimpleNamespace:
    return SimpleNamespace(id=uid, username=username, full_name=full_name, first_name="Alice")


def _setup_data(event_user, session) -> dict:
    return {"event_from_user": event_user, "session": session}


# ── cache miss → upsert ────────────────────────────────────────────────

async def test_auth_middleware_upserts_on_cache_miss(monkeypatch) -> None:
    _user_cache.clear()
    eu = _event_user(uid=1001)
    session = AsyncMock()
    db_user = make_user(user_id=5, telegram_id=1001, username="alice")
    db_user.is_admin = False

    monkeypatch.setattr(auth_module, "get_or_create_user", AsyncMock(return_value=(db_user, True)))

    handler = AsyncMock(return_value="done")
    middleware = AuthMiddleware()
    data = _setup_data(eu, session)

    result = await middleware(handler, object(), data)

    assert result == "done"
    assert data["db_user"] is db_user
    assert data["is_admin"] is False
    # Cache populated
    assert 1001 in _user_cache
    _user_cache.clear()


# ── cache hit → no upsert ───────────────────────────────────────────────

async def test_auth_middleware_uses_cache_on_hit(monkeypatch) -> None:
    _user_cache.clear()
    db_user = make_user(user_id=7, telegram_id=2002, username="bob")
    db_user.is_admin = False
    _user_cache[2002] = _CachedUserInfo(user_id=7, username="bob", full_name="Bob")

    eu = _event_user(uid=2002, username="bob", full_name="Bob")
    session = AsyncMock()
    session.get = AsyncMock(return_value=db_user)

    upsert = AsyncMock()
    monkeypatch.setattr(auth_module, "get_or_create_user", upsert)

    handler = AsyncMock(return_value="cached")
    middleware = AuthMiddleware()
    data = _setup_data(eu, session)

    result = await middleware(handler, object(), data)

    assert result == "cached"
    upsert.assert_not_awaited()  # No DB round-trip
    _user_cache.clear()


# ── is_admin not overwritten by middleware ────────────────────────────────

async def test_auth_middleware_does_not_overwrite_is_admin(monkeypatch) -> None:
    """is_admin must only be set by sync_admin_flags, not re-derived here."""
    _user_cache.clear()
    eu = _event_user(uid=3003, username="mod")
    session = AsyncMock()
    db_user = make_user(user_id=9, telegram_id=3003, username="mod")
    db_user.is_admin = True  # Set by sync_admin_flags

    monkeypatch.setattr(auth_module, "get_or_create_user", AsyncMock(return_value=(db_user, False)))

    handler = AsyncMock(return_value="ok")
    middleware = AuthMiddleware()
    data = _setup_data(eu, session)

    await middleware(handler, object(), data)

    # Middleware should preserve whatever is_admin was set to in DB
    assert data["is_admin"] is True
    _user_cache.clear()


# ── no event_from_user → passthrough ────────────────────────────────────

async def test_auth_middleware_skips_when_no_event_user(monkeypatch) -> None:
    session = AsyncMock()
    upsert = AsyncMock()
    monkeypatch.setattr(auth_module, "get_or_create_user", upsert)

    handler = AsyncMock(return_value="pass")
    middleware = AuthMiddleware()
    data = {"session": session}  # No event_from_user

    result = await middleware(handler, object(), data)

    assert result == "pass"
    upsert.assert_not_awaited()


# ── cache eviction when user deleted ───────────────────────────────────

async def test_auth_middleware_evicts_cache_when_user_deleted(monkeypatch) -> None:
    _user_cache.clear()
    _user_cache[4004] = _CachedUserInfo(user_id=11, username="ghost", full_name="Ghost")

    eu = _event_user(uid=4004, username="ghost")
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)  # User deleted from DB

    fresh_user = make_user(user_id=12, telegram_id=4004, username="ghost")
    fresh_user.is_admin = False
    upsert = AsyncMock(return_value=(fresh_user, True))
    monkeypatch.setattr(auth_module, "get_or_create_user", upsert)

    handler = AsyncMock(return_value="ok")
    middleware = AuthMiddleware()
    data = _setup_data(eu, session)

    await middleware(handler, object(), data)

    # Full upsert path taken after eviction
    upsert.assert_awaited_once()
    _user_cache.clear()
