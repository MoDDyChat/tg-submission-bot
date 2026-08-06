"""Entry point: creates Bot, Dispatcher, registers routers, runs polling."""

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from core.config import config
from core.logging import create_logger, get_logger
from db.session import check_db_connection, run_migrations, session_factory, shutdown_db
from handlers import common, contact, errors, moderator, service_messages, viewer
from middlewares.auth import AuthMiddleware
from middlewares.db import DbSessionMiddleware
from middlewares.rate_limit import ThrottleMiddleware
from middlewares.silent_chats import SilentChatsMiddleware
from services.health import TelegramWatchdog, start_health_server
from services.scheduler import (
    cleanup_edit_locks_job,
    create_scheduler,
    prune_throttle_job,
    queue_render_job,
    recover_scheduled_jobs,
    shutdown_scheduler,
    start_scheduler,
    topic_cards_reconcile_job,
    topic_title_sync_job,
    topic_titles_reconcile_job,
)
from services.topics import ensure_general_topic_nav
from services.topics_queue import render_queue as _render_queue, render_schedule as _render_schedule

logger = get_logger(__name__)


async def sync_admin_flags() -> None:
    """Delta-sync ``users.is_admin`` from ``config.admin_ids`` at startup.

    Fetches the current set of admin users from DB, then issues targeted
    UPDATE statements only for the changed rows.
    """
    from sqlalchemy import select, update as sa_update

    from db.models import User
    from db.session import session_factory

    async with session_factory() as session:
        result = await session.execute(
            select(User.telegram_id).where(User.is_admin.is_(True))
        )
        current_admin_ids: set[int] = {row[0] for row in result.all()}

        desired: set[int] = set(config.admin_ids)
        to_add = desired - current_admin_ids
        to_remove = current_admin_ids - desired

        if to_add:
            await session.execute(
                sa_update(User)
                .where(User.telegram_id.in_(to_add))
                .values(is_admin=True)
            )
        if to_remove:
            await session.execute(
                sa_update(User)
                .where(User.telegram_id.in_(to_remove))
                .values(is_admin=False)
            )
        if to_add or to_remove:
            await session.commit()

    logger.info(
        "sync_admin_flags: добавлено %d, снято %d is_admin флагов",
        len(to_add), len(to_remove),
    )


def _create_storage() -> MemoryStorage:
    """Return RedisStorage if REDIS_URL is configured, else MemoryStorage."""
    if config.redis_url:
        try:
            from aiogram.fsm.storage.redis import RedisStorage
            storage = RedisStorage.from_url(config.redis_url)
            logger.info("FSM storage: Redis (%s)", config.redis_url)
            return storage  # type: ignore[return-value]
        except Exception:
            logger.exception(
                "Не удалось подключиться к Redis FSM storage (%s), "
                "используем MemoryStorage",
                config.redis_url,
            )
    logger.info("FSM storage: Memory (FSM state не переживёт рестарт)")
    return MemoryStorage()


def _create_dispatcher(throttle: ThrottleMiddleware) -> Dispatcher:
    """Build and configure the Dispatcher with middlewares and routers."""
    dp = Dispatcher(storage=_create_storage())

    # Middlewares (order matters: throttle first, then db, then auth)
    dp.update.outer_middleware(throttle)
    dp.update.outer_middleware(DbSessionMiddleware(session_factory))
    dp.update.outer_middleware(AuthMiddleware())

    # Routers: service_messages first (suppresses forum noise),
    # then moderator so its /start deep link takes priority
    dp.include_routers(
        service_messages.router,
        moderator.router,
        common.router,
        contact.router,
        viewer.router,
    )

    # Global catch-all for exceptions uncaught by individual handlers
    # (e.g. TelegramNetworkError on internet loss) — logs and gives feedback.
    dp.errors.register(errors.handle_global_error)

    return dp


async def main() -> None:
    create_logger(config.log_level)

    # Initialise topic status config from file (removes import-time side effect)
    from core.topic_status_config import _init as _init_topic_status_config
    _init_topic_status_config(config.topic_statuses_path)

    # Initialise per-instance viewer rules text from file
    from core.rules_config import _init as _init_rules
    _init_rules(config.rules_path)

    await check_db_connection()
    await run_migrations()

    bot_session: AiohttpSession | None = None
    if config.proxy_url:
        bot_session = AiohttpSession(proxy=config.proxy_url)
        logger.info("Прокси для Telegram API: %s", config.proxy_url)

    bot = Bot(
        token=config.bot.token,
        session=bot_session or AiohttpSession(),
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    if config.silent_moderator_notifications:
        bot.session.middleware(SilentChatsMiddleware({config.moderator_group_id}))
        logger.info(
            "Тихий режим уведомлений включён для группы модераторов (id=%d)",
            config.moderator_group_id,
        )
    throttle = ThrottleMiddleware()
    dp = _create_dispatcher(throttle)

    me = await bot.get_me()
    logger.info("Имя бота: @%s", me.username)

    await bot.set_my_commands([
        BotCommand(command="start", description="Правила предложки"),
        BotCommand(command="help", description="Справка по командам"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ])
    logger.info("Команды бота зарегистрированы")

    # Validate channel access at startup
    for label, chat_id in [
        ("основному каналу", config.channel_id),
        ("группе модератора", config.moderator_group_id),
    ]:
        try:
            chat = await bot.get_chat(chat_id)
            logger.info("Доступ к %s OK: %s", label, chat.title or chat_id)
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logger.warning(
                "Нет доступа к %s (id=%d): %s. Убедитесь, что бот добавлен как администратор.",
                label, chat_id, e,
            )
        except Exception:
            logger.warning(
                "Не удалось проверить доступ к %s (id=%d) при старте "
                "(вероятно, сетевая ошибка). Проверка не критична — пропускаю.",
                label, chat_id, exc_info=True,
            )

    async with session_factory() as legend_session:
        await ensure_general_topic_nav(bot, legend_session)
        await _render_schedule(bot, legend_session, force_reconcile=True)
        await _render_queue(bot, legend_session, force_reconcile=True)
        await legend_session.commit()

    await sync_admin_flags()

    import services.scheduler as _sched_mod
    _sched_mod.scheduler = create_scheduler()
    start_scheduler()
    _sched_mod.scheduler.add_job(
        cleanup_edit_locks_job,
        "interval",
        minutes=5,
        id="cleanup_edit_locks",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        args=[bot, session_factory],
    )
    _sched_mod.scheduler.add_job(
        queue_render_job,
        "interval",
        minutes=5,
        id="queue_render",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        args=[bot, session_factory],
    )
    _sched_mod.scheduler.add_job(
        topic_titles_reconcile_job,
        "interval",
        minutes=10,
        id="topic_titles_reconcile",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        args=[session_factory],
    )
    _sched_mod.scheduler.add_job(
        topic_cards_reconcile_job,
        "interval",
        minutes=10,
        id="topic_cards_reconcile",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        args=[bot, session_factory],
        max_instances=1,
    )
    _sched_mod.scheduler.add_job(
        topic_title_sync_job,
        "interval",
        seconds=10,
        id="topic_title_sync",
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,
        args=[bot, session_factory],
        max_instances=1,
    )
    _sched_mod.scheduler.add_job(
        prune_throttle_job,
        "interval",
        hours=1,
        id="prune_throttle",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        args=[throttle],
    )
    await recover_scheduled_jobs(bot, session_factory)

    # Health-мониторинг: watchdog Telegram API + HTTP /health и /metrics
    watchdog = TelegramWatchdog(bot)
    watchdog.start()
    health_runner = await start_health_server(watchdog)

    logger.info("Бот запускается...")

    try:
        await dp.start_polling(bot)
    except asyncio.CancelledError:
        logger.info("Поллинг отменён")
    except Exception:
        logger.exception("Неожиданная ошибка при поллинге")
    finally:
        # Wait for in-flight media group finalizations (up to 30 s)
        from services.submission_intake import wait_for_pending_groups
        await wait_for_pending_groups(timeout=30.0)

        try:
            await watchdog.stop()
            await health_runner.cleanup()
        except Exception:
            logger.exception("Ошибка при остановке health-сервера")
        try:
            shutdown_scheduler()
        except Exception:
            logger.exception("Ошибка при остановке планировщика")
        try:
            await shutdown_db()
        except Exception:
            logger.exception("Ошибка при закрытии DB")
        try:
            await bot.session.close()
        except Exception:
            logger.exception("Ошибка при закрытии bot session")
        logger.info("Бот остановлен.")
