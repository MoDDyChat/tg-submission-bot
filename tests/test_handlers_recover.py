"""Unit tests for handlers/moderator/recover.py."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from handlers.moderator import recover
from tests.helpers import FakeSessionFactory, make_bot, make_media, make_submission


# ── recover_missing_posts ────────────────────────────────────────────

async def test_recover_skips_alive_cards(monkeypatch) -> None:
    bot = make_bot()
    sub = make_submission(sub_id=1)
    sub.topic_card_message_id = 99

    probe_session = AsyncMock()

    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[sub]))
    monkeypatch.setattr(recover.topics, "probe_submission_card", AsyncMock(return_value=True))
    monkeypatch.setattr(recover, "clear_topic_card_ids_if_unchanged", AsyncMock())
    monkeypatch.setattr(recover, "_repost_card", AsyncMock())
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    factory = FakeSessionFactory(AsyncMock(), probe_session)
    total, recovered = await recover.recover_missing_posts(bot, factory)

    assert total == 1
    assert recovered == 0
    recover._repost_card.assert_not_awaited()
    recover.clear_topic_card_ids_if_unchanged.assert_not_awaited()
    # The refreshed-card hash write is committed in its own short transaction.
    probe_session.commit.assert_awaited_once()


async def test_recover_reposts_when_card_missing(monkeypatch) -> None:
    """Stale card IDs are cleared and committed before the Telegram call."""
    bot = make_bot()
    sub = make_submission(sub_id=2)
    sub.topic_card_message_id = 50

    probe_session = AsyncMock()
    clear_session = AsyncMock()
    fresh = make_submission(sub_id=2)

    repost = AsyncMock()
    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[sub]))
    monkeypatch.setattr(recover.topics, "probe_submission_card", AsyncMock(return_value=False))
    clear = AsyncMock(return_value=True)
    monkeypatch.setattr(recover, "clear_topic_card_ids_if_unchanged", clear)
    monkeypatch.setattr(recover, "get_submission_with_user", AsyncMock(return_value=fresh))
    monkeypatch.setattr(recover, "_repost_card", repost)
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    factory = FakeSessionFactory(AsyncMock(), probe_session, clear_session)
    total, recovered = await recover.recover_missing_posts(bot, factory)

    assert total == 1
    assert recovered == 1
    # Compare-and-swap on the probed card ID, not a blind clear by post ID.
    clear.assert_awaited_once_with(clear_session, sub.id, 50)
    clear_session.commit.assert_awaited_once()
    # The Telegram call happens without an open DB transaction: the repost
    # receives the session factory, never a live session.
    repost.assert_awaited_once_with(bot, factory, fresh)


async def test_recover_skips_post_whose_card_was_replaced_meanwhile(monkeypatch) -> None:
    """Замена карточки закоммичена между пробой и очисткой → ничего не трогаем."""
    bot = make_bot()
    sub = make_submission(sub_id=9)
    sub.topic_card_message_id = 50  # card X, already deleted by the media handler

    repost = AsyncMock()
    # The CAS loses: the row now holds the replacement card Y.
    clear = AsyncMock(return_value=False)
    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[sub]))
    monkeypatch.setattr(recover.topics, "probe_submission_card", AsyncMock(return_value=False))
    monkeypatch.setattr(recover, "clear_topic_card_ids_if_unchanged", clear)
    monkeypatch.setattr(recover, "_repost_card", repost)
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    factory = FakeSessionFactory(AsyncMock())
    total, recovered = await recover.recover_missing_posts(bot, factory)

    assert total == 1
    assert recovered == 0
    # Y stays recorded and is not duplicated by a second card.
    clear.assert_awaited_once()
    repost.assert_not_awaited()


async def test_recover_missing_reposts_the_freshly_read_post(monkeypatch) -> None:
    """Модератор дописал медиа до восстановления → шлём свежий состав, не снимок батча."""
    bot = make_bot()
    stale = make_submission(sub_id=10, media=[make_media(media_id=1, submission_id=10)])
    stale.topic_card_message_id = 50
    fresh = make_submission(
        sub_id=10,
        media=[make_media(media_id=1, submission_id=10), make_media(media_id=2, submission_id=10)],
    )

    repost = AsyncMock()
    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[stale]))
    monkeypatch.setattr(recover.topics, "probe_submission_card", AsyncMock(return_value=False))
    monkeypatch.setattr(
        recover, "clear_topic_card_ids_if_unchanged", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(recover, "get_submission_with_user", AsyncMock(return_value=fresh))
    monkeypatch.setattr(recover, "_repost_card", repost)
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    factory = FakeSessionFactory(AsyncMock())
    total, recovered = await recover.recover_missing_posts(bot, factory)

    assert total == 1
    assert recovered == 1
    reposted = repost.await_args.args[2]
    assert reposted is fresh
    assert [m.id for m in reposted.media] == [1, 2]


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


# ── recover_cardless_posts ───────────────────────────────────────────

def _patch_recheck(monkeypatch, subs) -> AsyncMock:
    """Make the pre-repost re-read return the same (still cardless) posts."""
    by_id = {sub.id: sub for sub in subs}
    fetch = AsyncMock(side_effect=lambda _session, sub_id: by_id.get(sub_id))
    monkeypatch.setattr(recover, "get_submission_with_user", fetch)
    return fetch


async def test_recover_cardless_reposts_every_selected_post(monkeypatch) -> None:
    """The narrow query owns the filtering: every returned post is reposted."""
    bot = make_bot()
    subs = [make_submission(sub_id=i) for i in range(1, 4)]

    repost = AsyncMock()
    listing = AsyncMock(return_value=subs)
    _patch_recheck(monkeypatch, subs)
    monkeypatch.setattr(recover, "list_active_submissions_without_card", listing)
    monkeypatch.setattr(recover, "_repost_card", repost)
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    session = AsyncMock()
    factory = FakeSessionFactory(session)
    recovered = await recover.recover_cardless_posts(bot, factory)

    assert recovered == 3
    listing.assert_awaited_once_with(session)
    assert [call.args[2] for call in repost.await_args_list] == subs
    # No probe: the job trusts the query instead of paying an API call per post.
    bot.forward_message.assert_not_awaited()


async def test_recover_cardless_continues_after_failures(monkeypatch) -> None:
    """A failing post never swallows the rest of the batch."""
    bot = make_bot()
    subs = [make_submission(sub_id=i) for i in range(1, 7)]

    repost = AsyncMock(side_effect=[Exception("boom")] * 5 + [None])
    _patch_recheck(monkeypatch, subs)
    monkeypatch.setattr(
        recover, "list_active_submissions_without_card", AsyncMock(return_value=subs)
    )
    monkeypatch.setattr(recover, "_repost_card", repost)
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    factory = FakeSessionFactory(AsyncMock())
    recovered = await recover.recover_cardless_posts(bot, factory)

    assert recovered == 1
    assert repost.await_count == 6
    assert repost.await_args_list[-1].args[2] is subs[-1]


async def test_recover_cardless_no_posts_is_silent(monkeypatch, caplog) -> None:
    bot = make_bot()

    monkeypatch.setattr(
        recover, "list_active_submissions_without_card", AsyncMock(return_value=[])
    )
    repost = AsyncMock()
    monkeypatch.setattr(recover, "_repost_card", repost)

    factory = FakeSessionFactory(AsyncMock())
    with caplog.at_level("INFO"):
        recovered = await recover.recover_cardless_posts(bot, factory)

    assert recovered == 0
    repost.assert_not_awaited()
    bot.send_message.assert_not_awaited()
    assert not [r for r in caplog.records if r.levelname == "INFO"]


async def test_recover_cardless_skips_post_that_got_a_card_meanwhile(monkeypatch) -> None:
    """A card recorded between the selection and the repost cancels the repost."""
    bot = make_bot()
    stale = make_submission(sub_id=1)
    other = make_submission(sub_id=2)
    fresh = make_submission(sub_id=1, topic_card_message_id=777)

    repost = AsyncMock()
    monkeypatch.setattr(
        recover, "list_active_submissions_without_card", AsyncMock(return_value=[stale, other])
    )
    monkeypatch.setattr(
        recover, "get_submission_with_user",
        AsyncMock(side_effect=lambda _session, sub_id: fresh if sub_id == 1 else other),
    )
    monkeypatch.setattr(recover, "_repost_card", repost)
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    factory = FakeSessionFactory(AsyncMock())
    recovered = await recover.recover_cardless_posts(bot, factory)

    assert recovered == 1
    assert [call.args[2] for call in repost.await_args_list] == [other]


async def test_recover_cardless_reposts_the_freshly_read_post(monkeypatch) -> None:
    """Модератор дописал медиа между выборкой и репостом → шлём свежий состав."""
    bot = make_bot()
    stale = make_submission(sub_id=4, media=[make_media(media_id=1, submission_id=4)])
    fresh = make_submission(
        sub_id=4,
        media=[make_media(media_id=1, submission_id=4), make_media(media_id=2, submission_id=4)],
    )

    repost = AsyncMock()
    monkeypatch.setattr(
        recover, "list_active_submissions_without_card", AsyncMock(return_value=[stale])
    )
    monkeypatch.setattr(recover, "get_submission_with_user", AsyncMock(return_value=fresh))
    monkeypatch.setattr(recover, "_repost_card", repost)
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    factory = FakeSessionFactory(AsyncMock())
    recovered = await recover.recover_cardless_posts(bot, factory)

    assert recovered == 1
    reposted = repost.await_args.args[2]
    assert reposted is fresh
    assert [m.id for m in reposted.media] == [1, 2]


async def test_recover_cardless_skips_post_gone_terminal(monkeypatch) -> None:
    """A post published/rejected after the selection is not reposted."""
    bot = make_bot()
    sub = make_submission(sub_id=3)
    fresh = make_submission(sub_id=3, status="published")

    repost = AsyncMock()
    monkeypatch.setattr(
        recover, "list_active_submissions_without_card", AsyncMock(return_value=[sub])
    )
    monkeypatch.setattr(recover, "get_submission_with_user", AsyncMock(return_value=fresh))
    monkeypatch.setattr(recover, "_repost_card", repost)
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())

    factory = FakeSessionFactory(AsyncMock())
    recovered = await recover.recover_cardless_posts(bot, factory)

    assert recovered == 0
    repost.assert_not_awaited()


async def test_recover_runs_never_overlap(monkeypatch) -> None:
    """The shared guard keeps the manual run and the job from double-posting."""
    bot = make_bot()
    sub = make_submission(sub_id=1)

    concurrent = 0
    peak = 0
    real_sleep = asyncio.sleep

    async def slow_repost(*args, **kwargs) -> None:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        # Yield to the loop: without the guard the other run would slip in here.
        await real_sleep(0)
        concurrent -= 1

    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())
    _patch_recheck(monkeypatch, [sub])
    monkeypatch.setattr(recover, "_repost_card", AsyncMock(side_effect=slow_repost))
    monkeypatch.setattr(
        recover, "list_active_submissions_without_card", AsyncMock(return_value=[sub])
    )
    monkeypatch.setattr(recover, "get_active_submissions", AsyncMock(return_value=[sub]))

    factory = FakeSessionFactory(AsyncMock())
    await asyncio.gather(
        recover.recover_cardless_posts(bot, factory),
        recover.recover_missing_posts(bot, factory),
    )

    assert peak == 1


# ── _repost_card ──────────────────────────────────────────────────────

async def test_repost_card_recreates_topic_when_deleted(monkeypatch) -> None:
    bot = make_bot()
    sub = make_submission(sub_id=5)
    sub.user_id = 99

    err = TelegramBadRequest(method=None, message="Message thread not found")  # type: ignore[arg-type]
    post_card = AsyncMock(side_effect=[err, ([], 1001)])
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


async def test_repost_card_deletes_delivered_block_when_commit_fails(monkeypatch) -> None:
    """Telegram delivered, commit failed → the live block is removed, error propagates."""
    bot = make_bot()
    sub = make_submission(sub_id=8)

    monkeypatch.setattr(
        recover.topics, "post_submission_card", AsyncMock(return_value=([11, 12], 13))
    )
    monkeypatch.setattr(recover.topics, "ensure_user_topic", AsyncMock())
    cleanup = AsyncMock()
    monkeypatch.setattr(recover.topics, "delete_delivered_card_messages", cleanup)

    ensure_session = AsyncMock()
    post_session = AsyncMock()
    post_session.commit.side_effect = RuntimeError("lock timeout")
    factory = FakeSessionFactory(ensure_session, post_session)

    with pytest.raises(RuntimeError):
        await recover._repost_card(bot, factory, sub)

    cleanup.assert_awaited_once_with(bot, [11, 12, 13], sub.id)
