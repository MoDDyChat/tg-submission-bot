"""Unit tests for services/moderator_invites.py."""

from __future__ import annotations

import string
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db.models import ModeratorInvite
from services import moderator_invites
from tests.helpers import make_bot, make_user


def _mock_session() -> AsyncMock:
    session = AsyncMock()
    # session.add is synchronous in SQLAlchemy; AsyncMock would return an
    # unawaited coroutine and emit RuntimeWarnings
    session.add = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = []
    result.fetchone.return_value = None
    result.first.return_value = None
    result.scalar_one.return_value = 0
    result.scalar_one_or_none.return_value = None
    result.rowcount = 0
    session.execute.return_value = result
    return session


def _used_row(created_by: int = 9):
    row = MagicMock()
    row.created_by = created_by
    return row


# ── create_invite ──────────────────────────────────────────────────

async def test_create_invite_returns_invite_with_valid_token() -> None:
    session = _mock_session()
    creator = make_user(user_id=9, telegram_id=90)

    invite = await moderator_invites.create_invite(session, created_by=creator, ttl_hours=24)

    assert invite.created_by == 9
    assert invite.used_at is None
    # token_urlsafe(24) → 32 chars; prefix + token must fit Telegram's 64-char
    # deep-link payload alphabet A-Za-z0-9_-
    assert len(invite.token) == 32
    payload = f"{moderator_invites.INVITE_PREFIX}{invite.token}"
    assert len(payload) <= 64
    assert set(payload) <= set(string.ascii_letters + string.digits + "_-")
    now = datetime.now(tz=timezone.utc)
    assert now < invite.expires_at < now + timedelta(hours=25)
    session.add.assert_called_once_with(invite)


async def test_create_invite_respects_custom_ttl() -> None:
    session = _mock_session()
    invite = await moderator_invites.create_invite(
        session, created_by=make_user(user_id=9, telegram_id=90), ttl_hours=6,
    )
    now = datetime.now(tz=timezone.utc)
    assert now < invite.expires_at < now + timedelta(hours=7)


def test_build_invite_link() -> None:
    link = moderator_invites.build_invite_link("tok", "mybot")
    assert link == "https://t.me/mybot?start=modinvite_tok"


# ── redeem_invite ──────────────────────────────────────────────────

async def test_redeem_invite_success() -> None:
    session = _mock_session()
    result = MagicMock()
    result.first.return_value = _used_row(created_by=9)
    session.execute.return_value = result
    creator = make_user(user_id=9, telegram_id=90)
    target = make_user(user_id=5, telegram_id=50)
    bot = make_bot()

    with (
        patch.object(moderator_invites, "get_user_by_id", AsyncMock(return_value=creator)),
        patch.object(moderator_invites.roles, "grant_moderator", AsyncMock()) as grant,
        patch.object(moderator_invites.roles, "notify_role_change", AsyncMock()) as notify,
    ):
        ok = await moderator_invites.redeem_invite(session, bot, token="tok", user=target)

    assert ok is True
    # The atomic one-shot guard lives in the UPDATE itself
    sql, params = session.execute.call_args.args
    assert "used_at IS NULL" in str(sql)
    assert "expires_at > NOW()" in str(sql)
    assert params == {"user_id": 5, "token": "tok"}
    # actor is the invite issuer, not the invitee
    grant.assert_awaited_once()
    assert grant.call_args.kwargs["actor"] is creator
    assert grant.call_args.kwargs["target"] is target
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    # notifications strictly after the single commit
    notify.assert_awaited_once_with(
        bot, session,
        actor=creator, target=target, action="moderator_granted",
    )


async def test_redeem_same_token_second_time_fails_and_does_not_change_roles() -> None:
    """done_when: второй вызов redeem_invite возвращает неуспех и не меняет роли."""
    session = _mock_session()
    result = MagicMock()
    result.first.return_value = _used_row(created_by=9)
    session.execute.return_value = result
    creator = make_user(user_id=9, telegram_id=90)
    target = make_user(user_id=5, telegram_id=50)

    with (
        patch.object(moderator_invites, "get_user_by_id", AsyncMock(return_value=creator)),
        patch.object(moderator_invites.roles, "grant_moderator", AsyncMock()),
        patch.object(moderator_invites.roles, "notify_role_change", AsyncMock()),
    ):
        first = await moderator_invites.redeem_invite(session, make_bot(), token="tok", user=target)
    assert first is True
    assert session.commit.call_count == 1

    # Second redeem: the UPDATE matches no rows (already used)
    result.first.return_value = None
    commits_before = session.commit.call_count
    with (
        patch.object(moderator_invites, "get_user_by_id", AsyncMock()) as get_creator,
        patch.object(moderator_invites.roles, "grant_moderator", AsyncMock()) as grant,
        patch.object(moderator_invites.roles, "notify_role_change", AsyncMock()) as notify,
    ):
        second = await moderator_invites.redeem_invite(session, make_bot(), token="tok", user=target)

    assert second is False
    grant.assert_not_awaited()
    notify.assert_not_awaited()
    get_creator.assert_not_awaited()
    assert session.commit.call_count == commits_before
    session.rollback.assert_not_awaited()


async def test_redeem_expired_token_fails() -> None:
    session = _mock_session()
    # UPDATE matched nothing: expires_at already passed
    result = MagicMock()
    result.first.return_value = None
    session.execute.return_value = result

    with (
        patch.object(moderator_invites, "get_user_by_id", AsyncMock()),
        patch.object(moderator_invites.roles, "grant_moderator", AsyncMock()) as grant,
        patch.object(moderator_invites.roles, "notify_role_change", AsyncMock()) as notify,
    ):
        ok = await moderator_invites.redeem_invite(
            session, make_bot(), token="tok", user=make_user(user_id=5, telegram_id=50),
        )

    assert ok is False
    grant.assert_not_awaited()
    notify.assert_not_awaited()
    session.commit.assert_not_awaited()


async def test_redeem_nonexistent_token_fails() -> None:
    session = _mock_session()
    result = MagicMock()
    result.first.return_value = None
    session.execute.return_value = result

    with (
        patch.object(moderator_invites, "get_user_by_id", AsyncMock()),
        patch.object(moderator_invites.roles, "grant_moderator", AsyncMock()) as grant,
        patch.object(moderator_invites.roles, "notify_role_change", AsyncMock()) as notify,
    ):
        ok = await moderator_invites.redeem_invite(
            session, make_bot(), token="never-issued", user=make_user(user_id=5, telegram_id=50),
        )

    assert ok is False
    grant.assert_not_awaited()
    notify.assert_not_awaited()
    session.commit.assert_not_awaited()


async def test_redeem_rolls_back_when_grant_moderator_fails() -> None:
    session = _mock_session()
    result = MagicMock()
    result.first.return_value = _used_row(created_by=9)
    session.execute.return_value = result
    creator = make_user(user_id=9, telegram_id=90)
    target = make_user(user_id=5, telegram_id=50)
    invite = ModeratorInvite(
        token="tok", created_by=9,
        expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
    )

    with (
        patch.object(moderator_invites, "get_user_by_id", AsyncMock(return_value=creator)),
        patch.object(
            moderator_invites.roles, "grant_moderator",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch.object(moderator_invites.roles, "notify_role_change", AsyncMock()) as notify,
    ):
        with pytest.raises(RuntimeError):
            await moderator_invites.redeem_invite(session, make_bot(), token="tok", user=target)

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    notify.assert_not_awaited()
    # The invite row was never published as used: the UPDATE is the only
    # mutator of used_at and its transaction was rolled back.
    assert invite.used_at is None


# ── cleanup_expired_invites ────────────────────────────────────────

async def test_cleanup_expired_invites_deletes_expired_rows() -> None:
    session = _mock_session()
    result = MagicMock()
    result.rowcount = 3
    session.execute.return_value = result

    removed = await moderator_invites.cleanup_expired_invites(session)

    assert removed == 3
    sql = str(session.execute.call_args.args[0])
    assert "DELETE FROM moderator_invites" in sql
    assert "expires_at" in sql
