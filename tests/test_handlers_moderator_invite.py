"""Unit tests for handlers/moderator_invite.py."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Dispatcher
from aiogram.types import Chat, Message, Update, User as TgUser

import core.messages as msg
from handlers import common, moderator, moderator_invite
from services import moderator_invites as invites_service
from tests.helpers import FakeState, make_bot, make_message, make_user


def _make_tg_message(text: str, *, user_id: int = 50) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=101, type="private"),
        from_user=TgUser(id=user_id, is_bot=False, first_name="Bob", username="bob"),
        text=text,
    )


@pytest.fixture(scope="module")
def _dispatcher() -> Dispatcher:
    """Mirror the production router order for /start handling."""
    dp = Dispatcher()
    dp.include_routers(moderator_invite.router, moderator.router, common.router)
    return dp


# ── routing: modinvite_ reaches the dedicated handler ─────────────

async def test_modinvite_deep_link_reaches_dedicated_handler_not_common_start(
    _dispatcher: Dispatcher,
) -> None:
    dp = _dispatcher
    session = AsyncMock()
    db_user = make_user(user_id=5, telegram_id=50)
    db_user.is_moderator = False
    bot = make_bot()
    update = Update(update_id=1, message=_make_tg_message("/start modinvite_abc"))

    with patch.object(invites_service, "redeem_invite", AsyncMock(return_value=True)) as redeem:
        await dp.feed_update(bot, update, session=session, db_user=db_user)

    # Had common.cmd_start intercepted the /start, redeem would never run.
    redeem.assert_awaited_once()
    assert redeem.call_args.kwargs["token"] == "abc"
    assert redeem.call_args.kwargs["user"] is db_user


async def test_modinvite_for_existing_moderator_does_not_burn_token(
    _dispatcher: Dispatcher,
) -> None:
    """The invite router must also win over cmd_start_review for moderators."""
    dp = _dispatcher
    session = AsyncMock()
    db_user = make_user(user_id=5, telegram_id=50, username="bob")
    db_user.is_moderator = True
    bot = make_bot()
    update = Update(update_id=2, message=_make_tg_message("/start modinvite_abc"))

    with patch.object(invites_service, "redeem_invite", AsyncMock()) as redeem:
        await dp.feed_update(bot, update, session=session, db_user=db_user)

    redeem.assert_not_awaited()
    # aiogram 3.7 executes message.answer() as bot(<SendMessage>)
    texts = [
        call.args[0].text for call in bot.call_args_list if hasattr(call.args[0], "text")
    ]
    assert msg.MODERATOR_INVITE_ALREADY_MODERATOR.format(user="@bob") in texts


# ── handler logic ──────────────────────────────────────────────────

async def test_already_moderator_does_not_burn_token_and_opens_home() -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=5, telegram_id=50, username="bob")
    db_user.is_moderator = True
    message = make_message(text="/start modinvite_abc", from_user_id=50)

    with (
        patch.object(invites_service, "redeem_invite", AsyncMock()) as redeem,
        patch.object(moderator_invite, "show_moderator_home", AsyncMock()) as home,
    ):
        await moderator_invite.cmd_start_moderator_invite(message, session, state, db_user)

    redeem.assert_not_awaited()
    home.assert_awaited_once_with(message, state)
    assert message.answer.await_args.args[0] == msg.MODERATOR_INVITE_ALREADY_MODERATOR.format(
        user="@bob"
    )


async def test_banned_user_does_not_burn_token() -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=5, telegram_id=50, is_banned=True)
    message = make_message(text="/start modinvite_abc", from_user_id=50)

    with (
        patch.object(invites_service, "redeem_invite", AsyncMock()) as redeem,
        patch.object(moderator_invite, "show_moderator_home", AsyncMock()) as home,
    ):
        await moderator_invite.cmd_start_moderator_invite(message, session, state, db_user)

    redeem.assert_not_awaited()
    home.assert_not_awaited()
    message.answer.assert_awaited_once_with(msg.MODERATOR_INVITE_BANNED)


async def test_invalid_or_used_link_shows_error() -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=5, telegram_id=50)
    message = make_message(text="/start modinvite_abc", from_user_id=50)

    with (
        patch.object(invites_service, "redeem_invite", AsyncMock(return_value=False)),
        patch.object(moderator_invite, "show_moderator_home", AsyncMock()) as home,
    ):
        await moderator_invite.cmd_start_moderator_invite(message, session, state, db_user)

    message.answer.assert_awaited_once_with(msg.MODERATOR_INVITE_INVALID)
    home.assert_not_awaited()


async def test_successful_redeem_opens_moderator_home() -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=5, telegram_id=50)
    message = make_message(text="/start modinvite_abc", from_user_id=50)

    with (
        patch.object(invites_service, "redeem_invite", AsyncMock(return_value=True)) as redeem,
        patch.object(moderator_invite, "show_moderator_home", AsyncMock()) as home,
    ):
        await moderator_invite.cmd_start_moderator_invite(message, session, state, db_user)

    redeem.assert_awaited_once()
    home.assert_awaited_once_with(message, state)
