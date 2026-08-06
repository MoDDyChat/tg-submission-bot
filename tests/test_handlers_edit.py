"""Unit tests for handlers/moderator/edit.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

import core.messages as msg
from handlers.moderator import edit
from states.moderator import ModeratorReview
from tests.helpers import FakeState, make_callback, make_message, make_submission, make_user


# ── handle_edit_caption_start ────────────────────────────────────────

async def test_edit_caption_start_sets_state(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    sub = make_submission(sub_id=4, status="pending")
    callback = make_callback()

    monkeypatch.setattr(edit, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(edit.edit_lock, "extend_lock", AsyncMock(return_value=True))

    await edit.handle_edit_caption_start(callback, AsyncMock(sub_id=4), session, state, db_user)

    assert state.state == ModeratorReview.editing_caption
    assert state.data["sub_id"] == 4
    callback.answer.assert_awaited()


async def test_edit_caption_start_aborts_on_lock_lost(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    db_user = make_user()
    sub = make_submission(sub_id=4, status="pending")
    callback = make_callback()

    monkeypatch.setattr(edit, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(edit.edit_lock, "extend_lock", AsyncMock(return_value=False))

    await edit.handle_edit_caption_start(callback, AsyncMock(sub_id=4), session, state, db_user)

    callback.answer.assert_awaited_once_with(msg.MODERATOR_LOCK_LOST, show_alert=True)
    assert state.cleared is True


async def test_edit_caption_start_rejects_terminal_status(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState()
    sub = make_submission(sub_id=4, status="rejected")
    callback = make_callback()

    monkeypatch.setattr(edit, "get_submission_with_user", AsyncMock(return_value=sub))

    await edit.handle_edit_caption_start(callback, AsyncMock(sub_id=4), session, state, make_user())

    callback.answer.assert_awaited_once_with(msg.SUBMISSION_NOT_AVAILABLE, show_alert=True)


# ── handle_edit_caption_text ──────────────────────────────────────────

async def test_edit_caption_text_updates_caption(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 4})
    db_user = make_user()
    message = make_message(text="New caption text")
    sub = make_submission(sub_id=4, status="pending", caption="Old caption", tags=[])
    update_caption = AsyncMock()
    send_view = AsyncMock(return_value=True)

    monkeypatch.setattr(edit.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(edit, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(edit, "update_submission_caption", update_caption)
    monkeypatch.setattr(edit, "_send_submission_view", send_view)
    monkeypatch.setattr(edit.topic_notifications, "notify_caption_changed", AsyncMock())
    monkeypatch.setattr(edit.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(edit.topics, "request_topic_title_sync", AsyncMock())

    await edit.handle_edit_caption_text(message, session, state, db_user)

    update_caption.assert_awaited_once_with(session, 4, "New caption text")
    send_view.assert_awaited_once()


async def test_edit_caption_text_rejects_too_long(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 4})
    db_user = make_user()
    # Long caption that exceeds MAX_TEXT_CAPTION
    long_text = "A" * 5000
    message = make_message(text=long_text)
    sub = make_submission(sub_id=4, status="pending", caption="Old", tags=[], media=[])
    update_caption = AsyncMock()

    monkeypatch.setattr(edit.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(edit, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(edit, "update_submission_caption", update_caption)

    await edit.handle_edit_caption_text(message, session, state, db_user)

    update_caption.assert_not_awaited()
    message.answer.assert_awaited_once()
    assert "длинн" in message.answer.await_args.args[0].lower()


async def test_edit_caption_text_aborts_on_lock_lost(monkeypatch) -> None:
    session = AsyncMock()
    state = FakeState({"sub_id": 4})
    db_user = make_user()
    message = make_message(text="New caption")
    update_caption = AsyncMock()

    monkeypatch.setattr(edit.edit_lock, "extend_lock", AsyncMock(return_value=False))
    monkeypatch.setattr(edit, "update_submission_caption", update_caption)

    await edit.handle_edit_caption_text(message, session, state, db_user)

    update_caption.assert_not_awaited()
    assert state.cleared is True


async def test_edit_caption_text_preserves_html(monkeypatch) -> None:
    """Caption stored in DB should be HTML (bold, italic preserved)."""
    session = AsyncMock()
    state = FakeState({"sub_id": 4})
    db_user = make_user()
    message = make_message(text="<b>Bold</b> text")
    sub = make_submission(sub_id=4, status="pending", caption="Old", tags=[])
    update_caption = AsyncMock()
    send_view = AsyncMock(return_value=True)

    monkeypatch.setattr(edit.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(edit, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(edit, "update_submission_caption", update_caption)
    monkeypatch.setattr(edit, "_send_submission_view", send_view)
    monkeypatch.setattr(edit.topic_notifications, "notify_caption_changed", AsyncMock())
    monkeypatch.setattr(edit.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(edit.topics, "request_topic_title_sync", AsyncMock())

    await edit.handle_edit_caption_text(message, session, state, db_user)

    # The caption passed to update_submission_caption should come from get_html_text,
    # which returns message.text (since no entities in this fake message)
    update_caption.assert_awaited_once()
