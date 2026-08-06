"""Unit tests for DbSessionMiddleware (middlewares/db.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from middlewares.db import DbSessionMiddleware


# ── commit on success ─────────────────────────────────────────────────

async def test_db_middleware_commits_on_success() -> None:
    session = AsyncMock()
    session.in_transaction = MagicMock(return_value=True)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    middleware = DbSessionMiddleware(factory)
    handler = AsyncMock(return_value="ok")

    result = await middleware(handler, object(), {})

    assert result == "ok"
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


async def test_db_middleware_skips_commit_when_not_in_transaction() -> None:
    session = AsyncMock()
    session.in_transaction = MagicMock(return_value=False)  # sync method
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    middleware = DbSessionMiddleware(factory)
    handler = AsyncMock(return_value="ok")

    await middleware(handler, object(), {})

    session.commit.assert_not_awaited()


# ── rollback on exception ─────────────────────────────────────────────

async def test_db_middleware_rolls_back_on_exception() -> None:
    session = AsyncMock()
    session.in_transaction = MagicMock(return_value=True)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    middleware = DbSessionMiddleware(factory)
    handler = AsyncMock(side_effect=ValueError("handler error"))

    import pytest
    with pytest.raises(ValueError, match="handler error"):
        await middleware(handler, object(), {})

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


# ── session injection ──────────────────────────────────────────────────

async def test_db_middleware_injects_session_into_data() -> None:
    session = AsyncMock()
    session.in_transaction.return_value = True
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    middleware = DbSessionMiddleware(factory)
    captured_data: dict = {}

    async def capture_handler(event, data):
        captured_data.update(data)
        return None

    await middleware(capture_handler, object(), {})

    assert captured_data["session"] is session
