"""Unit tests for handlers/moderator/media.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import core.messages as msg
from handlers.moderator import media
from services.media_append import AppendResult
from states.moderator import ModeratorReview
from tests.helpers import FakeState, make_callback, make_media, make_message, make_sent_message, make_submission, make_user


def _two_media() -> list:
    return [make_media(media_id=1, submission_id=5), make_media(media_id=2, submission_id=5)]


# ── handle_edit_media_open ──────────────────────────────────────────


async def test_edit_media_open_sets_state(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    sub = make_submission(sub_id=5, status="pending", media=_two_media())
    callback = make_callback()

    monkeypatch.setattr(media, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(media.media_append, "clear_append_cancel", MagicMock())

    await media.handle_edit_media_open(callback, AsyncMock(sub_id=5), session, state, db_user)

    assert state.state == ModeratorReview.editing_media
    assert state.data.get("sub_id") == 5
    assert state.data.get("media_sig_open") == [1, 2]
    assert state.data.get("media_manager_message_id") is not None
    callback.message.answer.assert_awaited_once()
    callback.answer.assert_awaited()


async def test_edit_media_open_double_click_guard(monkeypatch) -> None:
    session = AsyncMock()
    db_user = make_user()
    sub = make_submission(sub_id=5, status="pending", media=_two_media())
    callback = make_callback()
    callback.message.answer.side_effect = [
        make_sent_message(1001),
        make_sent_message(1002),
    ]

    monkeypatch.setattr(media, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(media.media_append, "clear_append_cancel", MagicMock())

    # First call
    state = FakeState()
    await media.handle_edit_media_open(callback, AsyncMock(sub_id=5), session, state, db_user)
    first_manager_id = state.data["media_manager_message_id"]

    # Second call (state already editing_media)
    callback.message.answer.side_effect = [
        make_sent_message(2001),
        make_sent_message(2002),
    ]
    monkeypatch.setattr(media, "get_submission_with_user", AsyncMock(return_value=sub))
    await media.handle_edit_media_open(callback, AsyncMock(sub_id=5), session, state, db_user)
    second_manager_id = state.data["media_manager_message_id"]

    assert first_manager_id != second_manager_id
    callback.bot.delete_message.assert_awaited_with(callback.message.chat.id, first_manager_id)


async def test_edit_media_open_terminal_status(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    sub = make_submission(sub_id=5, status="published")
    callback = make_callback()

    monkeypatch.setattr(media, "get_submission_with_user", AsyncMock(return_value=sub))

    await media.handle_edit_media_open(callback, AsyncMock(sub_id=5), session, state, make_user())

    callback.answer.assert_awaited_once_with(msg.SUBMISSION_NOT_AVAILABLE, show_alert=True)


async def test_edit_media_open_lock_lost(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    sub = make_submission(sub_id=5, status="pending")
    callback = make_callback()

    monkeypatch.setattr(media, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=False))

    await media.handle_edit_media_open(callback, AsyncMock(sub_id=5), session, state, db_user)

    callback.answer.assert_awaited_once_with(msg.MODERATOR_LOCK_LOST, show_alert=True)
    assert state.cleared is True


# ── handle_media_delete ──────────────────────────────────────────────


async def test_media_delete_last_forbidden(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    single_media = [make_media(media_id=1, submission_id=5)]
    callback = make_callback()
    callback_data = AsyncMock(sub_id=5, media_id=1)

    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(media, "get_submission_media", AsyncMock(return_value=single_media))
    delete_mock = AsyncMock()
    monkeypatch.setattr(media, "delete_media_unless_last", delete_mock)

    await media.handle_media_delete(callback, callback_data, session, state, db_user)

    callback.answer.assert_awaited_once_with(msg.MEDIA_DELETE_LAST_FORBIDDEN, show_alert=True)
    delete_mock.assert_not_awaited()


async def test_media_delete_atomic_guard_false(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    two = _two_media()
    callback = make_callback()
    callback_data = AsyncMock(sub_id=5, media_id=1)

    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(media, "get_submission_media", AsyncMock(return_value=two))
    monkeypatch.setattr(media, "delete_media_unless_last", AsyncMock(return_value=False))

    await media.handle_media_delete(callback, callback_data, session, state, db_user)

    callback.message.edit_text.assert_not_awaited()
    session.commit.assert_not_awaited()
    callback.answer.assert_awaited_once_with(msg.MEDIA_DELETE_LAST_FORBIDDEN, show_alert=True)


async def test_media_delete_ok(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    two = _two_media()
    callback = make_callback()
    callback_data = AsyncMock(sub_id=5, media_id=1)

    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(media, "get_submission_media", AsyncMock(return_value=two))
    delete_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(media, "delete_media_unless_last", delete_mock)

    await media.handle_media_delete(callback, callback_data, session, state, db_user)

    delete_mock.assert_awaited_once_with(session, 1, 5)
    session.commit.assert_awaited_once()
    callback.message.edit_text.assert_awaited_once()
    callback.answer.assert_awaited_once_with(msg.MEDIA_DELETED)


# ── handle_media_add_start ──────────────────────────────────────────


async def test_media_add_start_transitions_state(monkeypatch) -> None:
    session = AsyncMock()
    db_user = make_user()
    callback = make_callback()
    callback_data = AsyncMock(sub_id=5)
    state = FakeState({"media_added_message_ids": [101]})

    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))

    await media.handle_media_add_start(callback, callback_data, session, state, db_user)

    assert state.state == ModeratorReview.adding_media
    assert state.data["sub_id"] == 5
    assert state.data.get("media_added_message_ids") == [101]
    callback.message.answer.assert_awaited_once_with(msg.MEDIA_ADD_PROMPT)
    callback.answer.assert_awaited()


# ── handle_adding_media_group ───────────────────────────────────────


async def test_adding_media_group_calls_buffer(monkeypatch) -> None:
    state = FakeState({"sub_id": 5})
    db_user = make_user()
    message = make_message(media_group_id="grp1", photo=AsyncMock())
    buffer_mock = AsyncMock()
    monkeypatch.setattr(media.media_append, "buffer_append_media_group", buffer_mock)

    await media.handle_adding_media_group(message, state, db_user)

    buffer_mock.assert_awaited_once_with(message, 5, db_user.id)


# ── handle_adding_single_media ──────────────────────────────────────


async def test_adding_single_media_ok(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 5})
    state.state = ModeratorReview.adding_media.state
    db_user = make_user()
    sub = make_submission(sub_id=5, status="pending", media=_two_media())
    message = make_message(photo=AsyncMock())

    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(media, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(media, "extract_media_info", lambda m: ("fid", "fuid", "photo"))
    monkeypatch.setattr(media.media_append, "append_media_to_submission", AsyncMock(return_value=(AppendResult.OK, 1)))

    await media.handle_adding_single_media(message, session, state, db_user)

    message.answer.assert_awaited()
    assert "Медиа добавлено" in message.answer.await_args.args[0]
    # state remains adding_media
    assert state.state == ModeratorReview.adding_media


async def test_adding_single_media_invalid_composition(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 5})
    db_user = make_user()
    sub = make_submission(sub_id=5, status="pending", media=_two_media())
    message = make_message(photo=AsyncMock())

    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(media, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(media, "extract_media_info", lambda m: ("fid", "fuid", "photo"))
    monkeypatch.setattr(media.media_append, "append_media_to_submission", AsyncMock(return_value=(AppendResult.INVALID_COMPOSITION, 0)))

    await media.handle_adding_single_media(message, session, state, db_user)

    message.answer.assert_awaited_once_with(msg.MEDIA_COMPOSITION_INVALID)


# ── handle_adding_media_unexpected ──────────────────────────────────


async def test_adding_media_text_fallback() -> None:
    message = make_message(text="hello")
    await media.handle_adding_media_unexpected(message)
    message.answer.assert_awaited_once_with(msg.MEDIA_ADDING_EXPECT_MEDIA)


# ── handle_media_done ────────────────────────────────────────────────


async def test_media_done_waits_for_pending(monkeypatch) -> None:
    session = AsyncMock()
    db_user = make_user()
    callback = make_callback()
    callback_data = AsyncMock(sub_id=5)
    state = FakeState({"media_sig_open": [1, 2], "sub_id": 5})
    sub = make_submission(sub_id=5, status="pending", media=_two_media())

    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(media, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(media.media_append, "wait_for_pending_append", AsyncMock())
    monkeypatch.setattr(media, "render_submission_view", AsyncMock(return_value=True))

    await media.handle_media_done(callback, callback_data, session, state, db_user)

    media.media_append.wait_for_pending_append.assert_awaited_once_with(5)


async def test_media_done_with_change(monkeypatch) -> None:
    session = AsyncMock()
    db_user = make_user()
    callback = make_callback()
    callback_data = AsyncMock(sub_id=5)
    state = FakeState({"media_sig_open": [1], "sub_id": 5})
    sub = make_submission(sub_id=5, status="pending", media=_two_media())

    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(media, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(media.media_append, "wait_for_pending_append", AsyncMock())
    monkeypatch.setattr(
        media.topics, "repost_submission_card", AsyncMock(return_value=([21, 22], 23))
    )
    monkeypatch.setattr(media.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(media.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(media.topic_notifications, "notify_media_changed", AsyncMock())
    monkeypatch.setattr(media, "render_submission_view", AsyncMock(return_value=True))

    await media.handle_media_done(callback, callback_data, session, state, db_user)

    media.topics.repost_submission_card.assert_awaited_once_with(callback.bot, session, sub)
    assert session.commit.await_count == 2
    media.topics.update_submission_card.assert_awaited_once_with(callback.bot, session, sub)
    media.topics.request_topic_title_sync.assert_awaited_once_with(
        session, sub.user.id
    )
    media.render_submission_view.assert_awaited_once_with(callback.message, session, 5, state)
    media.topic_notifications.notify_media_changed.assert_awaited_once_with(callback.bot, session, sub, db_user)


async def test_media_done_deletes_replacement_when_commit_fails(monkeypatch) -> None:
    """Коммит ID замены упал → живой блок снимаем, иначе recover пришлёт дубль."""
    session = AsyncMock()
    session.commit.side_effect = [None, RuntimeError("lock timeout")]
    db_user = make_user()
    callback = make_callback()
    callback_data = AsyncMock(sub_id=5)
    state = FakeState({"media_sig_open": [1], "sub_id": 5})
    sub = make_submission(sub_id=5, status="pending", media=_two_media())

    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(media, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(media.media_append, "wait_for_pending_append", AsyncMock())
    monkeypatch.setattr(
        media.topics, "repost_submission_card", AsyncMock(return_value=([31, 32], 33))
    )
    monkeypatch.setattr(media.topics, "get_submission", AsyncMock(return_value=None))
    monkeypatch.setattr(media.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(media.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(media.topic_notifications, "notify_media_changed", AsyncMock())
    monkeypatch.setattr(media, "render_submission_view", AsyncMock(return_value=True))

    await media.handle_media_done(callback, callback_data, session, state, db_user)

    assert [call.args[1] for call in callback.bot.delete_message.await_args_list] == [31, 32, 33]
    media.topics.update_submission_card.assert_not_awaited()


async def test_media_done_repost_failure_rollback(monkeypatch) -> None:
    session = AsyncMock()
    db_user = make_user()
    callback = make_callback()
    callback_data = AsyncMock(sub_id=5)
    state = FakeState({"media_sig_open": [1], "sub_id": 5})
    sub = make_submission(sub_id=5, status="pending", media=_two_media())

    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(media, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(media.media_append, "wait_for_pending_append", AsyncMock())
    monkeypatch.setattr(media.topics, "repost_submission_card", AsyncMock(side_effect=ValueError("fail")))
    monkeypatch.setattr(media.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(media.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(media.topic_notifications, "notify_media_changed", AsyncMock())
    monkeypatch.setattr(media, "render_submission_view", AsyncMock(return_value=True))

    await media.handle_media_done(callback, callback_data, session, state, db_user)

    session.rollback.assert_awaited_once()
    session.commit.assert_awaited_once()


async def test_media_done_without_change(monkeypatch) -> None:
    session = AsyncMock()
    db_user = make_user()
    callback = make_callback()
    callback_data = AsyncMock(sub_id=5)
    two = _two_media()
    state = FakeState({"media_sig_open": [1, 2], "sub_id": 5})
    sub = make_submission(sub_id=5, status="pending", media=two)

    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(media, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(media.media_append, "wait_for_pending_append", AsyncMock())
    monkeypatch.setattr(media.topics, "repost_submission_card", AsyncMock())
    monkeypatch.setattr(media.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(media.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(media.topic_notifications, "notify_media_changed", AsyncMock())
    monkeypatch.setattr(media, "render_submission_view", AsyncMock(return_value=True))

    await media.handle_media_done(callback, callback_data, session, state, db_user)

    media.topics.repost_submission_card.assert_not_awaited()
    media.render_submission_view.assert_awaited_once_with(callback.message, session, 5, state)


async def test_media_done_deletes_prompt_message(monkeypatch) -> None:
    session = AsyncMock()
    db_user = make_user()
    callback = make_callback()
    callback_data = AsyncMock(sub_id=5)
    state = FakeState({"media_sig_open": [1, 2], "sub_id": 5, "prompt_message_id": 42})
    sub = make_submission(sub_id=5, status="pending", media=_two_media())

    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(media, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(media.media_append, "wait_for_pending_append", AsyncMock())
    monkeypatch.setattr(media.topics, "repost_submission_card", AsyncMock())
    monkeypatch.setattr(media.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(media.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(media.topic_notifications, "notify_media_changed", AsyncMock())
    monkeypatch.setattr(media, "render_submission_view", AsyncMock(return_value=True))

    await media.handle_media_done(callback, callback_data, session, state, db_user)

    callback.bot.delete_message.assert_awaited_once_with(callback.message.chat.id, 42)
    assert state.data.get("prompt_message_id") is None


async def test_media_done_stale_callback(monkeypatch) -> None:
    session = AsyncMock()
    db_user = make_user()
    callback = make_callback()
    callback_data = AsyncMock(sub_id=5)
    state = FakeState({"sub_id": 5})

    monkeypatch.setattr(media.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(media, "render_submission_view", AsyncMock())
    repost_mock = AsyncMock()
    monkeypatch.setattr(media.topics, "repost_submission_card", repost_mock)

    await media.handle_media_done(callback, callback_data, session, state, db_user)

    media.render_submission_view.assert_not_awaited()
    repost_mock.assert_not_awaited()
    callback.answer.assert_awaited_once()
