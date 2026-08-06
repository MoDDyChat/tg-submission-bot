"""Forum topic management service.

Single point of contact for moderator group forum topics. Each user gets one
dedicated topic; submission cards live inside it alongside system notifications.

Replaces the flat moderator-channel approach of services/mod_channel.py.
"""

import asyncio
import hashlib
import html
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import (
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import config
from core.logging import get_logger
from core.messages import (
    TOPIC_CARD_OUTDATED,
    TOPIC_WELCOME_TEXT,
)
from core.topic_status_config import build_topic_nav_legend
from core.topic_status_config import get_style as _get_status_style
from db.models import EditLock, Submission, User, UserTopic
from db.queries import (
    clear_topic_card_ids,
    enqueue_topic_title_sync,
    ensure_topic_title_sync_pending,
    get_publication_by_submission,
    get_system_message,
    get_user_by_id,
    get_user_topic,
    list_cards_for_reconcile,
    mark_card_rendered,
    mark_card_rendered_if_unchanged,
    mark_topic_title_sync_applied,
    save_topic_card_ids,
    upsert_system_message,
)
from keyboards.moderator import topic_submission_card_kb
from utils.tags import format_tags_line

logger = get_logger(__name__)

MEDIA_SEND_FN: dict[str, str] = {
    "photo": "send_photo",
    "video": "send_video",
    "animation": "send_animation",
    "document": "send_document",
}

MEDIA_INPUT_CLS = {
    "photo": InputMediaPhoto,
    "video": InputMediaVideo,
    "animation": InputMediaAnimation,
    "document": InputMediaDocument,
}

# Status priority for topic title (lower index = higher priority among active)
_ACTIVE_STATUS_PRIORITY = ["editing", "pending", "scheduled"]

# Hint substrings returned by Telegram when a forum topic is closed
_TOPIC_CLOSED_HINTS = ("topic_closed", "topic was closed")

# Lock to serialise concurrent calls to ensure_general_topic_nav (single-process guard)
_general_topic_nav_lock = asyncio.Lock()

# Only one title-outbox consumer runs per bot process. APScheduler also uses
# max_instances=1, but the lock keeps direct/test invocations serialized.
_topic_title_sync_lock = asyncio.Lock()


# ── Title helpers ──────────────────────────────────────────────────

def _build_topic_title(user: User, status_key: str) -> str:
    """Build a Telegram forum topic title.

    Format: ``[LABEL] Full Name`` (no @username).
    Trimmed to ``config.topic_title_max_length`` characters.
    """
    label = _get_status_style(status_key).label
    title = f"[{label}] - {user.full_name}"
    max_len = config.topic_title_max_length
    if len(title) > max_len:
        title = title[:max_len]
    return title


def _topic_icon_kwargs(status_key: str) -> dict:
    """Return keyword arguments for ``edit_forum_topic`` related to the topic icon.

    Returns ``{"icon_custom_emoji_id": ...}`` when a custom emoji is configured,
    otherwise an empty dict (Telegram keeps the existing icon).
    Note: ``icon_color`` can only be set on *create*, not on edit.
    """
    style = _get_status_style(status_key)
    if style.icon_custom_emoji_id:
        return {"icon_custom_emoji_id": style.icon_custom_emoji_id}
    return {}


# ── Status computation ─────────────────────────────────────────────

async def compute_topic_status_key(session: AsyncSession, user_id: int) -> str:
    """Determine the canonical status key for a user's forum topic title.

    Priority (highest first):
      EDITING  — any pending submission WITH an active lock
      NEW      — any pending submission WITHOUT an active lock
      SCHEDULED — any scheduled submission
      then terminal: PUBLISHED (if any published) > last terminal status by updated_at
    """
    terminal = {"published", "rejected", "cancelled"}

    # Fetch all active (non-terminal) submissions for this user
    active_result = await session.execute(
        select(Submission)
        .where(
            Submission.user_id == user_id,
            Submission.status.notin_(list(terminal)),
        )
        .order_by(Submission.updated_at.desc())
    )
    active_subs = list(active_result.scalars().all())

    pending_ids = [str(s.id) for s in active_subs if s.status == "pending"]

    # Which pending submissions are being edited right now?
    locked_sub_str_ids: set[str] = set()
    if pending_ids:
        now = datetime.now(tz=ZoneInfo("UTC"))
        lock_result = await session.execute(
            select(EditLock.resource_id).where(
                EditLock.resource_type == "submission",
                EditLock.resource_id.in_(pending_ids),
                EditLock.expires_at > now,
            )
        )
        locked_sub_str_ids = set(lock_result.scalars().all())

    has_free_pending = any(
        sub_id not in locked_sub_str_ids for sub_id in pending_ids
    )
    has_editing = bool(locked_sub_str_ids)
    has_scheduled = any(s.status == "scheduled" for s in active_subs)

    if has_editing:
        return "editing"
    if has_free_pending:
        return "pending"
    if has_scheduled:
        return "scheduled"

    # No active submissions — check terminal ones
    terminal_result = await session.execute(
        select(Submission)
        .where(
            Submission.user_id == user_id,
            Submission.status.in_(list(terminal)),
        )
        .order_by(Submission.updated_at.desc())
    )
    terminal_subs = list(terminal_result.scalars().all())

    if not terminal_subs:
        return "pending"  # New user, no submissions yet

    # PUBLISHED takes priority over other terminal statuses
    if any(s.status == "published" for s in terminal_subs):
        return "published"

    return terminal_subs[0].status  # Most recent terminal status


# ── Topic management ───────────────────────────────────────────────

async def ensure_user_topic(bot: Bot, session: AsyncSession, user: User) -> int:
    """Return the forum topic_id for a user, creating it if necessary.

    On creation: posts a welcome system message to the new topic.
    """
    existing = await get_user_topic(session, user.id)
    if existing is not None:
        return existing.topic_id

    # Create a new forum topic
    title = _build_topic_title(user, "pending")
    pending_style = _get_status_style("pending")
    create_kwargs: dict = {"name": title}
    if pending_style.icon_color is not None:
        create_kwargs["icon_color"] = pending_style.icon_color
    if pending_style.icon_custom_emoji_id:
        create_kwargs["icon_custom_emoji_id"] = pending_style.icon_custom_emoji_id
    try:
        topic = await bot.create_forum_topic(
            chat_id=config.moderator_group_id,
            **create_kwargs,
        )
        topic_id: int = topic.message_thread_id
    except Exception:
        logger.exception(
            "Не удалось создать тему форума для пользователя %d", user.id
        )
        raise

    # INSERT with DO NOTHING: if another coroutine already inserted while we
    # were creating the Telegram topic, we lose the race and compensate.
    insert_stmt = (
        pg_insert(UserTopic)
        .values(user_id=user.id, topic_id=topic_id, current_status_key="pending")
        .on_conflict_do_nothing(index_elements=[UserTopic.user_id])
        .returning(UserTopic.topic_id)
    )
    result = await session.execute(insert_stmt)
    inserted_id = result.scalar_one_or_none()
    if inserted_id is None:
        # Another coroutine won the race — clean up the orphaned Telegram topic
        try:
            await bot.delete_forum_topic(
                chat_id=config.moderator_group_id,
                message_thread_id=topic_id,
            )
        except Exception:
            logger.warning(
                "Не удалось удалить дублирующую тему форума %d (пользователь %d)",
                topic_id, user.id,
            )
        existing_after = await get_user_topic(session, user.id)
        if existing_after is not None:
            return existing_after.topic_id
        # Extremely unlikely: row disappeared; re-raise to let caller retry
        raise RuntimeError(
            f"ensure_user_topic: race lost but no topic found for user {user.id}"
        )
    await session.flush()

    # Welcome message in the topic
    display = f"@{user.username}" if user.username else html.escape(user.full_name)
    try:
        await bot.send_message(
            chat_id=config.moderator_group_id,
            message_thread_id=topic_id,
            text=TOPIC_WELCOME_TEXT.format(display=display),
            disable_notification=True,
        )
    except Exception:
        logger.warning(
            "Не удалось отправить приветственное сообщение в тему %d (пользователь %d)",
            topic_id, user.id,
        )

    logger.info(
        "Создана тема форума %d для пользователя %d", topic_id, user.id
    )
    return topic_id


_TITLE_EDIT_MIN_INTERVAL = 10

# Монотонный дедлайн: до него воркер не делает новых вызовов editForumTopic.
# Переждать флуд-контроль через asyncio.sleep внутри тика нельзя: тик длиной в
# retry_after (у editForumTopic это легко минуты) блокирует следующие запуски
# джобы (max_instances=1), и APScheduler на каждом интервале пишет
# "maximum number of running instances reached". Поэтому транзиентная ошибка не
# усыпляет тик, а сдвигает дедлайн — следующий тик просто выходит сразу.
_title_edit_backoff_until: float = 0.0


def _title_edit_backoff_remaining() -> float:
    """Seconds left before the next ``editForumTopic`` call is allowed."""
    return max(0.0, _title_edit_backoff_until - asyncio.get_running_loop().time())


def _defer_title_edits(seconds: float) -> None:
    """Block title edits for ``seconds`` without holding the scheduler tick."""
    global _title_edit_backoff_until
    _title_edit_backoff_until = asyncio.get_running_loop().time() + seconds


async def _edit_forum_topic_once(
    bot: Bot, topic_id: int, name: str, icon_kwargs: dict
) -> bool:
    """Send one ``edit_forum_topic`` call, never sleeping inside the tick.

    Returns ``True`` when Telegram is in the desired state, ``False`` when the
    attempt hit a transient flood/network error and was deferred (the revision
    stays pending and a later tick retries it). Non-transient errors
    (e.g. ``TelegramBadRequest``) propagate.
    """
    try:
        await bot.edit_forum_topic(
            chat_id=config.moderator_group_id,
            message_thread_id=topic_id,
            name=name,
            **icon_kwargs,
        )
        return True
    except TelegramRetryAfter as exc:
        wait_seconds = max(exc.retry_after, _TITLE_EDIT_MIN_INTERVAL)
        _defer_title_edits(wait_seconds)
        logger.info(
            "Флуд-контроль при обновлении заголовка темы %d, следующая попытка через %dс",
            topic_id, wait_seconds,
        )
        return False
    except TelegramNetworkError as exc:
        _defer_title_edits(_TITLE_EDIT_MIN_INTERVAL)
        logger.info(
            "Сетевая ошибка при обновлении заголовка темы %d: %s, следующая попытка через %dс",
            topic_id, exc, _TITLE_EDIT_MIN_INTERVAL,
        )
        return False
    except TelegramBadRequest as exc:
        if "TOPIC_NOT_MODIFIED" in str(exc):
            # Название/иконка уже соответствуют цели — Telegram и так в нужном
            # состоянии, считаем успехом, чтобы вызывающий синхронизировал БД.
            return True
        raise


async def request_topic_title_sync(session: AsyncSession, user_id: int) -> None:
    """Transactionally enqueue a new desired title revision for ``user_id``.

    No Telegram request is made here. The revision commits or rolls back with
    the domain change that requested it, eliminating DB/API split-brain writes.
    """
    await enqueue_topic_title_sync(session, user_id)


@dataclass(frozen=True)
class _ClaimedTitleRevision:
    """A title revision captured from the DB, ready to be sent to Telegram."""

    user_id: int
    topic_id: int
    requested_version: int
    previous_status_key: str
    status_key: str
    title: str
    icon_kwargs: dict


async def _claim_next_title_revision(
    session_factory: async_sessionmaker[AsyncSession],
) -> _ClaimedTitleRevision | None:
    """Drain no-op revisions and return the first one that needs an API call.

    Runs in its own short transaction that is committed before returning: the
    Telegram request must never happen while ``user_topics`` rows are locked.
    """
    async with session_factory() as session:
        claimed: _ClaimedTitleRevision | None = None
        while True:
            result = await session.execute(
                select(User, UserTopic)
                .join(UserTopic, UserTopic.user_id == User.id)
                .where(
                    UserTopic.title_sync_version
                    > UserTopic.title_applied_version
                )
                .order_by(UserTopic.updated_at, UserTopic.user_id)
                .limit(1)
            )
            row = result.first()
            if row is None:
                break

            user, topic = row
            requested_version = topic.title_sync_version
            new_status_key = await compute_topic_status_key(session, user.id)

            force_sync = (
                topic.title_force_sync_version
                > topic.title_applied_version
            )
            if new_status_key == topic.current_status_key and not force_sync:
                await mark_topic_title_sync_applied(
                    session, user.id, requested_version, new_status_key
                )
                continue

            claimed = _ClaimedTitleRevision(
                user_id=user.id,
                topic_id=topic.topic_id,
                requested_version=requested_version,
                previous_status_key=topic.current_status_key,
                status_key=new_status_key,
                title=_build_topic_title(user, new_status_key),
                icon_kwargs=_topic_icon_kwargs(new_status_key),
            )
            break

        await session.commit()
        return claimed


async def process_next_topic_title_sync(
    bot: Bot, session_factory: async_sessionmaker[AsyncSession]
) -> bool:
    """Drain no-op revisions and apply at most one Telegram title edit.

    Split into three phases — claim, call, record — each DB phase in its own
    short transaction, so no row lock is ever held across the Telegram request
    or its retry sleeps. A revision stays pending until the API call succeeds,
    and a newer revision queued mid-flight is left for the next pass because
    ``mark_topic_title_sync_applied`` only advances to the captured version.

    A tick never waits: if the previous one is still running or a transient
    error deferred the next call, it returns immediately instead of piling up
    behind ``max_instances=1``.

    Returns ``True`` when a Telegram edit was applied, otherwise ``False``.
    """
    if _topic_title_sync_lock.locked():
        return False

    async with _topic_title_sync_lock:
        remaining = _title_edit_backoff_remaining()
        if remaining > 0:
            logger.debug(
                "Синхронизация заголовков тем отложена ещё на %.0fс", remaining
            )
            return False

        claimed = await _claim_next_title_revision(session_factory)
        if claimed is None:
            return False

        try:
            applied = await _edit_forum_topic_once(
                bot, claimed.topic_id, claimed.title, claimed.icon_kwargs
            )
        except Exception:
            # Ревизия остаётся pending; дедлайн не даёт долбить Telegram
            # ошибочным запросом чаще раза в _TITLE_EDIT_MIN_INTERVAL.
            _defer_title_edits(_TITLE_EDIT_MIN_INTERVAL)
            logger.warning(
                "Не удалось применить revision=%d заголовка темы %d "
                "для пользователя %d",
                claimed.requested_version,
                claimed.topic_id,
                claimed.user_id,
                exc_info=True,
            )
            return False

        if not applied:
            return False

        async with session_factory() as session:
            await mark_topic_title_sync_applied(
                session,
                claimed.user_id,
                claimed.requested_version,
                claimed.status_key,
            )
            await session.commit()

        logger.debug(
            "Заголовок темы %d синхронизирован: %s → %s (revision=%d)",
            claimed.topic_id,
            claimed.previous_status_key,
            claimed.status_key,
            claimed.requested_version,
        )
        return True


async def reconcile_topic_titles(
    session: AsyncSession,
) -> int:
    """Queue DB-detectable title drift without calling Telegram directly.

    Existing pending revisions are not incremented again, so repeated scans do
    not create an ever-growing backlog. Returns the number of drifted rows.
    """
    result = await session.execute(
        select(User).join(UserTopic, UserTopic.user_id == User.id).order_by(User.id)
    )
    users = list(result.scalars().all())

    corrected = 0
    for user in users:
        topic = await get_user_topic(session, user.id)
        if topic is None:
            continue
        computed = await compute_topic_status_key(session, user.id)

        if computed != topic.current_status_key:
            await ensure_topic_title_sync_pending(session, user.id)
            corrected += 1

    if corrected:
        logger.info(
            "Reconcile заголовков тем: исправлено %d из %d", corrected, len(users)
        )
    return corrected


# ── Card formatting ────────────────────────────────────────────────

_CARD_TERMINAL_STATUSES = frozenset({"published", "rejected", "cancelled"})


async def _resolve_card_lock_owner(
    session: AsyncSession, submission: Submission
) -> User | None:
    """Return the moderator currently holding the edit lock on ``submission``.

    The card must never be rendered from a caller-supplied guess: a handler that
    forgets to pass the owner would repaint a locked post as free (status
    "НОВОЕ" + an active Edit button) while another moderator is still working on
    it. Reading the lock here keeps the card and ``edit_locks`` in one source of
    truth.
    """
    if submission.status in _CARD_TERMINAL_STATUSES:
        return None

    now = datetime.now(tz=ZoneInfo("UTC"))
    result = await session.execute(
        select(EditLock.moderator_id).where(
            EditLock.resource_type == "submission",
            EditLock.resource_id == str(submission.id),
            EditLock.expires_at > now,
        )
    )
    owner_id = result.scalar_one_or_none()
    if owner_id is None:
        return None
    return await get_user_by_id(session, owner_id)


def _card_render_hash(text: str, kb: InlineKeyboardMarkup | None) -> str:
    """Digest the exact card payload sent to Telegram (text + keyboard)."""
    buttons = [
        [(btn.text or "", btn.url or "", btn.callback_data or "") for btn in row]
        for row in (kb.inline_keyboard if kb else [])
    ]
    payload = json.dumps([text, buttons], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _render_submission_card(
    bot: Bot, session: AsyncSession, submission: Submission
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the current target text + keyboard for a submission card.

    Single rendering path for posting, updating, probing and reconciling, so a
    card's stored hash is always comparable with what a later pass computes.
    """
    pub = await get_publication_by_submission(session, submission.id)
    publish_at = pub.publish_at if pub else None
    lock_owner = await _resolve_card_lock_owner(session, submission)

    text = _format_topic_card_text(
        submission, lock_owner=lock_owner, publish_at=publish_at
    )
    kb = topic_submission_card_kb(
        submission.id,
        submission.status,
        # "" still means locked (kb falls back to a generic label); only None
        # means free — a moderator without a username must not unlock the card.
        locked_by=(lock_owner.username or "") if lock_owner else None,
        bot_username=(await bot.me()).username or "",
    )
    return text, kb


def _format_topic_card_text(
    submission: Submission,
    *,
    lock_owner: User | None = None,
    publish_at: datetime | None = None,
) -> str:
    """Format the text card for a submission inside a forum topic."""
    sub = submission
    style = _get_status_style(sub.status)
    emoji = style.emoji
    label = style.label.capitalize()
    tz = ZoneInfo(config.timezone)

    lines: list[str] = []

    if lock_owner is not None:
        lock_display = f"@{lock_owner.username}" if lock_owner.username else html.escape(lock_owner.full_name)
        lines.append(f"✏️ <b>Редактирует:</b> {lock_display}")
        lines.append("")

    lines.append(f"{emoji} <b>Пост #{sub.id}</b>")
    lines.append(f"<b>Статус:</b> {label}")

    if publish_at is not None:
        local_time = publish_at.astimezone(tz)
        lines.append(f"<b>Публикация:</b> {local_time.strftime('%d.%m.%Y %H:%M')}")

    if sub.tags:
        lines.append(f"<b>Теги:</b> {format_tags_line(sub.tags)}")

    if sub.caption:
        lines.append(f"\n<b>Описание:</b>\n{sub.caption}")

    return "\n".join(lines)


# ── Submission card operations ─────────────────────────────────────

async def post_submission_card(
    bot: Bot,
    session: AsyncSession,
    submission: Submission,
) -> tuple[list[int], int]:
    """Post submission media (if any) + text card to the user's forum topic.

    Returns ``(media_message_ids, card_message_id)``.
    Saves IDs to ``submission.topic_*`` columns via query.
    """
    topic_id = await ensure_user_topic(bot, session, submission.user)
    group_id = config.moderator_group_id
    media_ids: list[int] = []

    media_list = submission.media or []
    if media_list:
        try:
            if len(media_list) == 1:
                m = media_list[0]
                send_fn = getattr(bot, MEDIA_SEND_FN.get(m.media_type, "send_document"))
                sent = await send_fn(
                    group_id,
                    m.file_id,
                    message_thread_id=topic_id,
                    disable_notification=True,
                )
                media_ids.append(sent.message_id)
            else:
                group = [
                    MEDIA_INPUT_CLS.get(item.media_type, InputMediaDocument)(media=item.file_id)
                    for item in media_list
                ]
                sent_group = await bot.send_media_group(
                    group_id,
                    group,
                    message_thread_id=topic_id,
                    disable_notification=True,
                )
                media_ids.extend(m.message_id for m in sent_group)
        except Exception:
            logger.exception(
                "Не удалось отправить медиа поста #%d в тему %d",
                submission.id, topic_id,
            )
            raise

    text, kb = await _render_submission_card(bot, session, submission)
    try:
        card_msg = await bot.send_message(
            chat_id=group_id,
            message_thread_id=topic_id,
            text=text,
            reply_markup=kb,
            disable_notification=True,
        )
    except TelegramBadRequest as e:
        if _is_topic_closed(e):
            logger.error(
                "Тема форума %d закрыта при отправке карточки поста #%d, пытаемся открыть",
                topic_id, submission.id,
            )
            if await _try_reopen_topic(bot, topic_id):
                card_msg = await bot.send_message(
                    chat_id=group_id,
                    message_thread_id=topic_id,
                    text=text,
                    reply_markup=kb,
                    disable_notification=True,
                )
            else:
                raise
        else:
            logger.exception(
                "Не удалось отправить карточку поста #%d в тему %d",
                submission.id, topic_id,
            )
            raise
    except Exception:
        logger.exception(
            "Не удалось отправить карточку поста #%d в тему %d",
            submission.id, topic_id,
        )
        raise

    await save_topic_card_ids(session, submission.id, media_ids, card_msg.message_id)
    await mark_card_rendered(session, submission.id, _card_render_hash(text, kb))
    logger.info(
        "Карточка поста #%d размещена в теме %d (медиа: %d шт.)",
        submission.id, topic_id, len(media_ids),
    )
    return media_ids, card_msg.message_id


async def repost_submission_card(
    bot: Bot,
    session: AsyncSession,
    submission: Submission,
) -> tuple[list[int], int]:
    """Delete the submission's existing media+card block in its topic and re-post it.

    Used after a moderator changes the media composition: a Telegram media group
    cannot change its item count in place, so the whole block is re-sent. Side
    effect: the block moves to the bottom of the topic. *submission* must be loaded
    with a fresh `.media` relationship.
    """
    await delete_submission_card(bot, session, submission)
    await session.commit()
    return await post_submission_card(bot, session, submission)


_CARD_GONE_HINTS = ("message to edit not found", "message_id_invalid")


def _is_topic_closed(exc: Exception) -> bool:
    """Return True if the exception indicates the forum topic is closed."""
    return any(hint in str(exc).lower() for hint in _TOPIC_CLOSED_HINTS)


async def _try_reopen_topic(bot: Bot, topic_id: int) -> bool:
    """Attempt to reopen a closed forum topic. Returns True if successful."""
    try:
        await bot.reopen_forum_topic(
            chat_id=config.moderator_group_id,
            message_thread_id=topic_id,
        )
        logger.info("Тема форума %d повторно открыта", topic_id)
        return True
    except Exception:
        logger.error(
            "Не удалось повторно открыть тему форума %d",
            topic_id,
            exc_info=True,
        )
        return False


_CARD_EDIT_MAX_ATTEMPTS = 3
_CARD_EDIT_MAX_WAIT = 30


async def _edit_card_with_retry(
    bot: Bot,
    message_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
    *,
    sub_id: int,
) -> None:
    """Edit a submission card, retrying transient flood/network errors.

    Without this, a single ``TelegramRetryAfter`` (the moderator group shares its
    ``EditMessageText`` quota with the queue board) leaves the card frozen in its
    previous state forever — there is no reconcile pass for cards.

    Returns normally when the card already carries the target content
    ("message is not modified"). Other ``TelegramBadRequest`` errors and a final
    failed retry propagate to the caller.
    """
    for attempt in range(1, _CARD_EDIT_MAX_ATTEMPTS + 1):
        try:
            await bot.edit_message_text(
                text=text,
                chat_id=config.moderator_group_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return  # Already at the target content — success
            raise
        except (TelegramRetryAfter, TelegramNetworkError) as exc:
            wait_seconds = (
                min(exc.retry_after, _CARD_EDIT_MAX_WAIT)
                if isinstance(exc, TelegramRetryAfter)
                else 2
            )
            if attempt == _CARD_EDIT_MAX_ATTEMPTS or (
                isinstance(exc, TelegramRetryAfter)
                and exc.retry_after > _CARD_EDIT_MAX_WAIT
            ):
                raise
            logger.info(
                "Не удалось отредактировать карточку поста #%d (%s), "
                "retry через %dс (попытка %d/%d)",
                sub_id, type(exc).__name__, wait_seconds,
                attempt, _CARD_EDIT_MAX_ATTEMPTS,
            )
            await asyncio.sleep(wait_seconds)


async def probe_submission_card(
    bot: Bot,
    session: AsyncSession,
    submission: Submission,
) -> bool:
    """Check if a submission's topic card is reachable and refresh it.

    Returns True  — card exists (updated or content unchanged).
    Returns False — message is confirmed gone.
    Re-raises TelegramRetryAfter and other non-404 errors so callers can
    distinguish flood control from genuine missing cards.
    """
    text, kb = await _render_submission_card(bot, session, submission)
    try:
        await bot.edit_message_text(
            text=text,
            chat_id=config.moderator_group_id,
            message_id=submission.topic_card_message_id,
            reply_markup=kb,
        )
        await mark_card_rendered(session, submission.id, _card_render_hash(text, kb))
        return True
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "message is not modified" in err:
            await mark_card_rendered(session, submission.id, _card_render_hash(text, kb))
            return True
        if any(hint in err for hint in _CARD_GONE_HINTS):
            return False
        raise  # TOPIC_CLOSED and other non-404 errors — let caller decide
    # TelegramRetryAfter propagates naturally


async def update_submission_card(
    bot: Bot,
    session: AsyncSession,
    submission: Submission,
) -> bool:
    """Edit the text card of a submission in the forum topic. Best-effort.

    The lock indicator is derived from ``edit_locks``, not from the caller, so
    every repaint agrees with the actual lock state.
    """
    if submission.topic_card_message_id is None:
        return True

    existing_topic = await get_user_topic(session, submission.user_id)
    if existing_topic is None:
        return True

    text, kb = await _render_submission_card(bot, session, submission)
    try:
        await _edit_card_with_retry(
            bot, submission.topic_card_message_id, text, kb, sub_id=submission.id
        )
    except TelegramBadRequest as e:
        if _is_topic_closed(e):
            logger.error(
                "Тема форума закрыта при обновлении карточки поста #%d, пытаемся открыть",
                submission.id,
            )
            topic_id = existing_topic.topic_id
            if await _try_reopen_topic(bot, topic_id):
                try:
                    await bot.edit_message_text(
                        text=text,
                        chat_id=config.moderator_group_id,
                        message_id=submission.topic_card_message_id,
                        reply_markup=kb,
                    )
                    await mark_card_rendered(
                        session, submission.id, _card_render_hash(text, kb)
                    )
                    return True
                except Exception:
                    pass
            return False
        logger.warning(
            "Не удалось обновить карточку поста #%d в теме",
            submission.id,
            exc_info=True,
        )
        return False
    except Exception:
        logger.warning(
            "Не удалось обновить карточку поста #%d в теме",
            submission.id,
            exc_info=True,
        )
        return False

    await mark_card_rendered(session, submission.id, _card_render_hash(text, kb))
    return True


async def finalize_submission_card(
    bot: Bot,
    session: AsyncSession,
    submission: Submission,
) -> bool:
    """Remove the keyboard from a submission card (finalize to terminal state). Best-effort."""
    if submission.topic_card_message_id is None:
        return True

    pub = await get_publication_by_submission(session, submission.id)
    publish_at = pub.publish_at if pub else None

    text = _format_topic_card_text(submission, publish_at=publish_at)
    try:
        await _edit_card_with_retry(
            bot, submission.topic_card_message_id, text, None, sub_id=submission.id
        )
    except TelegramBadRequest as e:
        if _is_topic_closed(e):
            logger.error(
                "Тема форума закрыта при финализации карточки поста #%d, пытаемся открыть",
                submission.id,
            )
            existing_topic = await get_user_topic(session, submission.user_id)
            if existing_topic and await _try_reopen_topic(bot, existing_topic.topic_id):
                try:
                    await bot.edit_message_text(
                        text=text,
                        chat_id=config.moderator_group_id,
                        message_id=submission.topic_card_message_id,
                        reply_markup=None,
                    )
                    await mark_card_rendered(
                        session, submission.id, _card_render_hash(text, None)
                    )
                    return True
                except Exception:
                    pass
            return False
        logger.warning(
            "Не удалось финализировать карточку поста #%d",
            submission.id,
            exc_info=True,
        )
        return False
    except Exception:
        logger.warning(
            "Не удалось финализировать карточку поста #%d",
            submission.id,
            exc_info=True,
        )
        return False

    # A terminal card renders with an empty keyboard, which hashes the same as
    # the ``None`` markup sent here — so reconcile sees no drift afterwards.
    await mark_card_rendered(session, submission.id, _card_render_hash(text, None))
    return True


_CARD_RECONCILE_MAX_REPAIRS = 5
_CARD_RECONCILE_DELAY = 2
_CARD_RECONCILE_TERMINAL_WINDOW_HOURS = 48


@dataclass(frozen=True)
class _CardRepair:
    """A drifted card captured from the DB, ready to be repainted."""

    sub_id: int
    message_id: int
    text: str
    markup: InlineKeyboardMarkup | None
    target_hash: str
    original_hash: str | None


async def _collect_card_repairs(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    max_repairs: int,
) -> tuple[list[_CardRepair], int]:
    """Render candidate cards and return those whose content drifted.

    Read-only and committed before any Telegram call: the API requests below
    must not run inside the transaction that later writes ``card_rendered_hash``.
    Returns the repairs plus how many candidates were examined.
    """
    cutoff = datetime.now(tz=ZoneInfo("UTC")) - timedelta(
        hours=_CARD_RECONCILE_TERMINAL_WINDOW_HOURS
    )
    repairs: list[_CardRepair] = []
    async with session_factory() as session:
        candidates = await list_cards_for_reconcile(
            session, stale_terminal_cutoff=cutoff
        )
        for sub in candidates:
            if len(repairs) >= max_repairs:
                break
            if sub.topic_card_message_id is None:
                continue

            text, kb = await _render_submission_card(bot, session, sub)
            is_terminal = sub.status in _CARD_TERMINAL_STATUSES
            markup = None if is_terminal else kb
            target_hash = _card_render_hash(text, markup)
            if target_hash == sub.card_rendered_hash:
                continue

            repairs.append(
                _CardRepair(
                    sub_id=sub.id,
                    message_id=sub.topic_card_message_id,
                    text=text,
                    markup=markup,
                    target_hash=target_hash,
                    original_hash=sub.card_rendered_hash,
                )
            )
        await session.commit()
        return repairs, len(candidates)


async def reconcile_submission_cards(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    max_repairs: int = _CARD_RECONCILE_MAX_REPAIRS,
) -> int:
    """Repaint topic cards whose Telegram content drifted from the DB.

    A card edit that failed (flood control, network outage) leaves the message
    frozen in a stale state — most visibly a processed post still showing as
    "НОВОЕ" with a live Edit button. Unlike topic titles, cards have no
    versioned outbox: the stored ``card_rendered_hash`` is the record of what
    Telegram confirmed, so any mismatch against a freshly computed render is
    drift.

    Drift detection, the API calls and the bookkeeping write are separate
    transactions, so neither the retry sleeps nor the inter-repair delay is ever
    held across a row lock on ``submissions``. Only mismatches hit the API, and
    at most *max_repairs* per pass, so a large backlog drains over several runs
    instead of burning the group's shared ``EditMessageText`` quota in one
    burst. Returns the number of cards fixed.
    """
    repairs, examined = await _collect_card_repairs(bot, session_factory, max_repairs)

    repaired = 0
    for repair in repairs:
        if repaired:
            await asyncio.sleep(_CARD_RECONCILE_DELAY)
        try:
            await _edit_card_with_retry(
                bot,
                repair.message_id,
                repair.text,
                repair.markup,
                sub_id=repair.sub_id,
            )
        except TelegramBadRequest as e:
            err = str(e).lower()
            if any(hint in err for hint in _CARD_GONE_HINTS):
                # The card is gone for good; Recover re-creates it on demand.
                async with session_factory() as session:
                    await clear_topic_card_ids(session, repair.sub_id)
                    await session.commit()
                logger.info(
                    "Карточка поста #%d потеряна в теме, ссылки очищены",
                    repair.sub_id,
                )
                continue
            logger.warning(
                "Reconcile карточек: не удалось починить пост #%d",
                repair.sub_id, exc_info=True,
            )
            continue
        except Exception:
            logger.warning(
                "Reconcile карточек: не удалось починить пост #%d",
                repair.sub_id, exc_info=True,
            )
            continue

        async with session_factory() as session:
            applied = await mark_card_rendered_if_unchanged(
                session, repair.sub_id, repair.target_hash, repair.original_hash
            )
            await session.commit()
        if applied:
            repaired += 1

    if repaired:
        logger.info(
            "Reconcile карточек: починено %d из %d проверенных",
            repaired, examined,
        )
    return repaired


async def _mark_card_outdated(bot: Bot, msg_id: int, sub_id: int) -> None:
    """Flag a stale topic card (>48h, couldn't be deleted) as outdated.

    Rewrites the card text and removes its keyboard. Best-effort: editing our
    own messages has no age limit, but the card may already be gone — log and
    move on either way.
    """
    try:
        await bot.edit_message_text(
            text=TOPIC_CARD_OUTDATED.format(sub_id=sub_id),
            chat_id=config.moderator_group_id,
            message_id=msg_id,
            reply_markup=None,
        )
    except Exception:
        logger.debug(
            "Не удалось пометить устаревшую карточку %d (пост #%d)",
            msg_id, sub_id, exc_info=True,
        )


async def delete_submission_card(
    bot: Bot,
    session: AsyncSession,
    submission: Submission,
) -> bool:
    """Delete all topic messages for a submission (media + card). Best-effort.

    Clears ``topic_*`` ID columns from the submission row on full success only.
    """
    ids_to_delete: list[int] = list(submission.topic_media_message_ids or [])
    if submission.topic_card_message_id is not None:
        ids_to_delete.append(submission.topic_card_message_id)

    if not ids_to_delete:
        return True

    success = True
    for msg_id in ids_to_delete:
        try:
            await bot.delete_message(config.moderator_group_id, msg_id)
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message to delete not found" in err:
                pass  # Already gone — treat as success
            elif "message can't be deleted" in err:
                # Telegram forbids deleting messages older than 48h. The orphaned
                # block stays in the topic, but editing our own messages has no
                # age limit — so flag the stale card as outdated and strip its
                # button so moderators don't act on the leftover copy. Not a
                # failure: the fresh block is posted right after by the caller.
                if msg_id == submission.topic_card_message_id:
                    await _mark_card_outdated(bot, msg_id, submission.id)
                logger.info(
                    "Сообщение %d старше 48ч и не может быть удалено (пост #%d) — "
                    "остаётся в теме как осиротевшее",
                    msg_id, submission.id,
                )
            elif _is_topic_closed(e):
                logger.error(
                    "Тема форума закрыта при удалении сообщения %d (пост #%d), пытаемся открыть",
                    msg_id, submission.id,
                )
                existing_topic = await get_user_topic(session, submission.user_id)
                if existing_topic and await _try_reopen_topic(bot, existing_topic.topic_id):
                    try:
                        await bot.delete_message(config.moderator_group_id, msg_id)
                    except Exception:
                        logger.warning(
                            "Не удалось удалить сообщение %d после открытия темы (пост #%d)",
                            msg_id, submission.id,
                        )
                        success = False
                else:
                    success = False
            else:
                logger.warning(
                    "Не удалось удалить сообщение %d из темы (пост #%d)",
                    msg_id, submission.id,
                    exc_info=True,
                )
                success = False
        except Exception:
            logger.warning(
                "Не удалось удалить сообщение %d из темы (пост #%d)",
                msg_id, submission.id,
                exc_info=True,
            )
            success = False

    if success:
        await clear_topic_card_ids(session, submission.id)
    return success


# ── General topic nav pin ──────────────────────────────────────────

async def ensure_general_topic_nav(bot: Bot, session: AsyncSession) -> None:
    """Ensure the status legend is pinned in the General topic of the moderator group.

    Idempotent: edits the existing message when possible, recreates it if the
    message was deleted. Tracks the message_id in the ``system_messages`` table
    under the key ``general:legend``. Best-effort: logs warnings on failure.

    Uses a process-local asyncio lock to serialise concurrent calls (single-process bot).
    """
    async with _general_topic_nav_lock:
        group_id = config.moderator_group_id
        text = build_topic_nav_legend((await bot.me()).username or "")
        try:
            legend = await get_system_message(session, "general:legend")
            if legend is not None:
                try:
                    await bot.edit_message_text(
                        chat_id=legend.chat_id,
                        message_id=legend.message_id,
                        text=text,
                        disable_web_page_preview=True,
                    )
                    logger.debug("Легенда статусов обновлена (message_id=%d)", legend.message_id)
                    return
                except TelegramAPIError as e:
                    err = str(e).lower()
                    if "message is not modified" in err:
                        logger.debug("Легенда статусов не изменилась")
                        return
                    if "message to edit not found" in err or "message_id_invalid" in err:
                        logger.info(
                            "Легенда не найдена (message_id=%d), создаём заново",
                            legend.message_id,
                        )
                        # Fall through to recreate
                    else:
                        logger.warning("Ошибка при обновлении легенды: %s", e)
                        return

            # Send a new message and pin it
            sent = await bot.send_message(
                group_id, text, disable_notification=True, disable_web_page_preview=True
            )
            await bot.pin_chat_message(group_id, sent.message_id, disable_notification=True)
            await upsert_system_message(session, "general:legend", group_id, sent.message_id)
            logger.info("Легенда статусов создана и закреплена (message_id=%d)", sent.message_id)
        except Exception:
            logger.warning(
                "Не удалось создать/закрепить легенду в General-теме группы",
                exc_info=True,
            )
