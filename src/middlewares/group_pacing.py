"""Session-level middleware that paces edits in the moderator group.

Every edit in a group spends one Telegram quota: the queue and schedule boards,
submission and author cards, the dashboard and forum topic titles all draw on
it, and a burst from several of them at once trips flood control for the whole
group. Edits to the configured chats are spaced at least ``min_interval``
seconds apart, in arrival order. An edit with no other edit in flight goes out
immediately; other methods and other chats are never delayed.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods import (
    EditForumTopic,
    EditMessageCaption,
    EditMessageMedia,
    EditMessageReplyMarkup,
    EditMessageText,
    Response,
    TelegramMethod,
)
from aiogram.methods.base import TelegramType

from middlewares.silent_chats import _as_chat_id

if TYPE_CHECKING:
    from aiogram import Bot

_PACED_METHODS = (
    EditMessageText,
    EditMessageCaption,
    EditMessageMedia,
    EditMessageReplyMarkup,
    EditForumTopic,
)


class GroupEditPacingMiddleware(BaseRequestMiddleware):
    """Keep edits to *chat_ids* at least *min_interval* seconds apart."""

    def __init__(self, chat_ids: set[int], min_interval: float = 1.0) -> None:
        self._chat_ids = chat_ids
        self._min_interval = min_interval
        self._next_slot = 0.0

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: "Bot",
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if isinstance(method, _PACED_METHODS):
            chat_id = _as_chat_id(getattr(method, "chat_id", None))
            if chat_id is not None and chat_id in self._chat_ids:
                # Reserve a slot before awaiting: there is no await between the
                # read and the write, so concurrent callers get distinct slots.
                now = asyncio.get_running_loop().time()
                start = max(now, self._next_slot)
                self._next_slot = start + self._min_interval
                if start > now:
                    await asyncio.sleep(start - now)
        return await make_request(bot, method)
