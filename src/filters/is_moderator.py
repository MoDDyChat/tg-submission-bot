from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery

from core.config import config


class IsModerator(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return user is not None and user.id in config.moderator_ids
