"""Database engine, session factory, and Base declarative class."""

import asyncio
import os

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from core.config import config
from core.logging import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    pass


# Safety net against a connection that hangs forever after a network blip: the
# client stops waiting (command_timeout), the server kills a query or an idle
# transaction that outlives its budget. Without these a single stuck coroutine
# holds row locks indefinitely and blocks every writer behind it.
_COMMAND_TIMEOUT = 30.0
_SERVER_SETTINGS = {
    "statement_timeout": "30000",
    "idle_in_transaction_session_timeout": "60000",
}

engine: AsyncEngine = create_async_engine(
    config.db.url,
    pool_size=10,
    max_overflow=5,
    pool_recycle=600,
    pool_pre_ping=True,
    connect_args={
        "command_timeout": _COMMAND_TIMEOUT,
        "server_settings": _SERVER_SETTINGS,
    },
)
session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False,
)


async def check_db_connection(timeout: float = 5.0) -> None:
    """Verify database connectivity at startup."""
    try:
        async with engine.connect() as conn:
            await asyncio.wait_for(
                conn.execute(text("SELECT 1")),
                timeout=timeout,
            )
        logger.info("Подключение к базе данных OK")
    except asyncio.TimeoutError:
        logger.critical("Превышено время ожидания подключения к БД")
        raise
    except Exception as exc:
        logger.critical("Ошибка подключения к БД: %s", exc)
        raise


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ALEMBIC_DIR = os.path.join(_PROJECT_ROOT, "alembic")

# Фиксированный ключ session-level advisory-лока: общий для всех инстансов бота,
# чтобы одновременный старт не привёл к параллельному прогону миграций.
_MIGRATION_LOCK_KEY = 728_413_002


def _make_alembic_config() -> AlembicConfig:
    """Build an in-memory Alembic config.

    Deliberately not loaded from ``alembic.ini``: ``env.py`` calls
    ``fileConfig(config_file_name)`` and would reinitialise app logging.
    """
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", _ALEMBIC_DIR)
    cfg.set_main_option("sqlalchemy.url", config.db.url)
    return cfg


def _head_revisions(cfg: AlembicConfig) -> set[str]:
    return set(ScriptDirectory.from_config(cfg).get_heads())


def _current_revisions(sync_connection) -> set[str]:
    return set(MigrationContext.configure(sync_connection).get_current_heads())


# Ревизии 0003/0004 изначально были созданы с автогенерированными hex-id и
# позже переименованы под общий формат NNNN_slug. В уже проинициализированных
# БД alembic_version хранит старый id, которого больше нет в versions/ — Alembic
# на старте упал бы с "Can't locate revision". Шим переписывает его на месте;
# после того как все инстансы обновятся, его можно удалить.
_RENAMED_REVISIONS = {
    "f736150e6fa0": "0003_moderator_invites",
    "b7c1d2e3f4a5": "0004_group_observability",
}


async def _rename_legacy_revisions(conn) -> None:
    """Rewrite pre-rename revision ids in ``alembic_version`` (idempotent)."""
    exists = await conn.scalar(text("SELECT to_regclass('alembic_version')"))
    if exists is None:
        return

    for old, new in _RENAMED_REVISIONS.items():
        result = await conn.execute(
            text("UPDATE alembic_version SET version_num = :new WHERE version_num = :old"),
            {"new": new, "old": old},
        )
        if result.rowcount:
            logger.warning("alembic_version: ревизия %s переименована в %s", old, new)
    await conn.commit()


def _upgrade(cfg: AlembicConfig) -> None:
    """Sync upgrade run in a worker thread: env.py does its own asyncio.run()."""
    command.upgrade(cfg, "head")


async def run_migrations() -> None:
    """Check the schema and (by default) apply pending Alembic migrations.

    Raises:
        RuntimeError: migrations failed, timed out, or the schema lags behind
            ``head`` while ``DB_AUTO_MIGRATE`` is disabled.
    """
    if not os.path.isdir(os.path.join(_ALEMBIC_DIR, "versions")):
        raise RuntimeError(f"Не найдена директория миграций {_ALEMBIC_DIR}/versions")

    cfg = _make_alembic_config()
    heads = _head_revisions(cfg)

    # Migrations run on their own engine: a table rewrite legitimately exceeds
    # the runtime statement/command timeouts, and aborting one halfway is worse
    # than a slow startup. The same applies to waiting on the advisory lock.
    migration_engine = create_async_engine(config.db.url, poolclass=NullPool)
    try:
        async with migration_engine.connect() as conn:
            await _rename_legacy_revisions(conn)
            current = await conn.run_sync(_current_revisions)
            if current == heads:
                logger.info(
                    "Схема БД актуальна (revision: %s)", ", ".join(sorted(heads)) or "base"
                )
                return

            pending = ", ".join(sorted(heads)) or "base"
            if not config.db_auto_migrate:
                raise RuntimeError(
                    f"Схема БД отстаёт от head ({pending}), текущая: "
                    f"{', '.join(sorted(current)) or 'base'}. DB_AUTO_MIGRATE выключен."
                )

            logger.warning(
                "Схема БД отстаёт: current=%s → head=%s. Применяем миграции...",
                ", ".join(sorted(current)) or "base", pending,
            )

            # The lock is held by this connection until the run finishes: another
            # instance that started at the same time waits and then sees head.
            await conn.execute(
                text("SELECT pg_advisory_lock(:key)"), {"key": _MIGRATION_LOCK_KEY}
            )
            try:
                current = await conn.run_sync(_current_revisions)
                if current == heads:
                    logger.info("Миграции уже применены параллельным инстансом")
                    return

                await asyncio.wait_for(
                    asyncio.to_thread(_upgrade, cfg), timeout=config.db_migrate_timeout
                )
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"Миграции не завершились за {config.db_migrate_timeout:.0f}s. "
                    "Запуск прерван."
                )
            except Exception as e:
                raise RuntimeError(f"Ошибка применения миграций: {e}") from e
            finally:
                await conn.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": _MIGRATION_LOCK_KEY}
                )
    finally:
        await migration_engine.dispose()

    logger.info("Миграции Alembic применены до head (%s)", pending)


async def shutdown_db() -> None:
    """Dispose the engine on shutdown."""
    await engine.dispose()
    logger.info("SQLAlchemy engine закрыт")
