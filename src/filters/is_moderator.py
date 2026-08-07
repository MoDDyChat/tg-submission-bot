from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from db.models import User


class IsModerator(BaseFilter):
    """Pass when the incoming event's user has the ``is_moderator`` DB flag."""

    async def __call__(self, event: Message | CallbackQuery, db_user: User) -> bool:
        return db_user.is_moderator
