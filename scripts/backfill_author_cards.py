"""Retrofit the author card onto the welcome message of pre-existing forum topics.

New topics open with the author card (``services/topics.ensure_user_topic``).
Topics created before that opened with a plain welcome message whose
``message_id`` was never stored, and the Bot API cannot read chat history — so
this script has to *find* that message and prove it found the right one before
editing anything.

How a candidate is found and verified:

1. A forum topic's ``message_thread_id`` equals the ``message_id`` of its
   ``forum_topic_created`` service message, and the welcome message was sent
   immediately after — so the candidate window starts at ``topic_id + 1``.
2. The window is capped by ``--window`` and by the lowest message id we already
   know belongs to that topic (submission cards, their media, the standalone
   author card): the welcome predates all of them.
3. Every known id is skipped outright — the script never probes a message it
   can already identify as something else.
4. Each remaining candidate is **forwarded** to a private verification chat and
   read back (``forwardMessage`` is the only read primitive in the Bot API),
   then the forwarded copy is deleted. The original is edited only if the text
   matches the welcome template's static parts *and* the message was sent by
   this bot.

Anything unproven is left untouched. Two outcomes are distinguished: if the
probe finds nothing welcome-like at all, the topic simply never got a welcome
message (a handful of old ones didn't) and a fresh card is posted and pinned —
that overwrites nothing, and `--no-create` turns it off. If it finds a
welcome-looking message that does not mention this author, the window may have
drifted into a neighbouring topic and the topic is skipped. Re-runs are safe — a
converted card carries ``payload.opening`` and is skipped on the next pass.

Usage (inside the bot container, e.g. ``docker compose exec sm_bot ...``):

    python scripts/backfill_author_cards.py --dry-run --limit 1   # probe one topic
    python scripts/backfill_author_cards.py --limit 1             # convert one topic
    python scripts/backfill_author_cards.py                       # convert everything

The bot keeps running while this works, and it shares the moderator group's
Telegram quota with the queue board and the dashboard. Each converted topic
costs two group calls (edit + pin), so ``--delay`` (default 8s per topic) keeps
the script's share around 15 group calls per minute — under the ~20/min group
limit even with the bot's own traffic on top. Lower it only on an idle bot.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import config
from core.logging import create_logger, get_logger
from core.messages import TOPIC_WELCOME_TEXT
from db.models import Submission, SystemMessage, User, UserTopic
from db.queries import get_system_message, upsert_system_message
from db.session import session_factory, shutdown_db
from keyboards.moderator import author_card_kb
from services.author_card import (
    _checksum,
    build_author_card,
    card_key,
    create_author_card_message,
)

logger = get_logger(__name__)

# Static parts of the welcome template, i.e. everything around ``{display}``.
# Matching on these (not on the whole rendered string) keeps the check working
# after a rename: the display name itself is the one part that may have changed.
# Punctuation next to the placeholder is trimmed as well — the template has been
# edited over the years (an early version had no colon after "Тема пользователя"),
# and messages sent back then must still be recognised.
_MARKER_TRIM = " \t\n:—-–,"

WELCOME_MARKERS: tuple[str, ...] = tuple(
    stripped
    for part in TOPIC_WELCOME_TEXT.split("{display}")
    if (stripped := part.strip(_MARKER_TRIM))
)


def is_welcome_text(text: str | None) -> bool:
    """True when *text* is a rendered ``TOPIC_WELCOME_TEXT``."""
    if not text or not WELCOME_MARKERS:
        return False
    return all(marker in text for marker in WELCOME_MARKERS)


def sent_by_bot(forwarded: Message, bot_id: int) -> bool:
    """Whether the forwarded copy's origin is this bot.

    Advisory second opinion on top of the text match: origin metadata can be
    hidden (``MessageOriginHiddenUser``), in which case we do not object —
    ``editMessageText`` would fail anyway on a message the bot does not own.
    """
    origin = getattr(forwarded, "forward_origin", None)
    sender = getattr(origin, "sender_user", None) if origin is not None else None
    if sender is None:
        sender = getattr(forwarded, "forward_from", None)
    return sender is None or sender.id == bot_id


async def with_retry(factory, *, attempts: int = 3):
    """Await ``factory()``, honouring ``TelegramRetryAfter`` (flood control)."""
    for attempt in range(1, attempts + 1):
        try:
            return await factory()
        except TelegramRetryAfter as e:
            if attempt == attempts:
                raise
            logger.warning("Flood control: ждём %d с", e.retry_after + 1)
            await asyncio.sleep(e.retry_after + 1)
    raise RuntimeError("unreachable")


async def known_message_ids(session: AsyncSession, user_id: int) -> set[int]:
    """Message ids in this user's topic that are known to be something else."""
    known: set[int] = set()

    result = await session.execute(
        select(Submission.topic_card_message_id, Submission.topic_media_message_ids)
        .where(Submission.user_id == user_id)
    )
    for card_id, media_ids in result.all():
        if card_id:
            known.add(card_id)
        if media_ids:
            known.update(int(mid) for mid in media_ids)

    card = await get_system_message(session, card_key(user_id))
    if card is not None:
        known.add(card.message_id)
    return known


async def another_topic_created_before(
    session: AsyncSession, topic_id: int, candidate: int
) -> bool:
    """Was another forum topic created between this topic and *candidate*?

    A topic's ``topic_id`` is the ``message_id`` of its ``forum_topic_created``
    service message, so topics and their welcome messages share one id sequence.
    If no other topic was created in ``(topic_id, candidate)``, no other topic's
    welcome message can occupy that id — the candidate is ours even when its text
    names someone the author no longer is. If one was, the ids interleave and
    only the display name can tell the two apart.
    """
    result = await session.execute(
        select(UserTopic.topic_id).where(
            UserTopic.topic_id > topic_id, UserTopic.topic_id < candidate
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def peek_message_text(
    bot: Bot, group_id: int, message_id: int, verify_chat_id: int
) -> tuple[str | None, bool]:
    """Read a message's text by forwarding it away and deleting the copy.

    Returns ``(text, from_this_bot)``; ``(None, False)`` when the message does
    not exist or cannot be forwarded.
    """
    try:
        forwarded = await with_retry(lambda: bot.forward_message(
            chat_id=verify_chat_id,
            from_chat_id=group_id,
            message_id=message_id,
            disable_notification=True,
        ))
    except TelegramAPIError as e:
        logger.debug("message_id=%d переслать не удалось: %s", message_id, e)
        return None, False

    text = forwarded.text or forwarded.caption
    ours = sent_by_bot(forwarded, bot.id)

    try:
        await bot.delete_message(chat_id=verify_chat_id, message_id=forwarded.message_id)
    except TelegramAPIError as e:
        logger.warning(
            "Не удалось удалить проверочную копию message_id=%d: %s", forwarded.message_id, e
        )
    return text, ours


async def find_welcome_message(
    bot: Bot,
    session: AsyncSession,
    user: User,
    topic_id: int,
    *,
    verify_chat_id: int,
    window: int,
    probe_delay: float,
    allow_display_mismatch: bool = False,
) -> tuple[int | None, str]:
    """Locate the welcome message of a topic.

    Returns ``(message_id, "found")``, or ``(None, reason)`` where reason is
    ``absent`` (nothing in the window looks like this topic's welcome — it was
    deleted or the topic predates the welcome message) or ``ambiguous`` (a
    welcome-looking message that cannot be proven to belong to this topic). The
    caller may post a fresh card for ``absent`` but must leave ``ambiguous``
    topics alone.

    A welcome that does not mention the author is not automatically rejected:
    people rename themselves, and their old welcome message keeps the old name.
    It is only rejected when another topic was created between this one and the
    candidate — which is the only way the candidate could belong to a different
    topic (see ``another_topic_created_before``).
    """
    group_id = config.moderator_group_id
    known = await known_message_ids(session, user.id)
    # The welcome text was sent with parse_mode=HTML, so a forwarded copy reads
    # back *rendered*: ``html.escape``'d characters are plain again. Accept both
    # forms — the raw name is what a match normally looks like.
    if user.username:
        display_variants = (f"@{user.username}",)
    else:
        display_variants = (user.full_name, html.escape(user.full_name))
    display = display_variants[0]

    lo = topic_id + 1
    hi = lo + window - 1
    if known:
        # The welcome message precedes everything else we know about the topic.
        hi = min(hi, min(known) - 1)
    if hi < lo:
        return None, "absent"

    for candidate in range(lo, hi + 1):
        if candidate in known:
            continue
        text, ours = await peek_message_text(bot, group_id, candidate, verify_chat_id)
        await asyncio.sleep(probe_delay)
        if text is None:
            continue
        if not (is_welcome_text(text) and ours):
            logger.debug(
                "message_id=%d не приветствие (наше=%s): %r",
                candidate, ours, text[:60],
            )
            continue
        matches_display = any(variant in text for variant in display_variants)
        if not matches_display and not allow_display_mismatch:
            if await another_topic_created_before(session, topic_id, candidate):
                logger.warning(
                    "user_id=%d (topic %d): приветствие message_id=%d не упоминает %s, "
                    "и между темами есть чужая — пропускаем",
                    user.id, topic_id, candidate, display,
                )
                return None, "ambiguous"
            logger.info(
                "user_id=%d (topic %d): приветствие message_id=%d не упоминает %s "
                "(скорее всего автор сменил ник) — тема между ними не создавалась, берём",
                user.id, topic_id, candidate, display,
            )
        return candidate, "found"
    return None, "absent"


async def drop_standalone_card(
    bot: Bot, existing: SystemMessage | None, new_message_id: int
) -> None:
    """Remove the standalone card a topic may have got before the card moved up front."""
    if existing is None or existing.message_id == new_message_id:
        return
    for action in (
        lambda: bot.unpin_chat_message(chat_id=existing.chat_id, message_id=existing.message_id),
        lambda: bot.delete_message(chat_id=existing.chat_id, message_id=existing.message_id),
    ):
        try:
            await with_retry(action)
        except TelegramAPIError as e:
            logger.warning(
                "Не удалось убрать старую карточку message_id=%d: %s", existing.message_id, e
            )


async def convert_topic(
    bot: Bot,
    session: AsyncSession,
    user: User,
    topic_id: int,
    *,
    verify_chat_id: int,
    window: int,
    probe_delay: float,
    dry_run: bool,
    allow_display_mismatch: bool = False,
    create_missing: bool = True,
) -> str:
    """Give one topic its author card.

    Normally the topic's welcome message is edited into the card and pinned. A
    handful of old topics never got a welcome message; for those, and only when
    the probe proves nothing welcome-like is there (``absent``), a fresh card is
    posted and pinned instead — that cannot overwrite anything. An ``ambiguous``
    result is always left alone.

    Returns a one-word outcome: ``converted`` / ``created`` / ``skipped`` /
    ``not_found``.
    """
    existing = await get_system_message(session, card_key(user.id))
    if existing is not None and existing.payload and existing.payload.get("opening"):
        return "skipped"  # Already the topic's opening message.

    welcome_id, reason = await find_welcome_message(
        bot, session, user, topic_id,
        verify_chat_id=verify_chat_id, window=window, probe_delay=probe_delay,
        allow_display_mismatch=allow_display_mismatch,
    )
    if welcome_id is None:
        if reason != "absent" or not create_missing:
            logger.warning(
                "user_id=%d (topic %d): приветствие не найдено (%s) — тема пропущена",
                user.id, topic_id, reason,
            )
            return "not_found"

        # No welcome message ever existed here — post the card instead. Nothing
        # is overwritten, so this needs no verification beyond the empty probe.
        if dry_run:
            logger.info(
                "[dry-run] user_id=%d (topic %d): приветствия нет — была бы отправлена "
                "новая карточка", user.id, topic_id,
            )
            return "created"

        new_id = await with_retry(
            lambda: create_author_card_message(bot, session, user, topic_id)
        )
        await session.commit()
        await drop_standalone_card(bot, existing, new_id)
        logger.info(
            "user_id=%d (topic %d): карточка создана заново (message_id=%d)",
            user.id, topic_id, new_id,
        )
        return "created"

    if dry_run:
        logger.info(
            "[dry-run] user_id=%d (topic %d): приветствие message_id=%d → карточка"
            "%s",
            user.id, topic_id, welcome_id,
            f", старая карточка message_id={existing.message_id} была бы удалена"
            if existing is not None else "",
        )
        return "converted"

    group_id = config.moderator_group_id
    text = await build_author_card(session, user)
    reply_markup = author_card_kb(user)

    await with_retry(lambda: bot.edit_message_text(
        chat_id=group_id,
        message_id=welcome_id,
        text=text,
        parse_mode="HTML",
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    ))
    try:
        await with_retry(lambda: bot.pin_chat_message(
            chat_id=group_id, message_id=welcome_id, disable_notification=True
        ))
    except TelegramAPIError as e:
        logger.warning("Не удалось закрепить карточку user_id=%d: %s", user.id, e)

    await upsert_system_message(
        session,
        card_key(user.id),
        group_id,
        welcome_id,
        payload={"checksum": _checksum(text, reply_markup), "opening": True},
    )
    await session.commit()
    await drop_standalone_card(bot, existing, welcome_id)

    logger.info(
        "user_id=%d (topic %d): карточка перенесена в message_id=%d",
        user.id, topic_id, welcome_id,
    )
    return "converted"


async def load_targets(
    session: AsyncSession, *, user_id: int | None, limit: int | None
) -> list[tuple[User, int]]:
    """Users with a forum topic that has no opening card yet, oldest topic first.

    Already-converted topics are filtered out *before* ``--limit`` is applied,
    so a probe run of ``--limit 1`` always gets a topic with work to do.
    """
    stmt = (
        select(User, UserTopic.topic_id)
        .join(UserTopic, UserTopic.user_id == User.id)
        .order_by(UserTopic.topic_id.asc())
    )
    if user_id is not None:
        stmt = stmt.where(User.id == user_id)
    result = await session.execute(stmt)
    rows = [(row[0], row[1]) for row in result.all()]

    cards = await session.execute(
        select(SystemMessage.key, SystemMessage.payload).where(
            SystemMessage.key.like("user:%:card")
        )
    )
    converted = {key for key, payload in cards.all() if payload and payload.get("opening")}

    targets = [(user, topic_id) for user, topic_id in rows if card_key(user.id) not in converted]
    return targets[:limit] if limit is not None else targets


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="только найти и показать приветствия, ничего не менять")
    parser.add_argument("--limit", type=int, default=None,
                        help="обработать не больше N тем (для пробного запуска)")
    parser.add_argument("--user-id", type=int, default=None,
                        help="обработать только одного пользователя (внутренний users.id)")
    parser.add_argument("--verify-chat", type=int, default=None,
                        help="chat_id для проверочных пересылок (по умолчанию — первый ADMIN_IDS)")
    parser.add_argument("--window", type=int, default=5,
                        help="сколько message_id после создания темы просматривать (по умолчанию 5)")
    parser.add_argument("--delay", type=float, default=8.0,
                        help="пауза между темами в секундах (по умолчанию 8)")
    parser.add_argument("--probe-delay", type=float, default=1.5,
                        help="пауза между проверочными пересылками (по умолчанию 1.5)")
    parser.add_argument("--no-create", action="store_true",
                        help="не создавать карточку в темах без приветствия (только редактировать)")
    parser.add_argument("--allow-display-mismatch", action="store_true",
                        help="принимать приветствие, даже если оно не упоминает "
                             "текущего пользователя (для случаев переименования)")
    args = parser.parse_args()

    create_logger(config.log_level)

    from core.topic_status_config import _init as _init_topic_status_config
    _init_topic_status_config(config.topic_statuses_path)

    verify_chat_id = args.verify_chat or (config.admin_ids[0] if config.admin_ids else None)
    if verify_chat_id is None:
        parser.error("не задан --verify-chat, а ADMIN_IDS пуст")

    bot = Bot(
        token=config.bot.token,
        session=AiohttpSession(proxy=config.proxy_url) if config.proxy_url else AiohttpSession(),
        default=DefaultBotProperties(parse_mode="HTML"),
    )

    stats = {"converted": 0, "created": 0, "skipped": 0, "not_found": 0, "failed": 0}
    try:
        async with session_factory() as session:
            targets = await load_targets(session, user_id=args.user_id, limit=args.limit)
        logger.info(
            "К обработке тем: %d (dry-run=%s, проверочный чат=%d)",
            len(targets), args.dry_run, verify_chat_id,
        )

        for index, (user, topic_id) in enumerate(targets):
            if index > 0:
                await asyncio.sleep(args.delay)
            async with session_factory() as session:
                try:
                    outcome = await convert_topic(
                        bot, session, user, topic_id,
                        verify_chat_id=verify_chat_id,
                        window=args.window,
                        probe_delay=args.probe_delay,
                        dry_run=args.dry_run,
                        allow_display_mismatch=args.allow_display_mismatch,
                        create_missing=not args.no_create,
                    )
                except Exception:
                    logger.exception("user_id=%d (topic %d): ошибка", user.id, topic_id)
                    outcome = "failed"
            stats[outcome] += 1
    finally:
        await bot.session.close()
        await shutdown_db()

    logger.info(
        "Готово: перенесено %d, создано %d, пропущено %d, не найдено %d, ошибок %d",
        stats["converted"], stats["created"], stats["skipped"],
        stats["not_found"], stats["failed"],
    )
    if stats["failed"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
