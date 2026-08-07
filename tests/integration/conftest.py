from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from db.models import Base

TRUNCATE_TABLES = (
    "messages",
    "publications",
    "submission_media",
    "submissions",
    "tag_presets",
    "tag_preset_sections",
    "users",
)


async def _truncate_all(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE "
                + ", ".join(TRUNCATE_TABLES)
                + " RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture(scope="session")
def integration_db_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set; PostgreSQL integration tests are skipped.")
    return url


@pytest.fixture
async def integration_engine(integration_db_url: str) -> AsyncEngine:
    """Function-scoped on purpose.

    pytest-asyncio runs every test in its own event loop (the default
    ``asyncio_default_fixture_loop_scope``), while asyncpg connections are bound
    to the loop that opened them. A session-scoped engine would keep a pool tied
    to the first test's loop and every later test would blow up with
    ``RuntimeError: Event loop is closed``.
    """
    engine = create_async_engine(integration_db_url, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(integration_engine: AsyncEngine) -> AsyncSession:
    async with integration_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _truncate_all(integration_engine)

    session_factory = async_sessionmaker(integration_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()

    await _truncate_all(integration_engine)
