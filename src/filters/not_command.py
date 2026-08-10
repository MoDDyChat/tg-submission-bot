from aiogram.filters import BaseFilter
from aiogram.types import Message


class NotCommand(BaseFilter):
    """Pass when the message is not a bot command.

    Deep-link buttons the bot puts on its own topic cards ("✏️ Редактировать",
    the "being edited" indicator) open the DM with the plain text
    ``/start review_<id>``. A text handler of whatever FSM state the moderator
    happens to be in would swallow that text as content — that is how ``/start``
    and ``review_242`` once ended up as tags of post #241. Refusing here lets the
    message fall through to ``CommandStart``, so the deep link still opens the post.
    """

    async def __call__(self, message: Message) -> bool:
        return not (message.text or "").startswith("/")
