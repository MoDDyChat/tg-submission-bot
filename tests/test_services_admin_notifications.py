"""Unit tests for services/admin_notifications.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramAPIError

from services import admin_notifications
from tests.helpers import make_user
from db.models import User


# ── Helpers ────────────────────────────────────────────────────────

def _make_admin(telegram_id: int, username: str | None = None) -> User:
    u = make_user(telegram_id=telegram_id, username=username)
    u.is_admin = True
    return u


# ── actor_display / user_display ─────────────────────────────────

def test_actor_display_with_username() -> None:
    user = make_user(username="alice")
    assert admin_notifications.actor_display(user) == "@alice"


def test_actor_display_without_username() -> None:
    user = make_user(username=None, full_name="Alice B")
    assert admin_notifications.actor_display(user) == "Alice B"


def test_actor_display_escapes_html() -> None:
    user = make_user(username=None, full_name="<Admin>")
    assert "&lt;Admin&gt;" in admin_notifications.actor_display(user)


# ── notify_admins — sends DM to each admin ───────────────────────

async def test_notify_admins_sends_dm_to_all_admins() -> None:
    bot = AsyncMock()
    session = AsyncMock()
    actor = make_user(telegram_id=1, username="mod")

    admins = [_make_admin(10, "admin1"), _make_admin(11, "admin2")]
    with patch.object(admin_notifications, "get_admin_users", AsyncMock(return_value=admins)):
        await admin_notifications.notify_admins(
            bot, session, actor=actor, action_text="some action"
        )

    assert bot.send_message.await_count == 2
    sent_ids = {call.kwargs["chat_id"] for call in bot.send_message.call_args_list}
    assert sent_ids == {10, 11}
    # Each DM should mention the action text
    for call in bot.send_message.call_args_list:
        assert "some action" in call.kwargs.get("text", "") or "some action" in str(call.args)


# ── exclude_actor — don't notify actor themselves ────────────────

async def test_notify_admins_excludes_actor_by_default() -> None:
    bot = AsyncMock()
    session = AsyncMock()
    actor = make_user(telegram_id=10, username="self_admin")

    admins = [_make_admin(10, "self_admin"), _make_admin(11, "other_admin")]
    with patch.object(admin_notifications, "get_admin_users", AsyncMock(return_value=admins)):
        await admin_notifications.notify_admins(
            bot, session, actor=actor, action_text="action"
        )

    assert bot.send_message.await_count == 1
    call = bot.send_message.call_args
    assert call.kwargs["chat_id"] == 11
    assert "action" in call.kwargs.get("text", "") or "action" in str(call.args)


async def test_notify_admins_respects_silent_setting() -> None:
    bot = AsyncMock()
    session = AsyncMock()
    actor = make_user(telegram_id=10, username="self_admin")

    admins = [_make_admin(11, "other_admin")]
    with patch.object(admin_notifications, "get_admin_users", AsyncMock(return_value=admins)):
        with patch.object(
            admin_notifications.config, "silent_moderator_notifications", True
        ):
            await admin_notifications.notify_admins(
                bot, session, actor=actor, action_text="action"
            )
        assert bot.send_message.call_args.kwargs["disable_notification"] is True

        with patch.object(
            admin_notifications.config, "silent_moderator_notifications", False
        ):
            await admin_notifications.notify_admins(
                bot, session, actor=actor, action_text="action"
            )
        assert bot.send_message.call_args.kwargs["disable_notification"] is False


async def test_notify_admins_includes_actor_when_exclude_false() -> None:
    bot = AsyncMock()
    session = AsyncMock()
    actor = make_user(telegram_id=10, username="self_admin")

    admins = [_make_admin(10, "self_admin"), _make_admin(11, "other_admin")]
    with patch.object(admin_notifications, "get_admin_users", AsyncMock(return_value=admins)):
        await admin_notifications.notify_admins(
            bot, session, actor=actor, action_text="action", exclude_actor=False
        )

    assert bot.send_message.await_count == 2


async def test_notify_admins_skips_excluded_telegram_ids() -> None:
    bot = AsyncMock()
    session = AsyncMock()
    actor = make_user(telegram_id=10, username="self_admin")

    admins = [_make_admin(11, "other_admin"), _make_admin(12, "excluded_admin")]
    with patch.object(admin_notifications, "get_admin_users", AsyncMock(return_value=admins)):
        await admin_notifications.notify_admins(
            bot, session, actor=actor, action_text="action",
            exclude_telegram_ids={12},
        )

    bot.send_message.assert_awaited_once()
    assert bot.send_message.call_args.kwargs["chat_id"] == 11


# ── telegram error is swallowed per-admin ────────────────────────

async def test_notify_admins_swallows_telegram_error() -> None:
    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=TelegramAPIError(method=None, message="forbidden"))  # type: ignore[arg-type]
    session = AsyncMock()
    actor = make_user(telegram_id=99)

    admins = [_make_admin(10), _make_admin(11)]
    with patch.object(admin_notifications, "get_admin_users", AsyncMock(return_value=admins)):
        # Must not raise even when bot raises for all admins
        await admin_notifications.notify_admins(
            bot, session, actor=actor, action_text="action"
        )

    assert bot.send_message.await_count == 2


# ── actor=None — system-originated notifications ─────────────────

async def test_notify_admins_with_no_actor_sends_to_all_admins() -> None:
    """A bot-originated notification (no moderator actor) must not exclude anyone."""
    bot = AsyncMock()
    session = AsyncMock()

    admins = [_make_admin(10, "admin1"), _make_admin(11, "admin2")]
    with patch.object(admin_notifications, "get_admin_users", AsyncMock(return_value=admins)):
        await admin_notifications.notify_admins(
            bot, session, actor=None, action_text="system action"
        )

    assert bot.send_message.await_count == 2
    sent_ids = {call.kwargs["chat_id"] for call in bot.send_message.call_args_list}
    assert sent_ids == {10, 11}


# ── empty admin list — no sends ───────────────────────────────────

async def test_notify_admins_no_admins_no_calls() -> None:
    bot = AsyncMock()
    session = AsyncMock()
    actor = make_user(telegram_id=1)

    with patch.object(admin_notifications, "get_admin_users", AsyncMock(return_value=[])):
        await admin_notifications.notify_admins(
            bot, session, actor=actor, action_text="action"
        )

    bot.send_message.assert_not_awaited()
