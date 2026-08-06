"""Session-level middleware that mutes notifications for selected chats.

Any outgoing Telegram method that supports ``disable_notification`` and targets
one of the configured chats (the moderator forum group) is sent silently, so
moderators see status/publication updates without a push ping.

The flag is only injected when the caller did not set it explicitly, so
existing ``disable_notification=False`` call sites keep their behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods import Response, TelegramMethod
from aiogram.methods.base import TelegramType

if TYPE_CHECKING:
    from aiogram import Bot


def _as_chat_id(value: object) -> int | None:
    """Return *value* as an int chat id, or ``None`` if it is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


class SilentChatsMiddleware(BaseRequestMiddleware):
    """Force ``disable_notification=True`` for requests to *chat_ids*."""

    def __init__(self, chat_ids: set[int]) -> None:
        self._chat_ids = chat_ids

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: "Bot",
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if self._chat_ids and "disable_notification" in type(method).model_fields:
            if getattr(method, "disable_notification", None) is None:
                chat_id = _as_chat_id(getattr(method, "chat_id", None))
                if chat_id is not None and chat_id in self._chat_ids:
                    method.disable_notification = True
        return await make_request(bot, method)
