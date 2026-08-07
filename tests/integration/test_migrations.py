from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from db.models import Base

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_TABLES = {
    "users",
    "submissions",
    "submission_media",
    "publications",
    "user_topics",
    "edit_locks",
    "tag_preset_sections",
    "tag_presets",
    "messages",
    "system_messages",
    "moderator_invites",
}


def _build_alembic_env(database_url: str) -> dict[str, str]:
    url = make_url(database_url)
    env = os.environ.copy()
    env.update(
        {
            # Config uses "__" as the nested delimiter — flat BOT_TOKEN/DB_HOST
            # names are ignored and alembic dies on a missing `bot` field.
            "BOT__TOKEN": env.get("BOT__TOKEN", "123456:TEST"),
            "MODERATOR_IDS": env.get("MODERATOR_IDS", "1,2"),
            "CHANNEL_ID": env.get("CHANNEL_ID", "-100111"),
            "MODERATOR_GROUP_ID": env.get("MODERATOR_GROUP_ID", "-100333"),
            "TIMEZONE": env.get("TIMEZONE", "Europe/Moscow"),
            "DB__HOST": url.host or "127.0.0.1",
            "DB__PORT": str(url.port or 5432),
            "DB__NAME": url.database or "",
            "DB__USER": url.username or "",
            "DB__PASSWORD": url.password or "",
        }
    )
    return env


@pytest.mark.asyncio
async def test_alembic_upgrade_head_creates_expected_tables(integration_db_url: str) -> None:
    engine = create_async_engine(integration_db_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            # alembic_version is not part of Base.metadata: drop_all leaves a
            # leftover row, and if it already says head the subprocess upgrade
            # below becomes a no-op while the app tables are gone.
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=_build_alembic_env(integration_db_url),
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stdout + "\n" + result.stderr

        async with engine.connect() as conn:
            table_names = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
            user_topic_columns = await conn.run_sync(
                lambda sync_conn: {
                    column["name"]
                    for column in inspect(sync_conn).get_columns("user_topics")
                }
            )
            submission_columns = await conn.run_sync(
                lambda sync_conn: {
                    column["name"]
                    for column in inspect(sync_conn).get_columns("submissions")
                }
            )
            user_columns = await conn.run_sync(
                lambda sync_conn: {
                    column["name"]
                    for column in inspect(sync_conn).get_columns("users")
                }
            )

        assert REQUIRED_TABLES.issubset(table_names)
        assert {
            "title_sync_version",
            "title_applied_version",
            "title_force_sync_version",
        }.issubset(user_topic_columns)
        assert "card_rendered_hash" in submission_columns
        assert {"role_granted_by", "role_granted_at"}.issubset(user_columns)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_alembic_upgrade_head_adds_suggested_tags_column(integration_db_url: str) -> None:
    """After ``alembic upgrade head`` the submissions.suggested_tags column exists."""
    engine = create_async_engine(integration_db_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=_build_alembic_env(integration_db_url),
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stdout + "\n" + result.stderr

        async with engine.connect() as conn:
            submission_columns = await conn.run_sync(
                lambda sync_conn: {
                    column["name"]
                    for column in inspect(sync_conn).get_columns("submissions")
                }
            )

        assert "suggested_tags" in submission_columns
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_alembic_downgrade_to_base_and_back(integration_db_url: str) -> None:
    """Verify the whole history rolls back to base and reapplies cleanly."""
    engine = create_async_engine(integration_db_url, pool_pre_ping=True)
    env = _build_alembic_env(integration_db_url)

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "alembic"] + args,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    try:
        # Ensure we start at head
        r = _run(["upgrade", "head"])
        assert r.returncode == 0, r.stdout + "\n" + r.stderr

        # Roll the whole history back. A step count would have to track how
        # many revisions exist; "base" stays correct as the history grows.
        r = _run(["downgrade", "base"])
        assert r.returncode == 0, r.stdout + "\n" + r.stderr

        # Upgrade back to head
        r = _run(["upgrade", "head"])
        assert r.returncode == 0, r.stdout + "\n" + r.stderr

        async with engine.connect() as conn:
            table_names = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )

        assert REQUIRED_TABLES.issubset(table_names)
    finally:
        await engine.dispose()
