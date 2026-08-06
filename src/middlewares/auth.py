from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from cachetools import TTLCache
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging import get_logger, fmt_user
from db.models import User
from db.queries import get_or_create_user

logger = get_logger(__name__)


@dataclass(frozen=True)
class _CachedUserInfo:
    user_id: int
    username: str | None
    full_name: str


# Module-level TTL cache: keyed by telegram_id, TTL = 60 s.
# Single-process bot — no locking needed (asyncio is single-threaded).
_user_cache: TTLCache[int, _CachedUserInfo] = TTLCache(maxsize=10_000, ttl=60)


class AuthMiddleware(BaseMiddleware):
    """Resolves the Telegram user to a DB User record and injects it into data.

    Uses an in-memory TTL cache to avoid repeated upserts on every request.
    ``is_admin`` is NOT written here — managed exclusively by
    ``sync_admin_flags()`` at startup.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        event_user = data.get("event_from_user")
        if event_user is None:
            logger.debug("Нет event_from_user в апдейте, пропуск авторизации")
            return await handler(event, data)

        session: AsyncSession = data["session"]
        uid = event_user.id
        full_name = event_user.full_name or event_user.first_name or "Unknown"

        cached = _user_cache.get(uid)
        if cached is not None:
            db_user = await session.get(User, cached.user_id)
            if db_user is not None:
                # Delta-update username/full_name if changed (no UPSERT overhead)
                if db_user.username != event_user.username or db_user.full_name != full_name:
                    await session.execute(
                        update(User)
                        .where(User.id == cached.user_id)
                        .values(username=event_user.username, full_name=full_name)
                    )
                    db_user.username = event_user.username
                    db_user.full_name = full_name
                    _user_cache[uid] = _CachedUserInfo(
                        user_id=cached.user_id,
                        username=event_user.username,
                        full_name=full_name,
                    )
                data["db_user"] = db_user
                data["is_admin"] = db_user.is_admin
                return await handler(event, data)
            # User was deleted from DB — evict cache and fall through
            del _user_cache[uid]

        # Cache miss: full upsert path
        db_user, is_new = await get_or_create_user(
            session,
            telegram_id=uid,
            username=event_user.username,
            full_name=full_name,
        )
        if is_new:
            logger.info("Новый пользователь: %s", fmt_user(db_user))

        _user_cache[uid] = _CachedUserInfo(
            user_id=db_user.id,
            username=db_user.username,
            full_name=db_user.full_name,
        )
        data["db_user"] = db_user
        data["is_admin"] = db_user.is_admin
        return await handler(event, data)
