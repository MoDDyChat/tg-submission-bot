"""Tests for GroupEditPacingMiddleware — spacing edits in the moderator group."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.methods import EditForumTopic, EditMessageText, SendMessage

from middlewares import group_pacing
from middlewares.group_pacing import GroupEditPacingMiddleware

GROUP_ID = -1001234567890


async def _passthrough(bot, method):
    return method


def _edit(chat_id: int = GROUP_ID) -> EditMessageText:
    return EditMessageText(chat_id=chat_id, message_id=1, text="обновление")


async def test_single_edit_is_not_delayed() -> None:
    mw = GroupEditPacingMiddleware({GROUP_ID}, min_interval=1.0)
    sleep = AsyncMock()
    with patch.object(group_pacing.asyncio, "sleep", sleep):
        await mw(_passthrough, None, _edit())
    sleep.assert_not_awaited()


async def test_back_to_back_edits_are_spaced() -> None:
    mw = GroupEditPacingMiddleware({GROUP_ID}, min_interval=1.0)
    sleep = AsyncMock()
    with patch.object(group_pacing.asyncio, "sleep", sleep):
        await asyncio.gather(*(mw(_passthrough, None, _edit()) for _ in range(3)))
    delays = sorted(c.args[0] for c in sleep.await_args_list)
    assert delays == [pytest.approx(1.0, abs=0.05), pytest.approx(2.0, abs=0.05)]


async def test_forum_topic_edits_share_the_pace() -> None:
    mw = GroupEditPacingMiddleware({GROUP_ID}, min_interval=1.0)
    sleep = AsyncMock()
    title = EditForumTopic(chat_id=GROUP_ID, message_thread_id=5, name="тема")
    with patch.object(group_pacing.asyncio, "sleep", sleep):
        await mw(_passthrough, None, _edit())
        await mw(_passthrough, None, title)
    sleep.assert_awaited_once()


async def test_sends_and_other_chats_pass_through() -> None:
    mw = GroupEditPacingMiddleware({GROUP_ID}, min_interval=1.0)
    sleep = AsyncMock()
    with patch.object(group_pacing.asyncio, "sleep", sleep):
        await mw(_passthrough, None, _edit())
        await mw(_passthrough, None, SendMessage(chat_id=GROUP_ID, text="hi"))
        await mw(_passthrough, None, _edit(chat_id=-1009999999999))
    sleep.assert_not_awaited()


async def test_returns_downstream_result() -> None:
    sentinel = object()

    async def make_request(bot, method):
        return sentinel

    mw = GroupEditPacingMiddleware({GROUP_ID})
    assert await mw(make_request, None, _edit()) is sentinel
