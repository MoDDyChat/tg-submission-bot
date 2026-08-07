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
from handlers import common, contact, errors, moderator, moderator_invite, service_messages, viewer
from middlewares.auth import AuthMiddleware
from middlewares.db import DbSessionMiddleware
from middlewares.rate_limit import ThrottleMiddleware
from middlewares.silent_chats import SilentChatsMiddleware
from services.author_card import author_card_render_job, author_card_reconcile_job
from services.dashboard import dashboard_render_job, render_dashboard
from services.health import TelegramWatchdog, start_health_server
from services.scheduler import (
    cleanup_edit_locks_job,
    cleanup_moderator_invites_job,
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
from services.topics import cleanup_legacy_legend_pin
from services.topics_queue import render_queue as _render_queue, render_schedule as _render_schedule

logger = get_logger(__name__)


async def bootstrap_roles() -> None:
    """Apply ``MODERATOR_IDS``/``ADMIN_IDS`` from config at startup, additively.

    Upserts a ``users`` row for every configured telegram_id with
    ``is_moderator=True`` (admins also get ``is_admin=True``), creating rows
    with a placeholder full_name for people who have never written to the bot.
    Never removes flags: the DB is the source of truth at runtime, config is
    the break-glass bootstrap list.
    """
    from sqlalchemy import func, literal_column
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from core.messages import ROLE_BOOTSTRAP_PLACEHOLDER_NAME
    from db.models import User
    from db.session import session_factory

    async with session_factory() as session:
        created = 0
        flags_set = 0

        for telegram_id in config.moderator_ids:
            stmt = (
                pg_insert(User)
                .values(
                    telegram_id=telegram_id,
                    username=None,
                    full_name=ROLE_BOOTSTRAP_PLACEHOLDER_NAME,
                    is_moderator=True,
                )
                .on_conflict_do_update(
                    index_elements=[User.telegram_id],
                    set_=dict(is_moderator=True, updated_at=func.now()),
                )
                .returning(literal_column("(xmax::text::bigint = 0)").label("is_new"))
            )
            result = await session.execute(stmt)
            if result.scalar_one():
                created += 1
            flags_set += 1

        # admin_ids are already merged into moderator_ids by the config model
        # validator, so is_moderator is handled above; here we only ensure the
        # admin flag itself.
        for telegram_id in config.admin_ids:
            stmt = (
                pg_insert(User)
                .values(
                    telegram_id=telegram_id,
                    username=None,
                    full_name=ROLE_BOOTSTRAP_PLACEHOLDER_NAME,
                    is_moderator=True,
                    is_admin=True,
                )
                .on_conflict_do_update(
                    index_elements=[User.telegram_id],
                    set_=dict(is_admin=True, updated_at=func.now()),
                )
                .returning(literal_column("(xmax::text::bigint = 0)").label("is_new"))
            )
            result = await session.execute(stmt)
            if result.scalar_one():
                created += 1
            flags_set += 1

        await session.commit()

    logger.info(
        "bootstrap_roles: создано строк %d, выставлено флагов %d",
        created, flags_set,
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
    # then the moderator invite deep link (its invitee has no role yet and the
    # moderator router is fully IsModerator-filtered), then moderator so its
    # /start deep link takes priority over common.cmd_start
    dp.include_routers(
        service_messages.router,
        moderator_invite.router,
        moderator.router,
        common.router,
        contact.router,
        viewer.router,
    )

    # Global catch-all for exceptions uncaught by individual handlers
    # (e.g. TelegramNetworkError on internet loss) — logs and gives feedback.
    dp.errors.register(errors.handle_global_error)

    return dp


def _register_scheduled_jobs(bot: Bot, throttle: ThrottleMiddleware) -> None:
    """Register all periodic APScheduler jobs. Requires ``services.scheduler.scheduler``
    to already hold a live scheduler instance (set by ``main()`` before calling this)."""
    import services.scheduler as _sched_mod

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
    _sched_mod.scheduler.add_job(
        cleanup_moderator_invites_job,
        "interval",
        hours=24,
        id="cleanup_moderator_invites",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        args=[session_factory],
    )
    _sched_mod.scheduler.add_job(
        author_card_render_job,
        "interval",
        seconds=60,
        id="author_card_render",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        args=[bot, session_factory],
        max_instances=1,
    )
    _sched_mod.scheduler.add_job(
        author_card_reconcile_job,
        "interval",
        minutes=10,
        id="author_card_reconcile",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        args=[bot, session_factory],
        max_instances=1,
    )
    _sched_mod.scheduler.add_job(
        dashboard_render_job,
        "interval",
        seconds=60,
        id="dashboard_render",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        args=[bot, session_factory],
        max_instances=1,
    )


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
        await cleanup_legacy_legend_pin(bot, legend_session)
        await _render_schedule(bot, legend_session, force_reconcile=True)
        await _render_queue(bot, legend_session, force_reconcile=True)
        await render_dashboard(bot, legend_session, force=True)
        await legend_session.commit()

    await bootstrap_roles()

    import services.scheduler as _sched_mod
    _sched_mod.scheduler = create_scheduler()
    start_scheduler()
    _register_scheduled_jobs(bot, throttle)
    await recover_scheduled_jobs(bot, session_factory)

    async with session_factory() as post_recover_session:
        await _render_schedule(bot, post_recover_session, force_reconcile=True)
        await render_dashboard(bot, post_recover_session, force=True)
        await post_recover_session.commit()

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
            from handlers.moderator.management import cancel_recover_task
            await cancel_recover_task()
        except Exception:
            logger.exception("Ошибка при отмене фонового Recover")
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
