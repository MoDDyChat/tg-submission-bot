"""Unit tests for handlers/moderator/recover.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

from handlers.moderator import recover
from tests.helpers import make_bot, make_submission


# ── recover_missing_posts ────────────────────────────────────────────

async def test_recover_skips_alive_cards(monkeypatch) -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=1)
    sub.topic_card_message_id = 99

    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[sub]))
    monkeypatch.setattr(recover.topics, "probe_submission_card", AsyncMock(return_value=True))
    monkeypatch.setattr(recover, "_repost_card", AsyncMock())
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    total, recovered = await recover.recover_missing_posts(bot, session)

    assert total == 1
    assert recovered == 0
    recover._repost_card.assert_not_awaited()


async def test_recover_reposts_when_card_missing(monkeypatch) -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=2)
    sub.topic_card_message_id = 50

    repost = AsyncMock()
    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[sub]))
    monkeypatch.setattr(recover.topics, "probe_submission_card", AsyncMock(return_value=False))
    monkeypatch.setattr(recover, "clear_topic_card_ids", AsyncMock())
    monkeypatch.setattr(recover, "_repost_card", repost)
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    total, recovered = await recover.recover_missing_posts(bot, session)

    assert total == 1
    assert recovered == 1
    repost.assert_awaited_once_with(bot, session, sub)


async def test_recover_posts_when_no_card_id(monkeypatch) -> None:
    """Submission with no topic_card_message_id is always reposted."""
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=3)
    sub.topic_card_message_id = None

    repost = AsyncMock()
    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[sub]))
    monkeypatch.setattr(recover, "_repost_card", repost)
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    total, recovered = await recover.recover_missing_posts(bot, session)

    assert total == 1
    assert recovered == 1


async def test_recover_handles_exception_gracefully(monkeypatch) -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=4)
    sub.topic_card_message_id = None

    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[sub]))
    monkeypatch.setattr(recover, "_repost_card", AsyncMock(side_effect=Exception("network fail")))
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    # Should not raise
    total, recovered = await recover.recover_missing_posts(bot, session)

    assert total == 1
    assert recovered == 0


async def test_recover_returns_zero_when_no_submissions(monkeypatch) -> None:
    bot = make_bot()
    session = AsyncMock()

    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[]))

    total, recovered = await recover.recover_missing_posts(bot, session)

    assert total == 0
    assert recovered == 0


# ── _repost_card ──────────────────────────────────────────────────────

async def test_repost_card_recreates_topic_when_deleted(monkeypatch) -> None:
    bot = make_bot()
    session = AsyncMock()
    sub = make_submission(sub_id=5)
    sub.user_id = 99

    err = TelegramBadRequest(method=None, message="Message thread not found")  # type: ignore[arg-type]
    post_card = AsyncMock(side_effect=[err, None])
    delete_topic = AsyncMock()

    monkeypatch.setattr(recover.topics, "post_submission_card", post_card)
    monkeypatch.setattr(recover, "delete_user_topic", delete_topic)

    await recover._repost_card(bot, session, sub)

    assert post_card.await_count == 2
    delete_topic.assert_awaited_once_with(session, sub.user_id)
