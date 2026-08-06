from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from middlewares import rate_limit
from middlewares.db import DbSessionMiddleware
from middlewares.rate_limit import ThrottleMiddleware
from tests.helpers import FakeSessionFactory, make_callback


async def test_throttle_allows_request_under_limit(monkeypatch) -> None:
    middleware = ThrottleMiddleware(rate=2, period=10.0)
    handler = AsyncMock(return_value="ok")
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: 100.0)

    data = {"event_from_user": SimpleNamespace(id=11)}
    result = await middleware(handler, object(), data)

    assert result == "ok"
    handler.assert_awaited_once()


async def test_throttle_blocks_callback_and_shows_alert(monkeypatch) -> None:
    class FakeCallbackQuery:
        def __init__(self) -> None:
            self.answer = AsyncMock()

    monkeypatch.setattr(rate_limit, "CallbackQuery", FakeCallbackQuery)
    ticks = iter([10.0, 10.1, 10.2])
    monkeypatch.setattr(
        rate_limit,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks)),
    )

    middleware = ThrottleMiddleware(rate=2, period=10.0)
    handler = AsyncMock(return_value="ok")
    event = FakeCallbackQuery()
    data = {"event_from_user": SimpleNamespace(id=22)}

    await middleware(handler, event, data)
    await middleware(handler, event, data)
    result = await middleware(handler, event, data)

    assert result is None
    assert handler.await_count == 2
    event.answer.assert_awaited_once_with(
        "Слишком много запросов. Подождите немного.",
        show_alert=True,
    )


async def test_throttle_prunes_old_timestamps(monkeypatch) -> None:
    ticks = iter([0.0, 11.0])
    monkeypatch.setattr(
        rate_limit,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks)),
    )

    middleware = ThrottleMiddleware(rate=1, period=10.0)
    handler = AsyncMock(return_value="ok")
    data = {"event_from_user": SimpleNamespace(id=33)}

    assert await middleware(handler, object(), data) == "ok"
    assert await middleware(handler, object(), data) == "ok"
    assert handler.await_count == 2


async def test_throttle_skips_limit_when_user_is_missing() -> None:
    middleware = ThrottleMiddleware(rate=1, period=10.0)
    handler = AsyncMock(return_value="ok")

    result = await middleware(handler, object(), {})

    assert result == "ok"
    handler.assert_awaited_once()


def test_throttle_prune_stale_entries_removes_inactive_users(monkeypatch) -> None:
    # "Now" is 25.0; period=10.0 → cutoff=15.0; entries at 0.0/0.5 are stale
    monkeypatch.setattr(rate_limit, "time", SimpleNamespace(monotonic=lambda: 25.0))

    middleware = ThrottleMiddleware(rate=5, period=10.0)
    middleware._timestamps[10] = [0.0]
    middleware._timestamps[11] = [0.5]

    middleware.prune_stale_entries()

    assert 10 not in middleware._timestamps
    assert 11 not in middleware._timestamps


async def test_db_session_middleware_commits_and_injects_session() -> None:
    session = AsyncMock()
    session.in_transaction = MagicMock(return_value=True)
    middleware = DbSessionMiddleware(FakeSessionFactory(session))
    data = {}
    handler = AsyncMock(return_value="done")

    result = await middleware(handler, object(), data)

    assert result == "done"
    assert data["session"] is session
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


async def test_db_session_middleware_rolls_back_on_exception() -> None:
    session = AsyncMock()
    middleware = DbSessionMiddleware(FakeSessionFactory(session))

    async def failing_handler(event, data):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await middleware(failing_handler, make_callback(), {})

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


async def test_db_session_middleware_does_not_rollback_on_cancelled_error() -> None:
    """asyncio.CancelledError is BaseException, not Exception — must propagate without rollback."""
    session = AsyncMock()
    middleware = DbSessionMiddleware(FakeSessionFactory(session))

    async def cancel_handler(event, data):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await middleware(cancel_handler, make_callback(), {})

    session.rollback.assert_not_awaited()


# ── AuthMiddleware — is_admin injection ───────────────────────────

async def test_auth_middleware_sets_is_admin_true_for_admin(monkeypatch) -> None:
    from middlewares.auth import AuthMiddleware
    from tests.helpers import make_user
    from core import config as cfg_module

    monkeypatch.setattr(cfg_module.config, "admin_ids", [42])

    db_user = make_user(telegram_id=42)
    db_user.is_admin = True

    session = AsyncMock()
    data: dict = {
        "session": session,
        "event_from_user": SimpleNamespace(id=42, username="admin", full_name="Admin User", first_name="Admin"),
    }
    handler = AsyncMock(return_value="ok")

    with patch("middlewares.auth.get_or_create_user", AsyncMock(return_value=(db_user, False))):
        await AuthMiddleware()(handler, object(), data)

    assert data["is_admin"] is True


async def test_auth_middleware_sets_is_admin_false_for_non_admin(monkeypatch) -> None:
    from middlewares.auth import AuthMiddleware
    from tests.helpers import make_user
    from core import config as cfg_module

    monkeypatch.setattr(cfg_module.config, "admin_ids", [99])

    db_user = make_user(telegram_id=1)
    db_user.is_admin = False

    session = AsyncMock()
    data: dict = {
        "session": session,
        "event_from_user": SimpleNamespace(id=1, username="user", full_name="Regular User", first_name="User"),
    }
    handler = AsyncMock(return_value="ok")

    with patch("middlewares.auth.get_or_create_user", AsyncMock(return_value=(db_user, False))):
        await AuthMiddleware()(handler, object(), data)

    assert data["is_admin"] is False



async def test_auth_middleware_does_not_pass_is_admin_to_get_or_create_user(monkeypatch) -> None:
    """is_admin must NOT be passed to get_or_create_user - managed by sync_admin_flags only."""
    import middlewares.auth as auth_mod
    from middlewares.auth import AuthMiddleware
    from tests.helpers import make_user

    db_user = make_user(telegram_id=5)
    db_user.is_admin = True

    monkeypatch.setattr(auth_mod, "_user_cache", {})
    session = AsyncMock()
    data: dict = {
        "session": session,
        "event_from_user": SimpleNamespace(id=5, username="adm", full_name="Admin", first_name="Admin"),
    }
    handler = AsyncMock(return_value="ok")

    mock_get_or_create = AsyncMock(return_value=(db_user, False))
    with patch("middlewares.auth.get_or_create_user", mock_get_or_create):
        await AuthMiddleware()(handler, object(), data)

    call_kwargs = mock_get_or_create.call_args.kwargs
    assert "is_admin" not in call_kwargs


async def test_auth_middleware_cache_hit_skips_upsert(monkeypatch) -> None:
    """On cache hit, get_or_create_user is NOT called; session.get() is used instead."""
    import middlewares.auth as auth_mod
    from middlewares.auth import AuthMiddleware, _CachedUserInfo
    from tests.helpers import make_user

    db_user = make_user(telegram_id=7)
    db_user.is_admin = False

    fake_cache = {7: _CachedUserInfo(user_id=db_user.id, username="user7", full_name="User Seven")}
    monkeypatch.setattr(auth_mod, "_user_cache", fake_cache)

    session = AsyncMock()
    session.get = AsyncMock(return_value=db_user)
    data: dict = {
        "session": session,
        "event_from_user": SimpleNamespace(
            id=7, username="user7", full_name="User Seven", first_name="User"
        ),
    }
    handler = AsyncMock(return_value="ok")

    mock_get_or_create = AsyncMock()
    with patch("middlewares.auth.get_or_create_user", mock_get_or_create):
        result = await AuthMiddleware()(handler, object(), data)

    assert result == "ok"
    mock_get_or_create.assert_not_awaited()
    session.get.assert_awaited_once()
    assert data["db_user"] is db_user


async def test_auth_middleware_cache_hit_updates_username_if_changed(monkeypatch) -> None:
    """On cache hit with changed username, issues an UPDATE and refreshes cache entry."""
    import middlewares.auth as auth_mod
    from middlewares.auth import AuthMiddleware, _CachedUserInfo
    from tests.helpers import make_user

    db_user = make_user(telegram_id=8)
    db_user.username = "old_name"
    db_user.full_name = "Old Name"

    fake_cache = {8: _CachedUserInfo(user_id=db_user.id, username="old_name", full_name="Old Name")}
    monkeypatch.setattr(auth_mod, "_user_cache", fake_cache)

    session = AsyncMock()
    session.get = AsyncMock(return_value=db_user)
    data: dict = {
        "session": session,
        "event_from_user": SimpleNamespace(
            id=8, username="new_name", full_name="New Name", first_name="New"
        ),
    }
    handler = AsyncMock(return_value="ok")

    with patch("middlewares.auth.get_or_create_user", AsyncMock()):
        await AuthMiddleware()(handler, object(), data)

    session.execute.assert_awaited_once()
    assert fake_cache[8].username == "new_name"
    assert fake_cache[8].full_name == "New Name"


async def test_auth_middleware_is_admin_not_overwritten_by_middleware(monkeypatch) -> None:
    """is_admin is read from DB object, never set from config during request handling."""
    import middlewares.auth as auth_mod
    from middlewares.auth import AuthMiddleware, _CachedUserInfo
    from tests.helpers import make_user
    from core import config as cfg_module

    monkeypatch.setattr(cfg_module.config, "admin_ids", [9])
    db_user = make_user(telegram_id=9)
    db_user.is_admin = False

    fake_cache = {9: _CachedUserInfo(user_id=db_user.id, username="u9", full_name="U9")}
    monkeypatch.setattr(auth_mod, "_user_cache", fake_cache)

    session = AsyncMock()
    session.get = AsyncMock(return_value=db_user)
    data: dict = {
        "session": session,
        "event_from_user": SimpleNamespace(id=9, username="u9", full_name="U9", first_name="U"),
    }
    handler = AsyncMock(return_value="ok")

    await AuthMiddleware()(handler, object(), data)

    assert data["is_admin"] is False
