"""Integration tests for bootstrap_roles() (core/bot.py)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import db.session as session_module
from core.bot import bootstrap_roles
from core.messages import ROLE_BOOTSTRAP_PLACEHOLDER_NAME
from db.models import User
from db.queries import get_or_create_user

pytestmark = pytest.mark.integration


def _patch_session_factory(integration_engine, monkeypatch) -> None:
    """bootstrap_roles opens its own sessions via db.session.session_factory;
    point it at the integration engine so the test DB is used."""
    monkeypatch.setattr(
        session_module,
        "session_factory",
        async_sessionmaker(integration_engine, expire_on_commit=False),
    )


@pytest.mark.asyncio
async def test_bootstrap_roles_creates_rows_for_config_ids_on_empty_table(
    db_session, integration_engine, monkeypatch
) -> None:
    from core import config as cfg_module
    monkeypatch.setattr(cfg_module.config, "moderator_ids", [111, 222])
    monkeypatch.setattr(cfg_module.config, "admin_ids", [111])
    _patch_session_factory(integration_engine, monkeypatch)

    await bootstrap_roles()

    moderator = await db_session.scalar(select(User).where(User.telegram_id == 111))
    plain_moderator = await db_session.scalar(select(User).where(User.telegram_id == 222))

    assert moderator is not None
    assert moderator.is_moderator is True
    assert moderator.is_admin is True
    assert moderator.full_name == ROLE_BOOTSTRAP_PLACEHOLDER_NAME

    assert plain_moderator is not None
    assert plain_moderator.is_moderator is True
    assert plain_moderator.is_admin is False
    assert plain_moderator.full_name == ROLE_BOOTSTRAP_PLACEHOLDER_NAME


@pytest.mark.asyncio
async def test_bootstrap_roles_is_additive_and_preserves_existing_rows(
    db_session, integration_engine, monkeypatch
) -> None:
    from core import config as cfg_module
    monkeypatch.setattr(cfg_module.config, "moderator_ids", [201, 202])
    monkeypatch.setattr(cfg_module.config, "admin_ids", [])
    _patch_session_factory(integration_engine, monkeypatch)

    # Existing row without roles — must keep username/full_name after bootstrap.
    existing, _ = await get_or_create_user(db_session, 201, "mod", "Mod Name")
    # Non-config user with manually granted flags — must not be reset.
    manual, _ = await get_or_create_user(db_session, 999, "manual", "Manual Mod")
    manual.is_moderator = True
    manual.is_admin = True
    await db_session.commit()

    await bootstrap_roles()

    await db_session.refresh(existing)
    await db_session.refresh(manual)

    assert existing.is_moderator is True
    assert existing.is_admin is False
    assert existing.username == "mod"
    assert existing.full_name == "Mod Name"

    # Manually set flags for a non-config user survive a re-run.
    assert manual.is_moderator is True
    assert manual.is_admin is True

    created = await db_session.scalar(select(User).where(User.telegram_id == 202))
    assert created is not None
    assert created.is_moderator is True
    assert created.is_admin is False
    assert created.full_name == ROLE_BOOTSTRAP_PLACEHOLDER_NAME
