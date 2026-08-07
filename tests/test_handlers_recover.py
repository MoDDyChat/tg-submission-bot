"""Unit tests for handlers/moderator/recover.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

from handlers.moderator import recover
from tests.helpers import FakeSessionFactory, make_bot, make_submission


# ── recover_missing_posts ────────────────────────────────────────────

async def test_recover_skips_alive_cards(monkeypatch) -> None:
    bot = make_bot()
    sub = make_submission(sub_id=1)
    sub.topic_card_message_id = 99

    probe_session = AsyncMock()

    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[sub]))
    monkeypatch.setattr(recover.topics, "probe_submission_card", AsyncMock(return_value=True))
    monkeypatch.setattr(recover, "clear_topic_card_ids", AsyncMock())
    monkeypatch.setattr(recover, "_repost_card", AsyncMock())
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    factory = FakeSessionFactory(AsyncMock(), probe_session)
    total, recovered = await recover.recover_missing_posts(bot, factory)

    assert total == 1
    assert recovered == 0
    recover._repost_card.assert_not_awaited()
    recover.clear_topic_card_ids.assert_not_awaited()
    # The refreshed-card hash write is committed in its own short transaction.
    probe_session.commit.assert_awaited_once()


async def test_recover_reposts_when_card_missing(monkeypatch) -> None:
    """Stale card IDs are cleared and committed before the Telegram call."""
    bot = make_bot()
    sub = make_submission(sub_id=2)
    sub.topic_card_message_id = 50

    probe_session = AsyncMock()
    clear_session = AsyncMock()

    repost = AsyncMock()
    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[sub]))
    monkeypatch.setattr(recover.topics, "probe_submission_card", AsyncMock(return_value=False))
    monkeypatch.setattr(recover, "clear_topic_card_ids", AsyncMock())
    monkeypatch.setattr(recover, "_repost_card", repost)
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    factory = FakeSessionFactory(AsyncMock(), probe_session, clear_session)
    total, recovered = await recover.recover_missing_posts(bot, factory)

    assert total == 1
    assert recovered == 1
    recover.clear_topic_card_ids.assert_awaited_once_with(clear_session, sub.id)
    clear_session.commit.assert_awaited_once()
    # The Telegram call happens without an open DB transaction: the repost
    # receives the session factory, never a live session.
    repost.assert_awaited_once_with(bot, factory, sub)


async def test_recover_posts_when_no_card_id(monkeypatch) -> None:
    """Submission with no topic_card_message_id is always reposted."""
    bot = make_bot()
    sub = make_submission(sub_id=3)
    sub.topic_card_message_id = None

    repost = AsyncMock()
    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[sub]))
    monkeypatch.setattr(recover, "_repost_card", repost)
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    factory = FakeSessionFactory(AsyncMock())
    total, recovered = await recover.recover_missing_posts(bot, factory)

    assert total == 1
    assert recovered == 1
    repost.assert_awaited_once_with(bot, factory, sub)


async def test_recover_handles_exception_gracefully(monkeypatch) -> None:
    bot = make_bot()
    sub = make_submission(sub_id=4)
    sub.topic_card_message_id = None

    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[sub]))
    monkeypatch.setattr(recover, "_repost_card", AsyncMock(side_effect=Exception("network fail")))
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    # Should not raise
    factory = FakeSessionFactory(AsyncMock())
    total, recovered = await recover.recover_missing_posts(bot, factory)

    assert total == 1
    assert recovered == 0


async def test_recover_returns_zero_when_no_submissions(monkeypatch) -> None:
    bot = make_bot()

    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[]))

    factory = FakeSessionFactory(AsyncMock())
    total, recovered = await recover.recover_missing_posts(bot, factory)

    assert total == 0
    assert recovered == 0


async def test_recover_sends_no_admin_audit(monkeypatch) -> None:
    """recover_missing_posts must not DM admins — audit is the handler's job."""
    bot = make_bot()
    sub = make_submission(sub_id=6)
    sub.topic_card_message_id = None

    notify = AsyncMock()
    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[sub]))
    monkeypatch.setattr(recover, "_repost_card", AsyncMock())
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        recover, "admin_notifications",
        SimpleNamespace(notify_admins=notify),
        raising=False,
    )

    factory = FakeSessionFactory(AsyncMock())
    total, recovered = await recover.recover_missing_posts(bot, factory)

    assert total == 1
    assert recovered == 1
    notify.assert_not_awaited()
    bot.send_message.assert_not_awaited()


# ── _repost_card ──────────────────────────────────────────────────────

async def test_repost_card_recreates_topic_when_deleted(monkeypatch) -> None:
    bot = make_bot()
    sub = make_submission(sub_id=5)
    sub.user_id = 99

    err = TelegramBadRequest(method=None, message="Message thread not found")  # type: ignore[arg-type]
    post_card = AsyncMock(side_effect=[err, None])
    delete_topic = AsyncMock()
    ensure_topic = AsyncMock()

    monkeypatch.setattr(recover.topics, "post_submission_card", post_card)
    monkeypatch.setattr(recover, "delete_user_topic", delete_topic)
    monkeypatch.setattr(recover.topics, "ensure_user_topic", ensure_topic)

    ensure_session = AsyncMock()
    failed_session = AsyncMock()
    delete_session = AsyncMock()
    ensure_retry_session = AsyncMock()
    retry_session = AsyncMock()
    factory = FakeSessionFactory(
        ensure_session, failed_session, delete_session, ensure_retry_session, retry_session,
    )

    await recover._repost_card(bot, factory, sub)

    assert post_card.await_count == 2
    delete_topic.assert_awaited_once_with(delete_session, sub.user_id)
    # The UserTopic INSERT (ensure_user_topic) always lands in its own
    # committed transaction, never held open across the Telegram calls inside
    # post_submission_card — both before the first attempt and on recreate.
    assert ensure_topic.await_count == 2
    ensure_topic.assert_any_await(bot, ensure_session, sub.user)
    ensure_topic.assert_any_await(bot, ensure_retry_session, sub.user)
    ensure_session.commit.assert_awaited_once()
    ensure_retry_session.commit.assert_awaited_once()
    # The failed first attempt rolls back without a commit.
    failed_session.commit.assert_not_awaited()
    delete_session.commit.assert_awaited_once()
    retry_session.commit.assert_awaited_once()


async def test_repost_card_commits_successful_post(monkeypatch) -> None:
    bot = make_bot()
    sub = make_submission(sub_id=7)
    sub.topic_card_message_id = None

    post_card = AsyncMock(return_value=([], 1001))
    ensure_topic = AsyncMock()
    monkeypatch.setattr(recover.topics, "post_submission_card", post_card)
    monkeypatch.setattr(recover.topics, "ensure_user_topic", ensure_topic)

    ensure_session = AsyncMock()
    post_session = AsyncMock()
    factory = FakeSessionFactory(ensure_session, post_session)

    await recover._repost_card(bot, factory, sub)

    post_card.assert_awaited_once_with(bot, post_session, sub)
    post_session.commit.assert_awaited_once()
    ensure_topic.assert_awaited_once_with(bot, ensure_session, sub.user)
    ensure_session.commit.assert_awaited_once()
