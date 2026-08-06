"""Tests for services.health: watchdog state and HTTP endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from services.health import TelegramWatchdog, collect_health, create_health_app


def _make_watchdog(healthy: bool = True, failures: int = 0) -> TelegramWatchdog:
    watchdog = TelegramWatchdog(bot=AsyncMock())
    watchdog.healthy = healthy
    watchdog.consecutive_failures = failures
    return watchdog


async def test_watchdog_check_once_success() -> None:
    bot = AsyncMock()
    watchdog = TelegramWatchdog(bot)
    watchdog.healthy = False
    watchdog.consecutive_failures = 2

    assert await watchdog.check_once() is True
    assert watchdog.healthy is True
    assert watchdog.consecutive_failures == 0
    assert watchdog.seconds_since_last_ok is not None
    bot.get_me.assert_awaited_once()


async def test_watchdog_check_once_failure() -> None:
    bot = AsyncMock()
    bot.get_me.side_effect = RuntimeError("network down")
    watchdog = TelegramWatchdog(bot)

    assert await watchdog.check_once() is False
    assert watchdog.healthy is False
    assert watchdog.consecutive_failures == 1

    assert await watchdog.check_once() is False
    assert watchdog.consecutive_failures == 2


@pytest.fixture
def _healthy_checks():
    with (
        patch("services.health._check_db", new=AsyncMock(return_value=True)),
        patch("services.health._check_redis", new=AsyncMock(return_value=True)),
        patch("services.health._scheduler_running", return_value=True),
    ):
        yield


async def test_collect_health_ok(_healthy_checks) -> None:
    checks = await collect_health(_make_watchdog())
    assert checks["telegram"] is True
    assert checks["db"] is True
    assert checks["redis"] is True
    assert checks["scheduler"] is True


async def _get(watchdog: TelegramWatchdog, path: str):
    app = create_health_app(watchdog)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(path)
        return resp.status, await resp.text()


async def test_health_endpoint_ok(_healthy_checks) -> None:
    status, body = await _get(_make_watchdog(), "/api/v1/health")
    assert status == 200
    assert '"status": "ok"' in body


async def test_health_endpoint_telegram_dead(_healthy_checks) -> None:
    status, body = await _get(_make_watchdog(healthy=False, failures=3), "/api/v1/health")
    assert status == 503
    assert '"status": "dead"' in body


async def test_health_endpoint_db_dead() -> None:
    with (
        patch("services.health._check_db", new=AsyncMock(return_value=False)),
        patch("services.health._check_redis", new=AsyncMock(return_value=True)),
        patch("services.health._scheduler_running", return_value=True),
    ):
        status, _ = await _get(_make_watchdog(), "/api/v1/health")
    assert status == 503


async def test_health_endpoint_degraded_on_scheduler() -> None:
    with (
        patch("services.health._check_db", new=AsyncMock(return_value=True)),
        patch("services.health._check_redis", new=AsyncMock(return_value=None)),
        patch("services.health._scheduler_running", return_value=False),
    ):
        status, body = await _get(_make_watchdog(), "/api/v1/health")
    assert status == 200
    assert '"status": "degraded"' in body


async def test_metrics_endpoint(_healthy_checks) -> None:
    status, body = await _get(_make_watchdog(), "/api/v1/metrics")
    assert status == 200
    assert "tgarts_telegram_up 1" in body
    assert "tgarts_db_up 1" in body
    assert "tgarts_redis_up 1" in body
    assert "tgarts_scheduler_running 1" in body


async def test_metrics_endpoint_no_redis() -> None:
    with (
        patch("services.health._check_db", new=AsyncMock(return_value=True)),
        patch("services.health._check_redis", new=AsyncMock(return_value=None)),
        patch("services.health._scheduler_running", return_value=True),
    ):
        _, body = await _get(_make_watchdog(), "/api/v1/metrics")
    assert "tgarts_redis_up" not in body
