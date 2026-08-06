"""Moderator: recover — restore missing topic submission cards."""

import asyncio

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging import get_logger
from db.queries import clear_topic_card_ids, delete_user_topic, get_active_submissions
from services import topics

logger = get_logger(__name__)

router = Router()

# Pause between Telegram API calls to stay under EditMessageText flood limits.
_PROBE_DELAY = 0.5


async def recover_missing_posts(
    bot: Bot,
    session: AsyncSession,
) -> tuple[int, int]:
    """Find active submissions whose topic cards are missing and resend them."""
    submissions = await get_active_submissions(session)
    recovered = 0

    for sub in submissions:
        try:
            if sub.topic_card_message_id is not None:
                # Probe: refresh the card and check it still exists.
                # probe_submission_card re-raises TelegramRetryAfter and non-404 errors,
                # so flood control can never cause a false "card missing" result.
                card_alive = await topics.probe_submission_card(bot, session, sub)
                await asyncio.sleep(_PROBE_DELAY)
                if card_alive:
                    continue
                logger.info(
                    "Пост #%d: карточка %d отсутствует в теме, восстанавливаем",
                    sub.id, sub.topic_card_message_id,
                )
                # Only clear IDs after confirming the card is gone.
                await clear_topic_card_ids(session, sub.id)
                sub.topic_card_message_id = None
                sub.topic_media_message_ids = None

            await _repost_card(bot, session, sub)
            recovered += 1
            logger.info("Пост #%d восстановлен в теме модератора", sub.id)
            await asyncio.sleep(_PROBE_DELAY)

        except TelegramRetryAfter as e:
            logger.warning(
                "Пост #%d: flood control при восстановлении, ожидаем %d с",
                sub.id, e.retry_after,
            )
            await asyncio.sleep(e.retry_after)
        except Exception:
            logger.exception("Ошибка при восстановлении поста #%d", sub.id)

    return len(submissions), recovered


async def _repost_card(bot: Bot, session: AsyncSession, sub) -> None:
    """Post the submission card, handling the case where the forum topic was deleted."""
    try:
        await topics.post_submission_card(bot, session, sub)
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "message thread not found" in err or "topic_deleted" in err:
            logger.info(
                "Тема форума пользователя %d удалена, создаём новую для поста #%d",
                sub.user_id, sub.id,
            )
            await delete_user_topic(session, sub.user_id)
            await topics.post_submission_card(bot, session, sub)
        else:
            raise
