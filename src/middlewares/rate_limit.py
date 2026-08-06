"""Throttle middleware — limits requests per user."""
import time
from collections import defaultdict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from core.config import config
from core.logging import get_logger
from core.messages import RATE_LIMIT_EXCEEDED

logger = get_logger(__name__)


class ThrottleMiddleware(BaseMiddleware):
    """Per-user rate limiter. Silently drops excessive requests."""

    def __init__(
        self,
        rate: int | None = None,
        period: float | None = None,
    ) -> None:
        self.rate = rate if rate is not None else config.throttle_rate
        self.period = period if period is not None else config.throttle_period
        self._timestamps: dict[int, list[float]] = defaultdict(list)

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        uid = user.id
        now = time.monotonic()
        # Prune old timestamps outside the window
        self._timestamps[uid] = [
            ts for ts in self._timestamps[uid] if now - ts < self.period
        ]
        if not self._timestamps[uid]:
            del self._timestamps[uid]

        if len(self._timestamps.get(uid, [])) >= self.rate:
            logger.warning("Rate limit: user %d, %d req/%.0fs", uid, self.rate, self.period)
            if isinstance(event, CallbackQuery):
                await event.answer(RATE_LIMIT_EXCEEDED, show_alert=True)
            return None

        self._timestamps[uid].append(now)
        return await handler(event, data)

    def prune_stale_entries(self) -> None:
        """Remove tracking entries for users who have been idle longer than the window.

        Called periodically by the scheduler to prevent unbounded dict growth.
        """
        cutoff = time.monotonic() - self.period
        stale = [
            uid for uid, ts_list in self._timestamps.items()
            if not ts_list or ts_list[-1] < cutoff
        ]
        for uid in stale:
            del self._timestamps[uid]
        if stale:
            logger.debug("Throttle: удалено %d устаревших записей", len(stale))
