"""APScheduler wrapper: scheduling, cancellation, and recovery on restart."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import core.messages as msg
from core.config import config
from core.exceptions import (
    MSBotError,
    PublicationNotFoundError,
    SubmissionCancelledError,
    SubmissionStatusError,
)
from core.logging import get_logger
from db.queries import get_unpublished_publications, mark_publication_dead
from services.admin_notifications import notify_admins
from services.publisher import publish_post

logger = get_logger(__name__)

# Module-level reference set by core/bot.py via create_scheduler().
# None until main() initialises it.
scheduler: AsyncIOScheduler | None = None


def create_scheduler() -> AsyncIOScheduler:
    """Create and return a new AsyncIOScheduler instance (owner: core/bot.py)."""
    return AsyncIOScheduler(timezone=config.timezone)


def _job_id(publication_id: int) -> str:
    return f"pub_{publication_id}"


async def _publish_job(
    publication_id: int,
    submission_id: int,
    edited_caption: str | None,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Called by APScheduler at the scheduled time."""
    logger.info("Выполнение задачи публикации [pub:%d, пост:#%d]", publication_id, submission_id)
    try:
        await publish_post(bot, session_factory, publication_id, submission_id, edited_caption)
        logger.debug("Задача [pub:%d] успешно выполнена", publication_id)
    except (PublicationNotFoundError, SubmissionCancelledError, SubmissionStatusError) as exc:
        logger.info("Задача [pub:%d] пропущена: %s", publication_id, exc)
    except MSBotError as exc:
        logger.error("Задача публикации [pub:%d] завершилась с ошибкой: %s", publication_id, exc)
    except Exception:
        logger.exception("Неожиданная ошибка в задаче [pub:%d]", publication_id)


def schedule_post(
    publication_id: int,
    publish_at: datetime,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    submission_id: int | None = None,
    edited_caption: str | None = None,
) -> None:
    """Schedule a publication for a specific datetime."""
    if scheduler is None:
        raise RuntimeError("Scheduler not initialised; call create_scheduler() in main()")
    scheduler.add_job(
        _publish_job,
        trigger="date",
        run_date=publish_at,
        args=[publication_id, submission_id, edited_caption, bot, session_factory],
        id=_job_id(publication_id),
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    logger.debug("Публикация [pub:%d] запланирована на %s", publication_id, publish_at)


def cancel_scheduled(publication_id: int) -> None:
    """Cancel a scheduled publication job."""
    if scheduler is None:
        return
    job_id = _job_id(publication_id)
    job = scheduler.get_job(job_id)
    if job:
        scheduler.remove_job(job_id)
        logger.debug("Задача [pub:%d] отменена", publication_id)


async def recover_scheduled_jobs(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Recover all unpublished publications from DB on startup."""
    async with session_factory() as session:
        publications = await get_unpublished_publications(session)

    MAX_OVERDUE_SECONDS = 86400  # 24 hours

    now = datetime.now(timezone.utc)
    recovered = 0
    immediate = 0
    # (publication, publish_at normalized to UTC) — kept together so the later
    # notification doesn't have to re-derive the timezone-normalized instant.
    dead_candidates: list[tuple] = []

    for pub in publications:
        publish_at = pub.publish_at
        # DB stores TIMESTAMPTZ; asyncpg returns aware datetimes.
        # Fallback: treat naive as UTC (Postgres internal storage format).
        if publish_at.tzinfo is None:
            publish_at = publish_at.replace(tzinfo=timezone.utc)
        else:
            publish_at = publish_at.astimezone(timezone.utc)

        if publish_at <= now:
            overdue_seconds = (now - publish_at).total_seconds()
            if overdue_seconds > MAX_OVERDUE_SECONDS:
                logger.warning(
                    "Публикация #%d просрочена на %.0f ч — помечаем мёртвой",
                    pub.id, overdue_seconds / 3600,
                )
                dead_candidates.append((pub, publish_at))
                continue

            # Missed while bot was down — publish immediately
            logger.info(
                "Публикация [pub:%d] пропущена (запланирована %s), перепланируемся через 1с",
                pub.id, publish_at,
            )
            from datetime import timedelta
            schedule_post(
                pub.id, now + timedelta(seconds=1), bot, session_factory,
                submission_id=pub.submission_id,
                edited_caption=pub.edited_caption,
            )
            immediate += 1
        else:
            schedule_post(
                pub.id, publish_at, bot, session_factory,
                submission_id=pub.submission_id,
                edited_caption=pub.edited_caption,
            )
            recovered += 1

    newly_dead: list[tuple] = []
    if dead_candidates:
        async with session_factory() as session:
            for pub, publish_at in dead_candidates:
                if await mark_publication_dead(session, pub.id):
                    newly_dead.append((pub, publish_at))
            await session.commit()

    if newly_dead:
        tz = ZoneInfo(config.timezone)
        item_lines = "\n".join(
            msg.ADMIN_NOTIFY_DEAD_PUBLICATION_ITEM.format(
                sub_id=pub.submission_id,
                when=publish_at.astimezone(tz).strftime("%d.%m.%Y %H:%M"),
            )
            for pub, publish_at in newly_dead
        )
        async with session_factory() as session:
            await notify_admins(
                bot, session,
                actor=None,
                action_text=msg.ADMIN_NOTIFY_DEAD_PUBLICATIONS.format(
                    count=len(newly_dead), items=item_lines,
                ),
            )

    logger.info(
        "Восстановлено %d job, перепланировано %d просроченных (t+1с), помечено мёртвыми %d",
        recovered, immediate, len(dead_candidates),
    )


def start_scheduler() -> None:
    if scheduler is None:
        raise RuntimeError("Scheduler not initialised; call create_scheduler() in main()")
    if not scheduler.running:
        scheduler.start()
        logger.info("Планировщик запущен")


async def cleanup_edit_locks_job(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Clean up expired edit locks and notify affected topics."""
    from services.edit_lock import cleanup_expired_locks
    from services import topics
    from db.queries import get_submission_with_user

    async with session_factory() as session:
        expired = await cleanup_expired_locks(session)
        await session.commit()

    for lock in expired:
        if lock.resource_type != "submission":
            continue
        try:
            sub_id = int(lock.resource_id)
        except ValueError:
            continue
        async with session_factory() as session:
            sub = await get_submission_with_user(session, sub_id)
            if sub is not None:
                await topics.update_submission_card(bot, session, sub)
                await topics.request_topic_title_sync(session, sub.user.id)
            await session.commit()


async def cleanup_moderator_invites_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Daily cleanup of expired moderator invite links."""
    from services.moderator_invites import cleanup_expired_invites

    async with session_factory() as session:
        await cleanup_expired_invites(session)
        await session.commit()


def shutdown_scheduler() -> None:
    if scheduler is None:
        return
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("Планировщик остановлен (все задачи завершены)")


async def queue_render_job(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Periodic self-heal: refresh the queue board in the General topic."""
    from services.topics_queue import render_queue, render_schedule

    async with session_factory() as session:
        await render_queue(bot, session, force_reconcile=True)
        await render_schedule(bot, session, force_reconcile=True)
        await session.commit()


async def topic_titles_reconcile_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Queue DB-detectable forum-title drift for the outbox worker."""
    from services import topics

    async with session_factory() as session:
        try:
            await topics.reconcile_topic_titles(session)
            await session.commit()
        except Exception:
            logger.warning("Не удалось выполнить reconcile заголовков тем", exc_info=True)


async def topic_cards_reconcile_job(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Repair topic cards left stale by a failed edit.

    The service owns its transactions: each API call happens between them, never
    inside one.
    """
    from services import topics

    try:
        await topics.reconcile_submission_cards(bot, session_factory)
    except Exception:
        logger.warning("Не удалось выполнить reconcile карточек", exc_info=True)


async def topic_title_sync_job(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Apply at most one durable title-sync revision per scheduler tick.

    The service owns its transactions: the Telegram edit runs between them, so a
    hung request can never pin a ``user_topics`` row lock.
    """
    from services import topics

    try:
        await topics.process_next_topic_title_sync(bot, session_factory)
    except Exception:
        logger.warning("Не удалось обработать очередь заголовков тем", exc_info=True)


async def prune_throttle_job(throttle: "ThrottleMiddleware") -> None:  # noqa: F821
    """Hourly cleanup of stale per-user throttle tracking entries."""
    throttle.prune_stale_entries()

 
