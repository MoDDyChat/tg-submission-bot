"""Publishing logic: send submissions to the channel and notify viewers."""

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import InputMediaAnimation, InputMediaDocument, InputMediaPhoto, InputMediaVideo
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import core.messages as msg
from core.config import config
from core.exceptions import (
    PublishFailedError,
    PublishStateUnknownError,
    PublicationNotFoundError,
    SubmissionCancelledError,
    SubmissionNotFoundError,
    SubmissionStatusError,
)
from core.logging import get_logger
from db.models import User
from db.queries import (
    get_publication,
    get_submission_with_user,
    mark_published,
    update_submission_status,
)
from services import topic_notifications
from services import topics as topics_svc
from services.author_card import request_author_card
from services.dashboard import request_dashboard
from services.topics_queue import render_queue as _render_queue
from services.topics_queue import render_schedule as _render_schedule
from utils.tags import compose_caption

logger = get_logger(__name__)

MEDIA_TYPE_MAP = {
    "photo": InputMediaPhoto,
    "video": InputMediaVideo,
    "animation": InputMediaAnimation,
    "document": InputMediaDocument,
}

_TELEGRAM_TIMEOUT = 30.0


async def _delete_channel_messages(bot: Bot, message_ids: list[int]) -> bool:
    """Best-effort rollback of already-sent channel messages."""
    success = True
    for message_id in message_ids:
        try:
            await bot.delete_message(config.channel_id, message_id)
        except TelegramBadRequest as exc:
            if "message to delete not found" in str(exc).lower():
                pass  # Already gone — treat as success
            else:
                logger.warning(
                    "Не удалось откатить сообщение %d из основного канала",
                    message_id,
                    exc_info=True,
                )
                success = False
        except Exception:
            logger.warning(
                "Не удалось откатить сообщение %d из основного канала",
                message_id,
                exc_info=True,
            )
            success = False
    return success


async def _persist_publication_result(
    session: AsyncSession,
    publication_id: int,
    submission_id: int,
    channel_message_id: int | None,
    channel_message_ids: list[int],
) -> None:
    await mark_published(
        session, publication_id, channel_message_id, channel_message_ids
    )
    await update_submission_status(session, submission_id, "published")
    await session.commit()


async def _persist_publication_result_fresh(
    session_factory: async_sessionmaker[AsyncSession],
    publication_id: int,
    submission_id: int,
    channel_message_id: int | None,
    channel_message_ids: list[int],
) -> None:
    async with session_factory() as retry_session:
        pub = await get_publication(retry_session, publication_id)
        if pub is None:
            raise PublicationNotFoundError(publication_id)

        sub = await get_submission_with_user(retry_session, submission_id)
        if sub is None:
            raise SubmissionNotFoundError(submission_id)
        if sub.status == "cancelled":
            raise SubmissionCancelledError(submission_id)
        if sub.status != "scheduled":
            raise SubmissionStatusError(submission_id, sub.status)

        await _persist_publication_result(
            retry_session,
            publication_id,
            submission_id,
            channel_message_id,
            channel_message_ids,
        )


async def publish_post(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    publication_id: int,
    submission_id: int,
    edited_caption: str | None,
    actor: User | None = None,
) -> None:
    """Send a submission to the channel and notify the viewer."""
    async with session_factory() as session:
        # Advisory lock: prevent concurrent publish of the same publication
        # (single-process, but guards against scheduler + publish_now race)
        advisory_result = await session.execute(
            text("SELECT pg_try_advisory_xact_lock(:pub_id)").bindparams(pub_id=publication_id)
        )
        if not advisory_result.scalar():
            logger.info("Публикация [pub:%d] уже выполняется другим потоком, пропуск", publication_id)
            return

        # Idempotency: skip if already published
        pub = await get_publication(session, publication_id)
        if pub is None:
            raise PublicationNotFoundError(publication_id)
        if pub.published_at is not None:
            logger.info("Публикация [pub:%d] уже выполнена, пропуск", publication_id)
            return

        sub = await get_submission_with_user(session, submission_id)
        if sub is None:
            raise SubmissionNotFoundError(submission_id)

        if sub.status == "cancelled":
            raise SubmissionCancelledError(submission_id)
        if sub.status != "scheduled":
            raise SubmissionStatusError(submission_id, sub.status)

        # Pre-fetch viewer telegram_id before any rollback or session close
        viewer_telegram_id: int = sub.user.telegram_id

        media_list = sub.media
        description = edited_caption or sub.caption
        caption = compose_caption(sub.tags or [], description)
        channel_message_id = None
        all_channel_message_ids: list[int] = []

        _MAX_ATTEMPTS = 3
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                if not media_list:
                    result = await asyncio.wait_for(
                        bot.send_message(config.channel_id, caption or ""),
                        timeout=_TELEGRAM_TIMEOUT,
                    )
                    channel_message_id = result.message_id
                    all_channel_message_ids = [result.message_id]

                elif len(media_list) == 1:
                    m = media_list[0]
                    send_fn = {
                        "photo": bot.send_photo,
                        "video": bot.send_video,
                        "animation": bot.send_animation,
                        "document": bot.send_document,
                    }.get(m.media_type, bot.send_document)
                    result = await asyncio.wait_for(
                        send_fn(
                            config.channel_id,
                            m.file_id,
                            caption=caption,
                            parse_mode="HTML",
                        ),
                        timeout=_TELEGRAM_TIMEOUT,
                    )
                    channel_message_id = result.message_id
                    all_channel_message_ids = [result.message_id]

                else:
                    group = []
                    for i, item in enumerate(media_list):
                        cls = MEDIA_TYPE_MAP.get(item.media_type, InputMediaDocument)
                        group.append(cls(
                            media=item.file_id,
                            caption=caption if i == 0 else None,
                            parse_mode="HTML" if i == 0 else None,
                        ))
                    results = await asyncio.wait_for(
                        bot.send_media_group(config.channel_id, group),
                        timeout=_TELEGRAM_TIMEOUT,
                    )
                    channel_message_id = results[0].message_id
                    all_channel_message_ids = [r.message_id for r in results]

                logger.info(
                    "Пост #%d опубликован в канал (msg_ids=%s)",
                    submission_id, all_channel_message_ids,
                )
                last_exc = None
                break

            except TelegramRetryAfter as exc:
                last_exc = exc
                if attempt < _MAX_ATTEMPTS:
                    logger.warning(
                        "Flood control для поста #%d, ждём %ds (попытка %d/%d)",
                        submission_id, exc.retry_after, attempt, _MAX_ATTEMPTS,
                    )
                    await asyncio.sleep(exc.retry_after)
            except (TelegramNetworkError, asyncio.TimeoutError) as exc:
                last_exc = exc
                if attempt < _MAX_ATTEMPTS:
                    logger.warning(
                        "Сетевая ошибка при публикации поста #%d: %s, retry через 5с (попытка %d/%d)",
                        submission_id, exc, attempt, _MAX_ATTEMPTS,
                    )
                    await asyncio.sleep(5)
            except Exception as exc:
                logger.exception("Не удалось опубликовать пост #%d", submission_id)
                raise PublishFailedError(str(exc)) from exc

        if last_exc is not None:
            logger.exception("Не удалось опубликовать пост #%d (исчерпаны попытки)", submission_id)
            raise PublishFailedError(str(last_exc)) from last_exc

        # Persist DB state. If the current session fails after Telegram send, retry once
        # in a fresh session; if that also fails, compensate by deleting the sent messages.
        try:
            await _persist_publication_result(
                session,
                publication_id,
                submission_id,
                channel_message_id,
                all_channel_message_ids,
            )
        except Exception:
            logger.exception(
                "Не удалось зафиксировать публикацию [pub:%d] в БД, пробуем повторно",
                publication_id,
            )
            try:
                await session.rollback()
            except Exception:
                logger.warning(
                    "Не удалось откатить текущую DB-сессию после ошибки фиксации [pub:%d]",
                    publication_id,
                    exc_info=True,
                )

            try:
                await _persist_publication_result_fresh(
                    session_factory,
                    publication_id,
                    submission_id,
                    channel_message_id,
                    all_channel_message_ids,
                )
                logger.warning(
                    "Публикация [pub:%d] была зафиксирована повторно через новую DB-сессию",
                    publication_id,
                )
            except Exception as persist_exc:
                logger.exception(
                    "Повторная фиксация публикации [pub:%d] не удалась, откатываем Telegram side effects",
                    publication_id,
                )
                deleted = await _delete_channel_messages(bot, all_channel_message_ids)
                if deleted:
                    raise PublishFailedError(
                        "DB synchronization failed after send; published messages were rolled back"
                    ) from persist_exc
                raise PublishStateUnknownError(publication_id, submission_id) from persist_exc

        # Best-effort cleanup: finalize topic card and notify (non-critical, separate session)
        async with session_factory() as cleanup_session:
            fresh_sub = await get_submission_with_user(cleanup_session, submission_id)
            if fresh_sub:
                try:
                    await topics_svc.finalize_submission_card(bot, cleanup_session, fresh_sub)
                except Exception:
                    logger.exception("Не удалось финализировать карточку поста #%d", submission_id)

                try:
                    await topics_svc.request_topic_title_sync(
                        cleanup_session, fresh_sub.user.id
                    )
                except Exception:
                    logger.exception(
                        "Не удалось обновить заголовок темы для поста #%d", submission_id
                    )

                try:
                    await topic_notifications.notify_published(
                        bot, cleanup_session, fresh_sub, by_moderator=actor
                    )
                except Exception:
                    logger.exception(
                        "Не удалось уведомить тему о публикации поста #%d", submission_id
                    )

            try:
                await _render_queue(bot, cleanup_session)
                await _render_schedule(bot, cleanup_session)
                request_dashboard()
                if fresh_sub:
                    request_author_card(fresh_sub.user.id)
            except Exception:
                logger.exception("Не удалось обновить очередь после публикации поста #%d", submission_id)

            try:
                await cleanup_session.commit()
            except Exception:
                logger.exception(
                    "Не удалось зафиксировать cleanup-сессию для поста #%d", submission_id
                )

        # Notify the viewer (non-critical)
        try:
            await bot.send_message(
                viewer_telegram_id,
                msg.PUBLISHED_NOTIFICATION.format(sub_id=submission_id),
            )
        except Exception:
            logger.warning(
                "Не удалось уведомить пользователя %d о публикации поста #%d",
                viewer_telegram_id, submission_id,
            )
