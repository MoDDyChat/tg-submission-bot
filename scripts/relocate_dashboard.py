"""Move the General-topic dashboard onto an existing message of this bot.

One-off maintenance tool for the legend merge: the stats summary, the status
legend and the command list now live in a single message (``general:stats``),
and this script lets that message be the *old* legend message instead of the
newer dashboard one — so the General topic keeps one pin and no leftovers.

It renders the current dashboard text into the target message, re-points the
``general:stats`` row at it, pins it, and deletes the message the row pointed
at before. Idempotent: pointing it at the message it already uses only
re-renders. The bot may keep running — it re-reads ``general:stats`` on every
render pass, so it follows the move on its next tick.

Usage (inside the bot container):

    python scripts/relocate_dashboard.py --to 967
    python scripts/relocate_dashboard.py --to 967 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError

from core.config import config
from core.logging import create_logger, get_logger
from core.topic_status_config import build_topic_nav_legend
from db.queries import get_system_message, upsert_system_message
from db.session import session_factory, shutdown_db
from services.dashboard import _build_dashboard_text, _checksum

logger = get_logger(__name__)

_DASHBOARD_KEY = "general:stats"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", type=int, required=True,
                        help="message_id, который должен стать сообщением сводки")
    parser.add_argument("--dry-run", action="store_true",
                        help="только показать, что будет сделано")
    args = parser.parse_args()

    create_logger(config.log_level)

    from core.topic_status_config import _init as _init_topic_status_config
    _init_topic_status_config(config.topic_statuses_path)

    bot = Bot(
        token=config.bot.token,
        session=AiohttpSession(proxy=config.proxy_url) if config.proxy_url else AiohttpSession(),
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    group_id = config.moderator_group_id

    try:
        async with session_factory() as session:
            existing = await get_system_message(session, _DASHBOARD_KEY)
            legend = build_topic_nav_legend((await bot.me()).username or "")
            text = await _build_dashboard_text(session, legend)

        old_id = existing.message_id if existing is not None else None
        logger.info("Сводка: message_id=%s → %d", old_id, args.to)
        if args.dry_run:
            logger.info("[dry-run] ничего не меняю")
            return

        await bot.edit_message_text(
            chat_id=group_id,
            message_id=args.to,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        try:
            await bot.pin_chat_message(group_id, args.to, disable_notification=True)
        except TelegramAPIError as e:
            logger.warning("Не удалось закрепить сводку: %s", e)

        async with session_factory() as session:
            await upsert_system_message(
                session, _DASHBOARD_KEY, group_id, args.to, payload={"checksum": _checksum(text)}
            )
            await session.commit()
        logger.info("general:stats теперь указывает на message_id=%d", args.to)

        if old_id is not None and old_id != args.to:
            try:
                await bot.delete_message(chat_id=group_id, message_id=old_id)
                logger.info("Старое сообщение сводки удалено (message_id=%d)", old_id)
            except TelegramAPIError as e:
                logger.warning(
                    "Не удалось удалить старое сообщение сводки (message_id=%d): %s", old_id, e
                )
    finally:
        await bot.session.close()
        await shutdown_db()


if __name__ == "__main__":
    asyncio.run(main())
