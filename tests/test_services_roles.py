"""Unit tests for services/roles.py and the remove-flow lock release."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError

import core.messages as msg
from core.exceptions import (
    CannotChangeOwnRoleError,
    CannotRemoveLastAdminError,
    ConfigProtectedRoleError,
    RoleAlreadyGrantedError,
    RoleTargetBannedError,
)
from db.models import EditLock
from services import admin_notifications, edit_lock, roles
from tests.helpers import FakeSessionFactory, make_user


def _mock_session() -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.fetchall.return_value = []
    result.fetchone.return_value = None
    result.scalar_one.return_value = 0
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    return session


def _actor() -> MagicMock:
    return make_user(user_id=9, telegram_id=90, username="mod")


def _target(**kwargs) -> MagicMock:
    return make_user(user_id=5, telegram_id=50, username="bob", **kwargs)


# ── Invariant: cannot change own role ──────────────────────────────

async def test_cannot_change_own_role() -> None:
    session = _mock_session()
    user = _actor()
    with pytest.raises(CannotChangeOwnRoleError):
        await roles.grant_moderator(session, AsyncMock(), actor=user, target=user)
    with pytest.raises(CannotChangeOwnRoleError):
        await roles.remove_moderator(session, AsyncMock(), actor=user, target=user)


# ── Invariant: cannot remove the last admin ────────────────────────

async def test_cannot_revoke_last_admin() -> None:
    session = _mock_session()
    target = _target()
    target.is_admin = True
    with patch.object(roles, "count_admins", AsyncMock(return_value=1)):
        with pytest.raises(CannotRemoveLastAdminError):
            await roles.revoke_admin(session, AsyncMock(), actor=_actor(), target=target)


async def test_can_revoke_admin_when_another_admin_remains() -> None:
    session = _mock_session()
    target = _target()
    target.is_admin = True
    with patch.object(roles, "count_admins", AsyncMock(return_value=2)):
        with patch.object(roles, "set_roles", AsyncMock()) as set_roles_mock:
            await roles.revoke_admin(session, AsyncMock(), actor=_actor(), target=target)
    set_roles_mock.assert_awaited_once_with(
        session, 5,
        is_moderator=True, is_admin=False,
        granted_by=9, granted_at=ANY,
    )


# ── Invariant: config-protected users cannot be demoted ────────────

async def test_cannot_remove_config_protected_moderator() -> None:
    session = _mock_session()
    # telegram_id=2 is in MODERATOR_IDS (test env)
    target = make_user(user_id=3, telegram_id=2, username="env_mod")
    with pytest.raises(ConfigProtectedRoleError):
        await roles.remove_moderator(session, AsyncMock(), actor=_actor(), target=target)


async def test_cannot_revoke_config_protected_admin() -> None:
    session = _mock_session()
    target = _target()
    target.is_admin = True
    with patch.object(roles.config, "admin_ids", [50]):
        with pytest.raises(ConfigProtectedRoleError):
            await roles.revoke_admin(session, AsyncMock(), actor=_actor(), target=target)


# ── Invariant: duplicate grant is an error, not a silent no-op ─────

async def test_cannot_grant_moderator_when_already_moderator() -> None:
    session = _mock_session()
    target = _target()
    target.is_moderator = True
    with pytest.raises(RoleAlreadyGrantedError):
        await roles.grant_moderator(session, AsyncMock(), actor=_actor(), target=target)


async def test_cannot_grant_admin_when_already_admin() -> None:
    session = _mock_session()
    target = _target()
    target.is_admin = True
    with pytest.raises(RoleAlreadyGrantedError):
        await roles.grant_admin(session, AsyncMock(), actor=_actor(), target=target)


# ── Invariant: banned users cannot be granted a role ───────────────

async def test_cannot_grant_moderator_to_banned_user() -> None:
    session = _mock_session()
    target = _target(is_banned=True)
    with pytest.raises(RoleTargetBannedError):
        await roles.grant_moderator(session, AsyncMock(), actor=_actor(), target=target)


async def test_cannot_grant_admin_to_banned_user() -> None:
    session = _mock_session()
    target = _target(is_banned=True)
    with pytest.raises(RoleTargetBannedError):
        await roles.grant_admin(session, AsyncMock(), actor=_actor(), target=target)


async def test_grant_locks_target_row_before_checks() -> None:
    """Grant and ban serialize on the same row lock; flags are re-checked under it."""
    session = _mock_session()
    target = _target()
    with patch.object(roles, "set_roles", AsyncMock()):
        await roles.grant_moderator(session, AsyncMock(), actor=_actor(), target=target)
    session.refresh.assert_awaited_once_with(target, with_for_update=True)


async def test_grant_admin_locks_target_row_before_checks() -> None:
    session = _mock_session()
    target = _target()
    with patch.object(roles, "set_roles", AsyncMock()):
        await roles.grant_admin(session, AsyncMock(), actor=_actor(), target=target)
    session.refresh.assert_awaited_once_with(target, with_for_update=True)


# ── "Admin is always moderator" flag matrix ────────────────────────

async def test_grant_admin_sets_both_flags() -> None:
    session = _mock_session()
    with patch.object(roles, "set_roles", AsyncMock()) as set_roles_mock:
        await roles.grant_admin(session, AsyncMock(), actor=_actor(), target=_target())
    set_roles_mock.assert_awaited_once_with(
        session, 5,
        is_moderator=True, is_admin=True,
        granted_by=9, granted_at=ANY,
    )


async def test_grant_moderator_preserves_admin_flag() -> None:
    session = _mock_session()
    target = _target()
    target.is_admin = True
    with patch.object(roles, "set_roles", AsyncMock()) as set_roles_mock:
        await roles.grant_moderator(session, AsyncMock(), actor=_actor(), target=target)
    set_roles_mock.assert_awaited_once_with(
        session, 5,
        is_moderator=True, is_admin=True,
        granted_by=9, granted_at=ANY,
    )


async def test_remove_moderator_clears_both_flags() -> None:
    session = _mock_session()
    with patch.object(roles, "release_moderator_locks", AsyncMock(return_value=[])):
        with patch.object(roles, "set_roles", AsyncMock()) as set_roles_mock:
            await roles.remove_moderator(session, AsyncMock(), actor=_actor(), target=_target())
    set_roles_mock.assert_awaited_once_with(
        session, 5,
        is_moderator=False, is_admin=False,
        granted_by=None, granted_at=None,
    )


# ── Lock release: remove_moderator, correct owner per resource type ─

async def test_remove_moderator_releases_locks_by_correct_owner() -> None:
    session = _mock_session()
    target = _target()
    with patch.object(roles, "release_moderator_locks", AsyncMock(return_value=[])) as release:
        await roles.remove_moderator(session, AsyncMock(), actor=_actor(), target=target)
    release.assert_awaited_once_with(session, user_id=5, telegram_id=50)


async def test_release_moderator_locks_deletes_submission_and_management_locks() -> None:
    now = datetime.now(tz=timezone.utc)
    row_sub = MagicMock(resource_type="submission", resource_id="7", moderator_id=5,
                        acquired_at=now, expires_at=now)
    row_mgmt = MagicMock(resource_type="management", resource_id="presets", moderator_id=50,
                         acquired_at=now, expires_at=now)
    result = MagicMock()
    result.fetchall.return_value = [row_sub, row_mgmt]
    session = AsyncMock()
    session.execute.return_value = result

    removed = await edit_lock.release_moderator_locks(session, user_id=5, telegram_id=50)

    assert [(lock.resource_type, lock.resource_id) for lock in removed] == [
        ("submission", "7"),
        ("management", "presets"),
    ]
    sql, params = session.execute.call_args.args
    assert params == {"user_id": 5, "telegram_id": 50}
    assert "resource_type = 'submission' AND moderator_id = :user_id" in str(sql)
    assert "resource_type = 'management' AND moderator_id = :telegram_id" in str(sql)


async def test_revoke_admin_does_not_release_locks() -> None:
    session = _mock_session()
    target = _target()
    target.is_admin = True
    with patch.object(roles, "count_admins", AsyncMock(return_value=2)):
        with patch.object(roles, "release_moderator_locks", AsyncMock()) as release:
            await roles.revoke_admin(session, AsyncMock(), actor=_actor(), target=target)
    release.assert_not_awaited()


# ── Invite invalidation on demotion ────────────────────────────────

async def test_remove_moderator_invalidates_target_unused_invites() -> None:
    session = _mock_session()
    with patch.object(roles, "release_moderator_locks", AsyncMock(return_value=[])):
        await roles.remove_moderator(session, AsyncMock(), actor=_actor(), target=_target())

    executed = [
        (str(call.args[0]), call.args[1] if len(call.args) > 1 else None)
        for call in session.execute.call_args_list
    ]
    assert any(
        "moderator_invites" in sql and params is not None and params.get("target_id") == 5
        for sql, params in executed
    )


async def test_revoke_admin_invalidates_target_unused_invites() -> None:
    session = _mock_session()
    target = _target()
    target.is_admin = True
    with patch.object(roles, "count_admins", AsyncMock(return_value=2)):
        await roles.revoke_admin(session, AsyncMock(), actor=_actor(), target=target)

    executed = [
        (str(call.args[0]), call.args[1] if len(call.args) > 1 else None)
        for call in session.execute.call_args_list
    ]
    assert any(
        "moderator_invites" in sql and params is not None and params.get("target_id") == 5
        for sql, params in executed
    )


# ── notify_role_change ─────────────────────────────────────────────

@pytest.mark.parametrize(
    ("action", "message_key"),
    [
        ("moderator_granted", "ADMIN_NOTIFY_MODERATOR_ADDED"),
        ("moderator_removed", "ADMIN_NOTIFY_MODERATOR_REMOVED"),
        ("admin_granted", "ADMIN_NOTIFY_ADMIN_GRANTED"),
        ("admin_revoked", "ADMIN_NOTIFY_ADMIN_REVOKED"),
    ],
)
async def test_notify_role_change_uses_correct_action_text(action: str, message_key: str) -> None:
    bot = AsyncMock()
    session = AsyncMock()
    actor = _actor()
    target = _target()

    with patch.object(admin_notifications, "notify_admins", AsyncMock()) as notify:
        await roles.notify_role_change(bot, session, actor=actor, target=target, action=action)

    notify.assert_awaited_once()
    expected = getattr(msg, message_key).format(actor="@mod", user="@bob")
    assert notify.call_args.kwargs["action_text"] == expected


async def test_notify_role_change_sends_dm_to_target() -> None:
    bot = AsyncMock()
    session = AsyncMock()
    with patch.object(admin_notifications, "notify_admins", AsyncMock()):
        await roles.notify_role_change(
            bot, session, actor=_actor(), target=_target(), action="moderator_granted",
        )
    bot.send_message.assert_awaited_once()
    assert bot.send_message.call_args.kwargs["chat_id"] == 50
    assert bot.send_message.call_args.kwargs["text"] == msg.MODERATOR_INVITE_WELCOME.format(user="@bob")


async def test_notify_role_change_target_dm_differs_from_admin_broadcast() -> None:
    """moderator_granted: target DM is second-person, admin broadcast third-person."""
    bot = AsyncMock()
    session = AsyncMock()
    with patch.object(admin_notifications, "notify_admins", AsyncMock()) as notify:
        await roles.notify_role_change(
            bot, session, actor=_actor(), target=_target(), action="moderator_granted",
        )
    admin_text = notify.call_args.kwargs["action_text"]
    target_text = bot.send_message.call_args.kwargs["text"]
    assert admin_text == msg.ADMIN_NOTIFY_MODERATOR_ADDED.format(actor="@mod", user="@bob")
    assert target_text == msg.MODERATOR_INVITE_WELCOME.format(user="@bob")
    assert target_text != admin_text


async def test_notify_role_change_excludes_target_from_admin_broadcast() -> None:
    """The target never gets the third-person audit line — only their own notice."""
    bot = AsyncMock()
    session = AsyncMock()
    target = _target()
    target.is_admin = True
    with patch.object(admin_notifications, "notify_admins", AsyncMock()) as notify:
        await roles.notify_role_change(
            bot, session, actor=_actor(), target=target, action="admin_granted",
        )
    assert notify.call_args.kwargs["exclude_telegram_ids"] == {target.telegram_id}


async def test_notify_role_change_dms_new_admin_target() -> None:
    """A target who just became admin still gets the second-person notice."""
    bot = AsyncMock()
    session = AsyncMock()
    target = _target()
    target.is_admin = True
    with patch.object(admin_notifications, "notify_admins", AsyncMock()):
        await roles.notify_role_change(
            bot, session, actor=_actor(), target=target, action="admin_granted",
        )
    bot.send_message.assert_awaited_once()
    assert bot.send_message.call_args.kwargs["chat_id"] == 50
    assert (
        bot.send_message.call_args.kwargs["text"]
        == msg.MODERATOR_ADMIN_GRANTED_NOTICE.format(user="@bob")
    )


async def test_notify_role_change_suppresses_forbidden_from_target_dm() -> None:
    bot = AsyncMock()
    bot.send_message = AsyncMock(
        side_effect=TelegramForbiddenError(method="sendMessage", message="bot was blocked")
    )
    session = AsyncMock()
    with patch.object(admin_notifications, "notify_admins", AsyncMock()) as notify:
        await roles.notify_role_change(
            bot, session, actor=_actor(), target=_target(), action="moderator_removed",
        )
    notify.assert_awaited_once()
    bot.send_message.assert_awaited_once()


async def test_notify_role_change_suppresses_other_api_errors_from_target_dm() -> None:
    """A network/API failure on the target DM must not break the caller."""
    bot = AsyncMock()
    bot.send_message = AsyncMock(
        side_effect=TelegramNetworkError(method="sendMessage", message="connection reset")
    )
    session = AsyncMock()
    with patch.object(admin_notifications, "notify_admins", AsyncMock()):
        await roles.notify_role_change(
            bot, session, actor=_actor(), target=_target(), action="moderator_removed",
        )
    bot.send_message.assert_awaited_once()


# ── repaint_released_cards (post-commit helper) ────────────────────

async def test_repaint_released_cards_repaints_submission_cards_only() -> None:
    sub = MagicMock()
    sub.user.id = 7
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = sub
    session.execute.return_value = result
    factory = FakeSessionFactory(session)

    sub_lock = EditLock()
    sub_lock.resource_type = "submission"
    sub_lock.resource_id = "7"
    mgmt_lock = EditLock()
    mgmt_lock.resource_type = "management"
    mgmt_lock.resource_id = "presets"

    with (
        patch("db.queries.get_submission_with_user", AsyncMock(return_value=sub)) as get_sub,
        patch("services.topics.update_submission_card", AsyncMock()) as update_card,
        patch("services.topics.request_topic_title_sync", AsyncMock()) as sync_title,
    ):
        await roles.repaint_released_cards(AsyncMock(), factory, [sub_lock, mgmt_lock])

    get_sub.assert_awaited_once()
    update_card.assert_awaited_once()
    sync_title.assert_awaited_once_with(session, 7)
    session.commit.assert_awaited_once()


# ── protected predicates ───────────────────────────────────────────

async def test_is_moderator_protected_from_config() -> None:
    env_mod = make_user(user_id=3, telegram_id=2)
    plain = _target()
    assert roles.is_moderator_protected(env_mod) is True
    assert roles.is_moderator_protected(plain) is False


async def test_is_admin_protected_from_config() -> None:
    with patch.object(roles.config, "admin_ids", [50]):
        env_admin = _target()
        assert roles.is_admin_protected(env_admin) is True
    assert roles.is_admin_protected(_target()) is False
