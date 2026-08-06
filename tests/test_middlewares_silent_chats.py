"""Tests for SilentChatsMiddleware — muting notifications in the moderator group."""

import pytest
from aiogram.methods import EditMessageText, SendMessage, SendPhoto

from middlewares.silent_chats import SilentChatsMiddleware

GROUP_ID = -1001234567890


async def _passthrough(bot, method):
    return method


@pytest.fixture
def middleware() -> SilentChatsMiddleware:
    return SilentChatsMiddleware({GROUP_ID})


@pytest.mark.asyncio
async def test_injects_flag_for_target_chat(middleware):
    method = SendMessage(chat_id=GROUP_ID, text="статус изменён")
    await middleware(_passthrough, None, method)
    assert method.disable_notification is True


@pytest.mark.asyncio
async def test_injects_flag_for_media_methods(middleware):
    method = SendPhoto(chat_id=GROUP_ID, photo="file_id")
    await middleware(_passthrough, None, method)
    assert method.disable_notification is True


@pytest.mark.asyncio
async def test_accepts_string_chat_id(middleware):
    method = SendMessage(chat_id=str(GROUP_ID), text="hi")
    await middleware(_passthrough, None, method)
    assert method.disable_notification is True


@pytest.mark.asyncio
async def test_other_chats_untouched(middleware):
    method = SendMessage(chat_id=-1009999999999, text="в канал")
    await middleware(_passthrough, None, method)
    assert method.disable_notification is None


@pytest.mark.asyncio
async def test_explicit_flag_preserved(middleware):
    method = SendMessage(chat_id=GROUP_ID, text="важно", disable_notification=False)
    await middleware(_passthrough, None, method)
    assert method.disable_notification is False


@pytest.mark.asyncio
async def test_method_without_flag_untouched(middleware):
    method = EditMessageText(chat_id=GROUP_ID, message_id=1, text="обновление")
    await middleware(_passthrough, None, method)
    assert not hasattr(method, "disable_notification")


@pytest.mark.asyncio
async def test_empty_chat_set_is_noop():
    mw = SilentChatsMiddleware(set())
    method = SendMessage(chat_id=GROUP_ID, text="hi")
    await mw(_passthrough, None, method)
    assert method.disable_notification is None


@pytest.mark.asyncio
async def test_returns_downstream_result(middleware):
    sentinel = object()

    async def make_request(bot, method):
        return sentinel

    method = SendMessage(chat_id=GROUP_ID, text="hi")
    assert await middleware(make_request, None, method) is sentinel
