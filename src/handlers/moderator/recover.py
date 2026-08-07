"""Moderator: recover — restore missing topic submission cards."""

import asyncio

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.logging import get_logger
from db.models import Submission
from db.queries import clear_topic_card_ids, delete_user_topic, get_active_submissions
from services import topics

logger = get_logger(__name__)

router = Router()

# Pause between Telegram API calls to stay under EditMessageText flood limits.
_PROBE_DELAY = 0.5


async def recover_missing_posts(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    """Find active submissions whose topic cards are missing and resend them.

    Each submission is processed in short phases — claim/clear, call, record —
    every DB phase in its own short transaction that commits before the next
    one starts, so a submission row lock is never held across a Telegram
    request or the inter-call sleep.
    """
    async with session_factory() as session:
        submissions = await get_active_submissions(session)

    total = len(submissions)
    recovered = 0

    for sub in submissions:
        try:
            if sub.topic_card_message_id is not None:
                # Probe in its own short transaction: the refreshed-card hash
                # write must not stay open across the sleep or the repost.
                async with session_factory() as session:
                    card_alive = await topics.probe_submission_card(bot, session, sub)
                    await session.commit()
                await asyncio.sleep(_PROBE_DELAY)
                if card_alive:
                    continue
                logger.info(
                    "Пост #%d: карточка %d отсутствует в теме, восстанавливаем",
                    sub.id, sub.topic_card_message_id,
                )
                # Claim/clear phase: drop the stale IDs and commit before any
                # Telegram call so the row is never locked across the network.
                async with session_factory() as session:
                    await clear_topic_card_ids(session, sub.id)
                    await session.commit()
                sub.topic_card_message_id = None
                sub.topic_media_message_ids = None

            # Call phase: Telegram requests with no open DB transaction.
            await _repost_card(bot, session_factory, sub)
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

    return total, recovered


async def _repost_card(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    sub: Submission,
) -> None:
    """Post the submission card, handling the case where the forum topic was deleted.

    ``post_submission_card`` internally calls ``ensure_user_topic``, which would
    INSERT a ``UserTopic`` and then send welcome/media/card messages before the
    caller's commit — a write transaction held open across Telegram calls. To
    keep the recovery path clean, the user's forum topic is ensured first in its
    own short committed transaction; ``post_submission_card`` then only reads it.
    The recreate path drops the stale topic row and repeats the same two phases,
    each DB phase in a separate short transaction.
    """
    await _ensure_topic_committed(bot, session_factory, sub)
    try:
        async with session_factory() as session:
            await topics.post_submission_card(bot, session, sub)
            await session.commit()
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "message thread not found" not in err and "topic_deleted" not in err:
            raise
        logger.info(
            "Тема форума пользователя %d удалена, создаём новую для поста #%d",
            sub.user_id, sub.id,
        )
        async with session_factory() as session:
            await delete_user_topic(session, sub.user_id)
            await session.commit()
        await _ensure_topic_committed(bot, session_factory, sub)
        async with session_factory() as session:
            await topics.post_submission_card(bot, session, sub)
            await session.commit()


async def _ensure_topic_committed(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    sub: Submission,
) -> None:
    """Ensure the user's forum topic exists, committing its creation first."""
    async with session_factory() as session:
        await topics.ensure_user_topic(bot, session, sub.user)
        await session.commit()
