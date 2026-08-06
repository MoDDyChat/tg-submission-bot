"""Health/metrics HTTP-сервер и watchdog Telegram API.

Рядом с ботом поднимается HTTP-сервер с двумя эндпоинтами —
``GET /api/v1/health`` (JSON, для docker healthcheck) и
``GET /api/v1/metrics`` (Prometheus text format, для vmagent).

Проверяются: доступность Telegram API (фоновый watchdog с кэшем
состояния, чтобы скрейпы не били по API), PostgreSQL (``SELECT 1``),
Redis (``PING``, только если настроен ``REDIS_URL``), APScheduler.

Семантика кодов ответа /health: 503 — недоступен Telegram API или БД
(бот фактически мёртв); деградация Redis/планировщика видна в теле
(``status: degraded``), но отдаётся 200 — рестарт её не лечит.
"""

import asyncio
import time
from typing import Any

from aiogram import Bot
from aiohttp import web
from sqlalchemy import text

from core.config import config
from core.logging import get_logger

logger = get_logger(__name__)


class TelegramWatchdog:
    """Фоновый пробник Telegram API: периодический ``get_me`` с таймаутом.

    Health-эндпоинты читают кэшированное состояние, а не дёргают API
    на каждый скрейп.
    """

    def __init__(
        self,
        bot: Bot,
        check_interval: float = 60.0,
        timeout: float = 10.0,
    ) -> None:
        self._bot = bot
        self._check_interval = check_interval
        self._timeout = timeout
        self._task: asyncio.Task[None] | None = None
        self.healthy: bool = True
        self.consecutive_failures: int = 0
        self.last_ok_monotonic: float | None = None

    @property
    def seconds_since_last_ok(self) -> float | None:
        if self.last_ok_monotonic is None:
            return None
        return time.monotonic() - self.last_ok_monotonic

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="telegram_watchdog")
            logger.info("Telegram watchdog запущен (интервал %.0fс)", self._check_interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            await self.check_once()
            await asyncio.sleep(self._check_interval)

    async def check_once(self) -> bool:
        try:
            await asyncio.wait_for(self._bot.get_me(), timeout=self._timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.consecutive_failures += 1
            self.healthy = False
            logger.warning(
                "Telegram watchdog: get_me не прошёл (%d подряд): %s",
                self.consecutive_failures, exc,
            )
            return False
        if not self.healthy:
            logger.info(
                "Telegram watchdog: связь с API восстановлена после %d сбоев",
                self.consecutive_failures,
            )
        self.healthy = True
        self.consecutive_failures = 0
        self.last_ok_monotonic = time.monotonic()
        return True


async def _check_db(timeout: float = 5.0) -> bool:
    from db.session import engine

    try:
        async with engine.connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=timeout)
        return True
    except Exception:
        return False


async def _check_redis(timeout: float = 2.0) -> bool | None:
    """``None`` — Redis не настроен и не проверяется."""
    if not config.redis_url:
        return None
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(config.redis_url)
        try:
            await asyncio.wait_for(client.ping(), timeout=timeout)
        finally:
            await client.aclose()
        return True
    except Exception:
        return False


def _scheduler_running() -> bool:
    import services.scheduler as sched_mod

    return sched_mod.scheduler is not None and sched_mod.scheduler.running


async def collect_health(watchdog: TelegramWatchdog) -> dict[str, Any]:
    db_ok = await _check_db()
    redis_ok = await _check_redis()
    return {
        "telegram": watchdog.healthy,
        "telegram_failures": watchdog.consecutive_failures,
        "telegram_last_ok_seconds": watchdog.seconds_since_last_ok,
        "db": db_ok,
        "redis": redis_ok,
        "scheduler": _scheduler_running(),
    }


async def _handle_health(request: web.Request) -> web.Response:
    watchdog: TelegramWatchdog = request.app["watchdog"]
    checks = await collect_health(watchdog)

    if not checks["telegram"] or not checks["db"]:
        status, http_status = "dead", 503
    elif checks["redis"] is False or not checks["scheduler"]:
        status, http_status = "degraded", 200
    else:
        status, http_status = "ok", 200

    return web.json_response({"status": status, "checks": checks}, status=http_status)


async def _handle_metrics(request: web.Request) -> web.Response:
    watchdog: TelegramWatchdog = request.app["watchdog"]
    checks = await collect_health(watchdog)

    lines = [
        f"tgarts_telegram_up {int(checks['telegram'])}",
        f"tgarts_telegram_failures {checks['telegram_failures']}",
        f"tgarts_db_up {int(checks['db'])}",
        f"tgarts_scheduler_running {int(checks['scheduler'])}",
    ]
    if checks["redis"] is not None:
        lines.append(f"tgarts_redis_up {int(checks['redis'])}")
    if checks["telegram_last_ok_seconds"] is not None:
        lines.append(
            f"tgarts_telegram_last_ok_seconds {checks['telegram_last_ok_seconds']:.0f}"
        )
    return web.Response(text="\n".join(lines) + "\n", content_type="text/plain")


def create_health_app(watchdog: TelegramWatchdog) -> web.Application:
    app = web.Application()
    app["watchdog"] = watchdog
    app.router.add_get("/api/v1/health", _handle_health)
    app.router.add_get("/api/v1/metrics", _handle_metrics)
    return app


async def start_health_server(watchdog: TelegramWatchdog) -> web.AppRunner:
    """Поднять health-сервер; вернуть runner для cleanup() при остановке."""
    runner = web.AppRunner(create_health_app(watchdog), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.api_host, config.api_port)
    await site.start()
    logger.info("Health-сервер запущен на %s:%d", config.api_host, config.api_port)
    return runner
