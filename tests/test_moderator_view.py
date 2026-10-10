"""Unit tests for handlers/moderator/view.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

from handlers.moderator import view
from tests.helpers import FakeState, make_media, make_message, make_submission

# Описание у самого лимита: 1000 видимых символов сами по себе влезают в подпись,
# но вместе с шапкой превью («От», «Медиа», «Статус») — уже нет.
_LONG_CAPTION = "<b>" + "x" * 1000 + "</b>"


async def test_short_caption_goes_into_media_caption(monkeypatch) -> None:
    sub = make_submission(sub_id=5, caption="Short", media=[make_media(submission_id=5)])
    message = make_message()
    state = FakeState()
    monkeypatch.setattr(view, "get_submission_with_user", AsyncMock(return_value=sub))

    assert await view.render_submission_view(message, AsyncMock(), 5, state) is True

    assert "Short" in message.answer_photo.await_args.kwargs["caption"]
    # Единственный answer — сообщение с кнопками действий.
    assert message.answer.await_count == 1
    assert state.data["media_message_ids"] == [message.answer_photo.return_value.message_id]


async def test_long_caption_single_media_sent_as_separate_text(monkeypatch) -> None:
    sub = make_submission(sub_id=5, caption=_LONG_CAPTION, media=[make_media(submission_id=5)])
    message = make_message()
    state = FakeState()
    monkeypatch.setattr(view, "get_submission_with_user", AsyncMock(return_value=sub))

    assert await view.render_submission_view(message, AsyncMock(), 5, state) is True

    assert message.answer_photo.await_args.kwargs["caption"] is None
    preview_call, actions_call = message.answer.await_args_list
    assert _LONG_CAPTION in preview_call.args[0]
    assert preview_call.kwargs["parse_mode"] == "HTML"
    assert "reply_markup" in actions_call.kwargs
    assert state.data["media_message_ids"] == [
        message.answer_photo.return_value.message_id,
        message.answer.return_value.message_id,
    ]
    assert state.data["actions_message_id"] == message.answer.return_value.message_id


async def test_long_caption_media_group_sent_as_separate_text(monkeypatch) -> None:
    media = [make_media(media_id=1, submission_id=5), make_media(media_id=2, submission_id=5)]
    sub = make_submission(sub_id=5, caption=_LONG_CAPTION, media=media)
    message = make_message()
    state = FakeState()
    monkeypatch.setattr(view, "get_submission_with_user", AsyncMock(return_value=sub))

    assert await view.render_submission_view(message, AsyncMock(), 5, state) is True

    group = message.answer_media_group.await_args.args[0]
    assert all(item.caption is None for item in group)
    assert _LONG_CAPTION in message.answer.await_args_list[0].args[0]
    assert len(state.data["media_message_ids"]) == 3
