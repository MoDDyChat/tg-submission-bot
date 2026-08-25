"""Unit tests for handlers/moderator/management.py."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import core.messages as msg
from aiogram.types import CallbackQuery, Chat, Message, User as TgUser
from handlers.moderator import management
from keyboards.callbacks import ManagementCB, UnbanCB
from tests.helpers import FakeSessionFactory, FakeState, make_bot, make_callback, make_user


# ── handle_presets_menu — lock acquisition ────────────────────────────

async def test_presets_menu_acquires_lock_and_renders(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    db_user.is_admin = False
    callback = make_callback(from_user_id=202)
    render = AsyncMock()

    monkeypatch.setattr(management.edit_lock, "acquire_lock", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(management, "_render_preset_sections", render)

    await management.handle_presets_menu(callback, session, state, db_user)

    render.assert_awaited_once()
    callback.answer.assert_awaited()


async def test_presets_menu_shows_alert_when_locked(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    callback = make_callback()

    owner = make_user(user_id=3, telegram_id=303, username="other_mod")
    monkeypatch.setattr(management.edit_lock, "acquire_lock", AsyncMock(return_value=(False, 303)))
    monkeypatch.setattr(management, "get_user_by_telegram_id", AsyncMock(return_value=owner))

    await management.handle_presets_menu(callback, session, state, db_user)

    callback.answer.assert_awaited_once()
    call = callback.answer.await_args
    assert call.kwargs.get("show_alert") is True


# ── handle_banned_users — lock acquisition ────────────────────────────

async def test_banned_users_acquires_lock_and_renders(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    callback = make_callback()
    render = AsyncMock()

    monkeypatch.setattr(management.edit_lock, "acquire_lock", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(management, "_render_banned_users", render)

    await management.handle_banned_users(callback, session, state, db_user)

    render.assert_awaited_once()
    callback.answer.assert_awaited()


# ── handle_recover — is_admin guard ──────────────────────────────────

async def test_handle_recover_rejected_for_non_admin(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    db_user.is_admin = False
    callback = make_callback()

    await management.handle_recover(callback, session, state, db_user)

    callback.answer.assert_awaited_once_with(msg.RECOVER_ADMIN_ONLY, show_alert=True)


async def test_handle_recover_runs_for_admin(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    db_user.is_admin = True
    callback = make_callback()

    monkeypatch.setattr(management, "get_active_submissions", AsyncMock(return_value=[]))
    monkeypatch.setattr(management, "recover_missing_posts", AsyncMock(return_value=(0, 0)))
    monkeypatch.setattr(management, "_render_management_message", AsyncMock())
    monkeypatch.setattr(
        management, "session_factory",
        FakeSessionFactory(AsyncMock(), AsyncMock()),
    )
    monkeypatch.setattr(management.admin_notifications, "notify_admins", AsyncMock())

    await management.handle_recover(callback, session, state, db_user)
    task = management._recover_task
    assert task is not None
    await task
    await asyncio.sleep(0)

    management.recover_missing_posts.assert_awaited_once()


async def test_handle_recover_recover_called_once_and_notifies_admins_once(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    db_user.is_admin = True
    callback = make_callback()

    notify = AsyncMock()
    count_session = AsyncMock()
    notify_session = AsyncMock()
    monkeypatch.setattr(management, "get_active_submissions", AsyncMock(return_value=[]))
    monkeypatch.setattr(management, "recover_missing_posts", AsyncMock(return_value=(2, 1)))
    monkeypatch.setattr(management, "_render_management_message", AsyncMock())
    monkeypatch.setattr(
        management, "session_factory",
        FakeSessionFactory(count_session, notify_session),
    )
    monkeypatch.setattr(management.admin_notifications, "notify_admins", notify)

    await management.handle_recover(callback, session, state, db_user)
    task = management._recover_task
    assert task is not None
    await task
    await asyncio.sleep(0)

    management.recover_missing_posts.assert_awaited_once()
    notify.assert_awaited_once()
    call = notify.await_args
    assert call.args[1] is not session
    assert call.kwargs["actor"] is db_user
    assert call.kwargs["action_text"] == msg.ADMIN_NOTIFY_RECOVER_USED.format(
        actor=management.admin_notifications.actor_display(db_user)
    )


async def test_handle_recover_second_call_rejected_while_running(monkeypatch) -> None:
    """Two simultaneous Recover attempts must not run concurrently."""
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    db_user.is_admin = True
    callback1 = make_callback()
    callback2 = make_callback()

    calls = 0

    async def slow_recover(bot, session_factory):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return (1, 1)

    monkeypatch.setattr(management, "get_active_submissions", AsyncMock(return_value=[AsyncMock()]))
    monkeypatch.setattr(management, "recover_missing_posts", slow_recover)
    monkeypatch.setattr(management, "_render_management_message", AsyncMock())
    monkeypatch.setattr(
        management, "session_factory",
        FakeSessionFactory(AsyncMock(), AsyncMock()),
    )
    monkeypatch.setattr(management.admin_notifications, "notify_admins", AsyncMock())

    await management.handle_recover(callback1, session, state, db_user)
    await management.handle_recover(callback2, session, state, db_user)

    callback2.answer.assert_awaited_once_with(msg.RECOVER_ALREADY_RUNNING, show_alert=True)
    task = management._recover_task
    assert task is not None
    await task
    await asyncio.sleep(0)

    assert calls == 1
    assert management._recover_task is None


async def test_handle_recover_second_call_allowed_after_finish(monkeypatch) -> None:
    """A finished Recover frees the slot for the next run."""
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    db_user.is_admin = True
    callback1 = make_callback()
    callback2 = make_callback()

    monkeypatch.setattr(management, "get_active_submissions", AsyncMock(return_value=[]))
    monkeypatch.setattr(management, "recover_missing_posts", AsyncMock(return_value=(0, 0)))
    monkeypatch.setattr(management, "_render_management_message", AsyncMock())
    monkeypatch.setattr(
        management, "session_factory",
        FakeSessionFactory(AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()),
    )
    monkeypatch.setattr(management.admin_notifications, "notify_admins", AsyncMock())

    await management.handle_recover(callback1, session, state, db_user)
    task = management._recover_task
    assert task is not None
    await task
    await asyncio.sleep(0)
    assert management._recover_task is None

    await management.handle_recover(callback2, session, state, db_user)
    task2 = management._recover_task
    assert task2 is not None
    await task2
    await asyncio.sleep(0)

    assert management.recover_missing_posts.await_count == 2


async def test_handle_recover_dispatched_through_router(monkeypatch) -> None:
    """The Recover button callback must reach the handler via the router."""
    bot = make_bot()
    db_user = make_user()
    db_user.is_admin = True
    state = FakeState()

    callback = CallbackQuery(
        id="42",
        from_user=TgUser(id=202, is_bot=False, first_name="Admin"),
        chat_instance="abc",
        message=Message(
            message_id=7,
            date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            chat=Chat(id=10, type="private"),
            from_user=TgUser(id=202, is_bot=False, first_name="Admin"),
            text="x",
        ),
        data=ManagementCB(action="recover").pack(),
    ).as_(bot)

    monkeypatch.setattr(management, "get_active_submissions", AsyncMock(return_value=[]))
    monkeypatch.setattr(management, "recover_missing_posts", AsyncMock(return_value=(0, 0)))
    monkeypatch.setattr(management, "_render_management_message", AsyncMock())
    monkeypatch.setattr(
        management, "session_factory",
        FakeSessionFactory(AsyncMock(), AsyncMock()),
    )
    monkeypatch.setattr(management.admin_notifications, "notify_admins", AsyncMock())

    # Dispatch through the router's callback_query observer — a missing
    # @router.callback_query decorator would leave _recover_task unset.
    await management.router.callback_query.trigger(
        callback, session=AsyncMock(), state=state, db_user=db_user,
    )

    task = management._recover_task
    assert task is not None
    await task
    await asyncio.sleep(0)

    management.recover_missing_posts.assert_awaited_once()


async def test_handle_recover_concurrent_calls_start_single_task(monkeypatch) -> None:
    """Simultaneous callbacks must not both claim the free Recover slot."""
    session = AsyncMock()
    state1 = FakeState()
    state2 = FakeState()
    db_user = make_user()
    db_user.is_admin = True
    cb1 = make_callback()
    cb2 = make_callback()

    recover_calls = 0

    async def slow_recover(bot, session_factory):
        nonlocal recover_calls
        recover_calls += 1
        await asyncio.sleep(0)
        return (0, 0)

    monkeypatch.setattr(management, "get_active_submissions", AsyncMock(return_value=[]))
    monkeypatch.setattr(management, "recover_missing_posts", slow_recover)
    monkeypatch.setattr(management, "_render_management_message", AsyncMock())
    monkeypatch.setattr(
        management, "session_factory",
        FakeSessionFactory(AsyncMock(), AsyncMock()),
    )
    notify = AsyncMock()
    monkeypatch.setattr(management.admin_notifications, "notify_admins", notify)

    # Barrier: hold the first callback inside the slot check so the second one
    # really races for the free slot — sequential awaits would not model it.
    entered = asyncio.Event()
    release = asyncio.Event()
    first_answer = True

    async def gated_answer(*args, **kwargs):
        nonlocal first_answer
        if first_answer:
            first_answer = False
            entered.set()
            await release.wait()
        return None

    cb1.answer = AsyncMock(side_effect=gated_answer)
    cb2.answer = AsyncMock(side_effect=gated_answer)

    gather_task = asyncio.gather(
        management.handle_recover(cb1, session, state1, db_user),
        management.handle_recover(cb2, session, state2, db_user),
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    release.set()
    await gather_task

    cb2.answer.assert_awaited_once_with(msg.RECOVER_ALREADY_RUNNING, show_alert=True)
    task = management._recover_task
    assert task is not None
    await task
    await asyncio.sleep(0)

    assert recover_calls == 1
    notify.assert_awaited_once()
    assert management._recover_task is None


# ── handle_unban_select ───────────────────────────────────────────────

async def test_handle_unban_select_already_unbanned_skips_side_effects(monkeypatch) -> None:
    """unban_user → False: alert shown, no admin notification, no re-render."""
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    callback = make_callback()
    callback_data = UnbanCB(user_id=7)
    render = AsyncMock()

    monkeypatch.setattr(management, "_guard_management_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(management, "get_user_by_id", AsyncMock(return_value=None))
    monkeypatch.setattr(management, "unban_user", AsyncMock(return_value=False))
    monkeypatch.setattr(management.admin_notifications, "notify_admins", AsyncMock())
    monkeypatch.setattr(management, "_render_banned_users", render)

    await management.handle_unban_select(callback, callback_data, session, state, db_user)

    callback.answer.assert_awaited_once_with(msg.USER_ALREADY_UNBANNED, show_alert=True)
    management.admin_notifications.notify_admins.assert_not_awaited()
    render.assert_not_awaited()


# ── handle_home / handle_close_management — releases all management locks ─

async def test_handle_home_releases_management_locks(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    callback = make_callback()
    release = AsyncMock()

    monkeypatch.setattr(management.edit_lock, "release_lock", release)
    monkeypatch.setattr(management, "_render_home", AsyncMock())

    await management.handle_home(callback, session, state, db_user)

    assert release.await_count == 3  # presets + banned + moderators
    calls = [call.args[1:] for call in release.call_args_list]
    assert ("management", "moderators", 202) in calls
    callback.answer.assert_awaited()


async def test_handle_close_management_releases_moderators_lock(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user(user_id=2, telegram_id=202)
    callback = make_callback()
    release = AsyncMock()

    monkeypatch.setattr(management.edit_lock, "release_lock", release)

    await management.handle_close_management(callback, session, state, db_user)

    assert release.await_count == 3  # presets + banned + moderators
    calls = [call.args[1:] for call in release.call_args_list]
    assert ("management", "moderators", 202) in calls
    assert state.cleared is True
    callback.answer.assert_awaited()
