"""Unit tests for the moderator submit mode."""

from __future__ import annotations

from unittest.mock import AsyncMock

import core.messages as msg
from handlers.moderator import management, submit
from services import submission_intake
from states.moderator import ModeratorReview
from tests.helpers import (
    FakeState,
    make_callback,
    make_message,
    make_photo_sizes,
    make_submission,
    make_user,
)


# ── Entering submit mode ──────────────────────────────────────────────

async def test_enter_submit_mode_sets_state_and_shows_prompt(monkeypatch) -> None:
    state = FakeState()
    callback = make_callback(message=make_message())
    render = AsyncMock()
    monkeypatch.setattr(management, "_render_management_message", render)

    await management.handle_enter_submit_mode(callback, state)

    assert state.state == ModeratorReview.submitting_post
    render.assert_awaited_once()
    call_args = render.await_args
    assert call_args.args[3] == msg.MODERATOR_SUBMIT_PROMPT
    callback.answer.assert_awaited_once()


# ── Submitting single media in submit mode ───────────────────────────

async def test_moderator_submit_single_media_creates_submission(monkeypatch) -> None:
    session = AsyncMock()
    db_user = make_user(user_id=5, telegram_id=500)
    message = make_message(photo=make_photo_sizes(("f1", "u1")))
    sub = make_submission(sub_id=11, user=db_user)

    monkeypatch.setattr(submission_intake, "create_submission", AsyncMock(return_value=sub))
    add_media_mock = AsyncMock()
    monkeypatch.setattr(submission_intake, "add_media", add_media_mock)
    monkeypatch.setattr(submission_intake, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(submission_intake.topics, "ensure_user_topic", AsyncMock(return_value=42))
    monkeypatch.setattr(submission_intake.topics, "post_submission_card", AsyncMock(return_value=([], 1)))
    monkeypatch.setattr(submission_intake.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(submission_intake, "_render_queue", AsyncMock())

    await submit.handle_moderator_single_media(message, session, db_user)

    submission_intake.create_submission.assert_awaited_once()
    call_kwargs = submission_intake.create_submission.await_args.kwargs
    assert call_kwargs["user_id"] == db_user.id
    add_media_mock.assert_awaited_once()
    message.answer.assert_awaited_once()
    assert msg.SUBMISSION_ACCEPTED.format(sub_id=11) in message.answer.await_args.args[0]


# ── Submitting text in submit mode ───────────────────────────────────

async def test_moderator_submit_text_creates_submission(monkeypatch) -> None:
    session = AsyncMock()
    db_user = make_user(user_id=5, telegram_id=500)
    message = make_message(text="Привет, это мой арт")
    sub = make_submission(sub_id=12, user=db_user, caption="Привет, это мой арт")

    monkeypatch.setattr(submission_intake, "get_html_text", lambda m: "Привет, это мой арт")
    monkeypatch.setattr(submission_intake, "create_submission", AsyncMock(return_value=sub))
    monkeypatch.setattr(submission_intake, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(submission_intake.topics, "ensure_user_topic", AsyncMock(return_value=42))
    monkeypatch.setattr(submission_intake.topics, "post_submission_card", AsyncMock(return_value=([], 1)))
    monkeypatch.setattr(submission_intake.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(submission_intake, "_render_queue", AsyncMock())

    await submit.handle_moderator_text(message, session, db_user)

    submission_intake.create_submission.assert_awaited_once()
    call_kwargs = submission_intake.create_submission.await_args.kwargs
    assert call_kwargs["user_id"] == db_user.id
    message.answer.assert_awaited_once()
    assert msg.SUBMISSION_ACCEPTED.format(sub_id=12) in message.answer.await_args.args[0]


# ── State is preserved after submission ──────────────────────────────

async def test_submit_mode_state_not_cleared_after_single_media(monkeypatch) -> None:
    """State remains submitting_post after receiving a submission — mode stays active."""
    session = AsyncMock()
    db_user = make_user()
    message = make_message(photo=make_photo_sizes(("f1", "u1")))
    sub = make_submission(sub_id=7, user=db_user)

    monkeypatch.setattr(submission_intake, "create_submission", AsyncMock(return_value=sub))
    monkeypatch.setattr(submission_intake, "add_media", AsyncMock())
    monkeypatch.setattr(submission_intake, "get_submission_with_user", AsyncMock(return_value=None))
    monkeypatch.setattr(submission_intake, "_render_queue", AsyncMock())

    state = FakeState({"sub_id": None})
    await state.set_state(ModeratorReview.submitting_post)

    await submit.handle_moderator_single_media(message, session, db_user)

    assert state.state == ModeratorReview.submitting_post
    assert not state.cleared
