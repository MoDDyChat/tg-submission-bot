"""Unit tests for handlers/moderator/moderators.py and the roster UI."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import core.messages as msg
from handlers.moderator import moderators
from keyboards.callbacks import ManagementCB, ModeratorCB
from keyboards.moderator import management_menu_kb
from services import roles
from states.moderator import ModeratorReview, STATE_CATEGORY
from tests.helpers import FakeState, make_callback, make_message, make_user


def _button_by_text(kb, text):
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.text == text:
                return btn
    return None


# ── entry: «Модераторы» button visibility in the management menu ──

def test_management_menu_shows_moderators_button_for_admin() -> None:
    kb = management_menu_kb(is_admin=True)
    btn = _button_by_text(kb, msg.BTN_MODERATORS)
    assert btn is not None
    assert btn.callback_data == ManagementCB(action="moderators").pack()


def test_management_menu_hides_moderators_button_for_non_admin() -> None:
    kb = management_menu_kb(is_admin=False)
    assert _button_by_text(kb, msg.BTN_MODERATORS) is None


# ── entry: lock acquisition and admin gate ─────────────────────────

async def test_handle_moderators_menu_acquires_lock_and_renders(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    db_user.is_admin = True
    callback = make_callback(from_user_id=202)
    render = AsyncMock()

    monkeypatch.setattr(moderators.edit_lock, "acquire_lock", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(moderators, "_render_moderators_list", render)

    await moderators.handle_moderators_menu(callback, session, state, db_user)

    render.assert_awaited_once()
    callback.answer.assert_awaited()


async def test_handle_moderators_menu_rejected_for_non_admin(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    db_user.is_admin = False
    callback = make_callback(from_user_id=202)
    acquire = AsyncMock()

    monkeypatch.setattr(moderators.edit_lock, "acquire_lock", acquire)

    await moderators.handle_moderators_menu(callback, session, state, db_user)

    acquire.assert_not_awaited()
    callback.answer.assert_awaited_once_with(msg.MODERATORS_ADMIN_ONLY, show_alert=True)


# ── ModeratorCB callbacks are admin-gated inside handlers ──────────

async def test_moderator_cb_rejected_for_non_admin(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    db_user.is_admin = False
    callback = make_callback(from_user_id=202)
    guard = AsyncMock()

    monkeypatch.setattr(moderators, "_guard_management_lock", guard)

    await moderators.handle_moderator_list(callback, session, state, db_user)

    guard.assert_not_awaited()
    callback.answer.assert_awaited_once_with(msg.MODERATORS_ADMIN_ONLY, show_alert=True)


# ── list rendering ─────────────────────────────────────────────────

async def test_handle_moderator_list_renders_roster(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    db_user.is_admin = True
    callback = make_callback(from_user_id=202)

    admin = make_user(user_id=1, telegram_id=1, username="chief")  # telegram_id in MODERATOR_IDS
    admin.is_admin = True
    mod = make_user(user_id=3, telegram_id=3, username="bob")

    monkeypatch.setattr(moderators, "_guard_management_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(moderators, "list_moderators", AsyncMock(return_value=[admin, mod]))
    render = AsyncMock()
    monkeypatch.setattr(moderators, "_render_management_message", render)

    await moderators.handle_moderator_list(callback, session, state, db_user)

    assert state.state == ModeratorReview.management_moderators
    text = render.await_args.args[3]
    assert msg.ROLE_LABEL_ADMIN in text
    assert msg.ROLE_LABEL_MODERATOR in text
    assert msg.ROLE_FROM_ENV_BADGE in text


# ── card: destructive actions hidden for self / config-protected ───

async def test_confirm_revoke_rejects_self_demotion(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202, username="chief")
    db_user.is_admin = True
    db_user.is_moderator = True
    callback = make_callback(from_user_id=202)

    monkeypatch.setattr(moderators, "_guard_management_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(moderators, "get_user_by_id", AsyncMock(return_value=db_user))

    await moderators.handle_moderator_confirm_revoke(
        callback, ModeratorCB(action="confirm_revoke", user_id=2), session, state, db_user,
    )

    callback.answer.assert_awaited_once_with(msg.MODERATOR_SELF_ACTION_FORBIDDEN, show_alert=True)


async def test_card_omits_remove_for_config_protected(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    db_user.is_admin = True
    callback = make_callback(from_user_id=202)

    protected = make_user(user_id=5, telegram_id=1, username="env_mod")  # in MODERATOR_IDS
    protected.is_moderator = True

    monkeypatch.setattr(moderators, "_guard_management_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(moderators, "get_user_by_id", AsyncMock(return_value=protected))
    render = AsyncMock()
    monkeypatch.setattr(moderators, "_render_management_message", render)

    await moderators.handle_moderator_view(
        callback, ModeratorCB(action="view", user_id=5), session, state, db_user,
    )

    keyboard = render.await_args.args[4]
    assert _button_by_text(keyboard, msg.BTN_REMOVE_MODERATOR) is None
    assert _button_by_text(keyboard, msg.BTN_GRANT_ADMIN) is not None


async def test_card_omits_revoke_admin_for_config_protected_admin(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    db_user.is_admin = True
    callback = make_callback(from_user_id=202)

    protected = make_user(user_id=5, telegram_id=1, username="env_admin")
    protected.is_moderator = True
    protected.is_admin = True

    monkeypatch.setattr(moderators, "_guard_management_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(moderators, "get_user_by_id", AsyncMock(return_value=protected))
    render = AsyncMock()
    monkeypatch.setattr(moderators, "_render_management_message", render)
    monkeypatch.setattr(roles.config, "admin_ids", [1])

    await moderators.handle_moderator_view(
        callback, ModeratorCB(action="view", user_id=5), session, state, db_user,
    )

    keyboard = render.await_args.args[4]
    assert _button_by_text(keyboard, msg.BTN_REVOKE_ADMIN) is None
    assert _button_by_text(keyboard, msg.BTN_REMOVE_MODERATOR) is None


async def test_card_hides_self_actions_for_own_card(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202, username="chief")
    db_user.is_admin = True
    db_user.is_moderator = True
    callback = make_callback(from_user_id=202)

    monkeypatch.setattr(moderators, "_guard_management_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(moderators, "get_user_by_id", AsyncMock(return_value=db_user))
    render = AsyncMock()
    monkeypatch.setattr(moderators, "_render_management_message", render)

    await moderators.handle_moderator_view(
        callback, ModeratorCB(action="view", user_id=2), session, state, db_user,
    )

    keyboard = render.await_args.args[4]
    assert _button_by_text(keyboard, msg.BTN_REVOKE_ADMIN) is None
    assert _button_by_text(keyboard, msg.BTN_REMOVE_MODERATOR) is None
    assert _button_by_text(keyboard, msg.BTN_GRANT_ADMIN) is None


# ── a successful role-change callback reaches services/roles.py ────

async def test_grant_admin_callback_reaches_roles_service(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202, username="chief")
    db_user.is_admin = True
    db_user.is_moderator = True
    callback = make_callback(from_user_id=202)
    target = make_user(user_id=5, telegram_id=505, username="bob")
    target.is_moderator = True

    grant = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(moderators, "_guard_management_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(moderators, "get_user_by_id", AsyncMock(return_value=target))
    monkeypatch.setattr(moderators.roles, "grant_admin", grant)
    monkeypatch.setattr(moderators.roles, "notify_role_change", notify)
    monkeypatch.setattr(moderators, "_render_moderator_card", AsyncMock(return_value=True))

    await moderators.handle_moderator_grant_admin(
        callback, ModeratorCB(action="grant_admin", user_id=5), session, state, db_user,
    )

    grant.assert_awaited_once_with(session, callback.bot, actor=db_user, target=target)
    notify.assert_awaited_once_with(
        callback.bot, session, actor=db_user, target=target, action="admin_granted",
    )
    session.commit.assert_awaited_once()
    callback.answer.assert_awaited()


# ── add via telegram_id input ──────────────────────────────────────

async def test_enter_id_callback_renders_prompt(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    db_user.is_admin = True
    callback = make_callback(from_user_id=202)

    monkeypatch.setattr(moderators, "_guard_management_lock", AsyncMock(return_value=True))
    render = AsyncMock()
    monkeypatch.setattr(moderators, "_render_management_message", render)

    await moderators.handle_moderator_enter_id(callback, session, state, db_user)

    assert state.state == ModeratorReview.management_enter_moderator_id
    text = render.await_args.args[3]
    assert text == msg.MODERATOR_ENTER_ID_PROMPT
    callback.answer.assert_awaited()


async def test_enter_id_input_creates_user_and_grants(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    db_user.is_admin = True
    db_user.is_moderator = True
    message = make_message(text="505")

    created = make_user(user_id=9, telegram_id=505, username=None, full_name="(не заходил)")

    grant = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(moderators, "_guard_management_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(moderators, "get_user_by_telegram_id", AsyncMock(return_value=None))
    monkeypatch.setattr(
        moderators, "get_or_create_user", AsyncMock(return_value=(created, True)),
    )
    monkeypatch.setattr(moderators.roles, "grant_moderator", grant)
    monkeypatch.setattr(moderators.roles, "notify_role_change", notify)
    monkeypatch.setattr(moderators, "_render_moderators_list", AsyncMock())

    await moderators.handle_moderator_enter_id_input(message, session, state, db_user)

    grant.assert_awaited_once_with(session, message.bot, actor=db_user, target=created)
    notify.assert_awaited_once_with(
        message.bot, session, actor=db_user, target=created, action="moderator_granted",
    )
    message.delete.assert_awaited_once()


async def test_enter_id_input_rejects_garbage(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    db_user.is_admin = True
    message = make_message(text="abc")
    grant = AsyncMock()
    render = AsyncMock()

    monkeypatch.setattr(moderators, "_guard_management_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(moderators.roles, "grant_moderator", grant)
    monkeypatch.setattr(moderators, "_render_management_message", render)

    await moderators.handle_moderator_enter_id_input(message, session, state, db_user)

    grant.assert_not_awaited()
    text = render.await_args.args[3]
    assert text == msg.MODERATOR_INVALID_ID
    message.delete.assert_awaited_once()


async def test_enter_id_input_rejects_revoked_admin(monkeypatch) -> None:
    """The text handler re-checks is_admin: the lock is not a substitute for
    authorization, so a demoted admin with a live FSM cannot grant a role."""
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    db_user.is_admin = False  # rights revoked while FSM/lock were still alive
    message = make_message(text="505")
    grant = AsyncMock()
    lock_guard = AsyncMock()

    monkeypatch.setattr(moderators, "_guard_management_lock", lock_guard)
    monkeypatch.setattr(moderators.roles, "grant_moderator", grant)
    monkeypatch.setattr(moderators, "_render_moderators_list", AsyncMock())

    await moderators.handle_moderator_enter_id_input(message, session, state, db_user)

    grant.assert_not_awaited()
    lock_guard.assert_not_awaited()
    message.answer.assert_awaited_once_with(msg.MODERATORS_ADMIN_ONLY)


# ── invite link ────────────────────────────────────────────────────

async def test_invite_callback_shows_link(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    db_user.is_admin = True
    callback = make_callback(from_user_id=202)

    invite = SimpleNamespace(token="tok123")
    monkeypatch.setattr(moderators, "_guard_management_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(
        moderators.moderator_invites, "create_invite", AsyncMock(return_value=invite),
    )
    render = AsyncMock()
    monkeypatch.setattr(moderators, "_render_management_message", render)

    await moderators.handle_moderator_invite(callback, session, state, db_user)

    text = render.await_args.args[3]
    assert "https://t.me/testbot?start=modinvite_tok123" in text
    assert "24 ч" in text
    # The invite row is committed before the link is shown to the user.
    session.commit.assert_awaited_once()


def test_role_error_text_maps_banned_target() -> None:
    from core.exceptions import RoleTargetBannedError

    target = make_user(user_id=5, telegram_id=505, username="bob")
    text = moderators._role_error_text(RoleTargetBannedError(), target)
    assert text == msg.MODERATOR_TARGET_BANNED.format(user="@bob")


# ── cancellation releases the moderators lock ──────────────────────

async def test_cancel_management_releases_moderators_lock(monkeypatch) -> None:
    from handlers import moderator as mod_pkg

    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    message = make_message()
    release = AsyncMock()

    monkeypatch.setattr(mod_pkg.edit_lock, "release_lock", release)
    monkeypatch.setattr(mod_pkg, "show_moderator_home", AsyncMock())

    await mod_pkg._cancel_management(message, session, state, db_user)

    calls = [call.args[1:] for call in release.call_args_list]
    assert ("management", "moderators", 202) in calls
    assert ("management", "presets", 202) in calls
    assert ("management", "banned", 202) in calls


# ── new FSM states route /cancel to the management path ────────────

def test_moderator_states_are_management_category() -> None:
    for state in (
        ModeratorReview.management_moderators,
        ModeratorReview.management_moderator_detail,
        ModeratorReview.management_enter_moderator_id,
    ):
        assert STATE_CATEGORY[state.state] == "management"
