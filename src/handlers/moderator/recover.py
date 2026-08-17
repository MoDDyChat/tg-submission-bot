"""Moderator: recover — restore missing topic submission cards."""

import asyncio

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.logging import get_logger
from db.models import Submission
from db.queries import (
    clear_topic_card_ids_if_unchanged,
    delete_user_topic,
    get_active_submissions,
    get_submission_with_user,
    list_active_submissions_without_card,
)
from services import topics

logger = get_logger(__name__)

router = Router()

# Pause between Telegram API calls to stay under EditMessageText flood limits.
_PROBE_DELAY = 0.5

# Manual Recover and the periodic cardless job share one guard: without it the
# two could repost the same card twice. Lazily created — a lock binds to the
# event loop that first takes it.
_recover_guard: asyncio.Lock | None = None


def _get_recover_guard() -> asyncio.Lock:
    """Return the shared recovery guard, creating it lazily on the current loop."""
    global _recover_guard
    if _recover_guard is None:
        _recover_guard = asyncio.Lock()
    return _recover_guard


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
    async with _get_recover_guard():
        async with session_factory() as session:
            submissions = await get_active_submissions(session)

        total = len(submissions)
        recovered = 0

        for sub in submissions:
            try:
                target = sub
                if sub.topic_card_message_id is not None:
                    probed_id = sub.topic_card_message_id
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
                        sub.id, probed_id,
                    )
                    # Claim/clear phase: drop the stale IDs and commit before any
                    # Telegram call so the row is never locked across the network.
                    # Compare-and-swap on the probed ID: another writer may have
                    # replaced the card while the probe was in flight, and a blind
                    # clear would erase its freshly committed IDs.
                    async with session_factory() as session:
                        claimed = await clear_topic_card_ids_if_unchanged(
                            session, sub.id, probed_id
                        )
                        await session.commit()
                    if not claimed:
                        logger.info(
                            "Пост #%d: карточка сменилась во время проверки, пропускаем",
                            sub.id,
                        )
                        continue
                    # Repost the freshly read row, not the batch snapshot: the
                    # media composition may have changed since the selection.
                    fresh = await _still_cardless(session_factory, sub.id)
                    if fresh is None:
                        continue
                    target = fresh

                # Call phase: Telegram requests with no open DB transaction.
                await _repost_card(bot, session_factory, target)
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


async def recover_cardless_posts(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Переотправить карточки активных постов, у которых её нет. Возвращает число восстановленных."""
    async with _get_recover_guard():
        async with session_factory() as session:
            submissions = await list_active_submissions_without_card(session)

        recovered = 0
        for sub in submissions:
            try:
                fresh = await _still_cardless(session_factory, sub.id)
                if fresh is None:
                    continue
                await _repost_card(bot, session_factory, fresh)
                recovered += 1
                await asyncio.sleep(_PROBE_DELAY)
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except Exception:
                logger.exception("Ошибка при восстановлении карточки поста #%d", sub.id)

        if recovered:
            logger.info("Восстановлены карточки постов без карточки: %d", recovered)
        return recovered


_TERMINAL_STATUSES = ("published", "rejected", "cancelled")


async def _still_cardless(
    session_factory: async_sessionmaker[AsyncSession],
    sub_id: int,
) -> Submission | None:
    """Re-read the post right before the repost: still without a card?

    Returns the freshly loaded post (with ``user`` and ``media``) or None if it
    must be skipped. The fresh object — not the detached row from the batch
    query — is what gets reposted: the two are separated by Telegram calls and
    sleeps, and a moderator may have changed the media composition meanwhile,
    which no later reconcile would repair (the card hash covers only text and
    keyboard). This narrows the window; closing it fully needs a durable claim
    in the DB and is out of scope here.
    """
    async with session_factory() as session:
        fresh = await get_submission_with_user(session, sub_id)
    if fresh is None:
        return None
    if fresh.topic_card_message_id is not None:
        logger.info("Пост #%d: карточка появилась до восстановления, пропускаем", sub_id)
        return None
    if fresh.status in _TERMINAL_STATUSES:
        return None
    return fresh


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
        await _post_card_committed(bot, session_factory, sub)
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
        await _post_card_committed(bot, session_factory, sub)


async def _post_card_committed(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    sub: Submission,
) -> None:
    """Post the card and commit its IDs, undoing the delivery if the commit fails.

    Telegram would otherwise keep a live block the DB knows nothing about, and
    the next recovery pass would repost it as a duplicate.
    """
    async with session_factory() as session:
        media_ids, card_id = await topics.post_submission_card(bot, session, sub)
        await topics.commit_or_delete_delivered(session, bot, [*media_ids, card_id], sub.id)


async def _ensure_topic_committed(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    sub: Submission,
) -> None:
    """Ensure the user's forum topic exists, committing its creation first."""
    async with session_factory() as session:
        await topics.ensure_user_topic(bot, session, sub.user)
        await session.commit()
