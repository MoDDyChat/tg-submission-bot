from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock


import core.messages as msg
from handlers import viewer
from services import submission_intake
from tests.helpers import (
    FakeSessionFactory,
    make_callback,
    make_media,
    make_message,
    make_photo_sizes,
    make_submission,
    make_user,
)


async def _no_sleep(_: float) -> None:
    return None


async def test_finalize_media_group_sorts_messages_and_warns_about_extra_captions(monkeypatch) -> None:
    group_id = "album-1"
    db_user = make_user()
    session = AsyncMock()
    factory = FakeSessionFactory(session)

    first = make_message(
        message_id=10,
        caption="Главная подпись",
        photo=make_photo_sizes(("f1", "u1")),
        media_group_id=group_id,
    )
    second = make_message(
        message_id=20,
        caption="Лишняя подпись",
        photo=make_photo_sizes(("f2", "u2")),
        media_group_id=group_id,
        bot=first.bot,
    )
    submission_intake._media_group_buffers[group_id] = [second, first]
    submission_intake._media_group_locks[group_id] = asyncio.Lock()
    submission_intake._media_group_timestamps[group_id] = submission_intake.time.monotonic()

    sub = make_submission(sub_id=42, user=db_user, caption="Главная подпись")
    sub_full = make_submission(sub_id=42, user=db_user, caption="Главная подпись", media=[make_media(), make_media(media_id=2, sort_order=1)])

    monkeypatch.setattr(submission_intake.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(submission_intake, "create_submission", AsyncMock(return_value=sub))
    add_media = AsyncMock()
    monkeypatch.setattr(submission_intake, "add_media", add_media)
    monkeypatch.setattr(submission_intake, "get_submission_with_user", AsyncMock(return_value=sub_full))
    monkeypatch.setattr(submission_intake.topics, "ensure_user_topic", AsyncMock(return_value=42))
    monkeypatch.setattr(submission_intake.topics, "post_submission_card", AsyncMock(return_value=([], 1)))
    monkeypatch.setattr(submission_intake.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(submission_intake, "_render_queue", AsyncMock())

    await submission_intake._finalize_media_group(group_id, factory, db_user)

    assert [call.kwargs["file_id"] for call in add_media.await_args_list] == ["f1", "f2"]
    assert [call.kwargs["sort_order"] for call in add_media.await_args_list] == [0, 1]
    assert first.answer.await_args_list[0].args[0] == msg.MEDIA_GROUP_EXTRA_CAPTIONS_WARNING.format(count=1)
    assert first.answer.await_args_list[1].args[0] == msg.SUBMISSION_ACCEPTED.format(sub_id=42)
    # Границы транзакций: сам пост, тема, карточка, очередь заголовка.
    assert session.commit.await_count == 4


async def test_finalize_media_group_rejects_too_long_caption(monkeypatch) -> None:
    group_id = "album-2"
    db_user = make_user()
    message = make_message(
        message_id=10,
        caption="Слишком длинно",
        photo=make_photo_sizes(("f1", "u1")),
        media_group_id=group_id,
    )
    submission_intake._media_group_buffers[group_id] = [message]
    submission_intake._media_group_locks[group_id] = asyncio.Lock()
    submission_intake._media_group_timestamps[group_id] = submission_intake.time.monotonic()

    create_submission = AsyncMock()
    monkeypatch.setattr(submission_intake.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(submission_intake, "validate_caption_length", lambda *args, **kwargs: False)
    monkeypatch.setattr(submission_intake, "create_submission", create_submission)

    await submission_intake._finalize_media_group(group_id, FakeSessionFactory(AsyncMock()), db_user)

    message.answer.assert_awaited_once_with(msg.CAPTION_TOO_LONG)
    create_submission.assert_not_awaited()


async def test_handle_media_group_banned_user_gets_one_reply_per_group() -> None:
    db_user = make_user(is_banned=True, ban_reason="Спам")
    first = make_message(media_group_id="g1", photo=make_photo_sizes(("f1", "u1")))
    second = make_message(media_group_id="g1", photo=make_photo_sizes(("f2", "u2")), bot=first.bot)

    await viewer.handle_media_group(first, AsyncMock(), db_user)
    await viewer.handle_media_group(second, AsyncMock(), db_user)

    first.answer.assert_awaited_once()
    second.answer.assert_not_awaited()


async def test_handle_single_media_blocks_banned_user() -> None:
    message = make_message(photo=make_photo_sizes(("f1", "u1")))
    db_user = make_user(is_banned=True, ban_reason="Причина")

    await viewer.handle_single_media(message, AsyncMock(), db_user)

    message.answer.assert_awaited_once()


async def test_handle_single_media_rejects_unsupported_type(monkeypatch) -> None:
    monkeypatch.setattr(submission_intake, "extract_media_info", lambda message: None)
    message = make_message()

    await viewer.handle_single_media(message, AsyncMock(), make_user())

    message.answer.assert_awaited_once_with(msg.UNSUPPORTED_MEDIA)


async def test_handle_single_media_creates_submission_and_notifies_moderator(monkeypatch) -> None:
    session = AsyncMock()
    message = make_message(photo=make_photo_sizes(("f1", "u1")))
    db_user = make_user()
    sub = make_submission(sub_id=9, user=db_user)

    monkeypatch.setattr(submission_intake, "create_submission", AsyncMock(return_value=sub))
    add_media = AsyncMock()
    monkeypatch.setattr(submission_intake, "add_media", add_media)
    monkeypatch.setattr(submission_intake, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(submission_intake.topics, "ensure_user_topic", AsyncMock(return_value=42))
    monkeypatch.setattr(submission_intake.topics, "post_submission_card", AsyncMock(return_value=([], 1)))
    monkeypatch.setattr(submission_intake.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(submission_intake, "_render_queue", AsyncMock())

    await viewer.handle_single_media(message, session, db_user)

    add_media.assert_awaited_once()
    message.answer.assert_awaited_once()
    submission_intake.topics.post_submission_card.assert_awaited_once()


async def test_handle_text_submission_rejects_empty_text(monkeypatch) -> None:
    monkeypatch.setattr(submission_intake, "get_html_text", lambda message: "   ")
    message = make_message(text="ignored")

    await viewer.handle_text_submission(message, AsyncMock(), make_user())

    message.answer.assert_awaited_once_with(msg.TEXT_EMPTY_WARNING)


async def test_handle_text_submission_rejects_too_long_text(monkeypatch) -> None:
    monkeypatch.setattr(submission_intake, "get_html_text", lambda message: "x" * 5000)
    message = make_message(text="ignored")

    await viewer.handle_text_submission(message, AsyncMock(), make_user())

    message.answer.assert_awaited_once_with(
        msg.TEXT_TOO_LONG.format(length=5000, max_length=submission_intake.MAX_TEXT_CAPTION)
    )


async def test_handle_viewer_cancel_rejects_foreign_submission(monkeypatch) -> None:
    sub = make_submission(sub_id=7, user=make_user(user_id=99))
    monkeypatch.setattr(viewer, "get_submission_with_user", AsyncMock(return_value=sub))
    callback = make_callback(message=make_message())

    await viewer.handle_viewer_cancel(callback, SimpleNamespace(sub_id=7), AsyncMock(), make_user(user_id=1))

    callback.answer.assert_awaited_once_with(msg.NOT_YOUR_SUBMISSION, show_alert=True)


async def test_handle_viewer_cancel_updates_status_and_finalizes_mod_channel(monkeypatch) -> None:
    db_user = make_user(user_id=11)
    session = AsyncMock()
    sub = make_submission(sub_id=7, user=db_user, status="pending")
    monkeypatch.setattr(viewer, "get_submission_with_user", AsyncMock(return_value=sub))
    update_status = AsyncMock()
    monkeypatch.setattr(viewer, "update_submission_status", update_status)
    monkeypatch.setattr(viewer.topic_notifications, "notify_viewer_cancelled", AsyncMock())
    monkeypatch.setattr(viewer.topics, "finalize_submission_card", AsyncMock())
    monkeypatch.setattr(viewer.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(viewer, "_render_queue", AsyncMock())
    callback = make_callback(message=make_message())

    await viewer.handle_viewer_cancel(callback, SimpleNamespace(sub_id=7), session, db_user)

    assert sub.status == "cancelled"
    update_status.assert_awaited_once_with(session, 7, "cancelled")
    viewer.topics.finalize_submission_card.assert_awaited_once()
    callback.message.edit_text.assert_awaited_once_with(msg.SUBMISSION_CANCELLED.format(sub_id=7))
    callback.answer.assert_awaited_once_with(msg.SUBMISSION_CANCELLED.format(sub_id=7))
