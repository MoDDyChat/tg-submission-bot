"""Tests for the process-local intake/recovery guard."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from handlers.moderator import recover
from services import submission_intake
from tests.helpers import FakeSessionFactory, make_bot, make_message, make_submission, make_user


async def test_still_cardless_skips_inflight_submission(monkeypatch) -> None:
    sub_id = 21
    fresh = make_submission(sub_id=sub_id)
    monkeypatch.setattr(recover, "get_submission_with_user", AsyncMock(return_value=fresh))
    monkeypatch.setattr(recover, "is_intake_in_flight", lambda value: value == sub_id)

    result = await recover._still_cardless(FakeSessionFactory(AsyncMock()), sub_id)

    assert result is None


async def test_intake_guard_covers_telegram_region_and_is_cleared_after_commit(monkeypatch) -> None:
    sub_id = 22
    user = make_user()
    sub = make_submission(sub_id=sub_id, user=user)
    message = make_message()
    session = AsyncMock()
    ensure_started = asyncio.Event()
    release_ensure = asyncio.Event()
    commit_started = asyncio.Event()
    release_commit = asyncio.Event()

    async def ensure_user_topic(*args) -> None:
        ensure_started.set()
        await release_ensure.wait()

    async def commit_or_delete_delivered(*args) -> None:
        commit_started.set()
        await release_commit.wait()

    monkeypatch.setattr(submission_intake, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(submission_intake.topics, "ensure_user_topic", ensure_user_topic)
    monkeypatch.setattr(
        submission_intake.topics,
        "post_submission_card",
        AsyncMock(return_value=([], 23)),
    )
    monkeypatch.setattr(
        submission_intake.topics,
        "commit_or_delete_delivered",
        commit_or_delete_delivered,
    )
    monkeypatch.setattr(submission_intake.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(submission_intake, "_render_queue", AsyncMock())
    monkeypatch.setattr(submission_intake, "request_dashboard", lambda: None)
    monkeypatch.setattr(submission_intake, "request_author_card", lambda _user_id: None)
    monkeypatch.setattr(recover, "get_submission_with_user", AsyncMock(return_value=sub))

    task = asyncio.create_task(
        submission_intake._publish_submission_to_topic(message, session, sub_id, user.id)
    )
    await ensure_started.wait()
    assert submission_intake.is_intake_in_flight(sub_id)
    assert await recover._still_cardless(FakeSessionFactory(AsyncMock()), sub_id) is None

    release_ensure.set()
    await commit_started.wait()
    assert submission_intake.is_intake_in_flight(sub_id)
    assert await recover._still_cardless(FakeSessionFactory(AsyncMock()), sub_id) is None

    release_commit.set()
    await task
    assert not submission_intake.is_intake_in_flight(sub_id)


async def test_intake_guard_is_cleared_when_telegram_fails(monkeypatch) -> None:
    sub_id = 23
    user = make_user()
    sub = make_submission(sub_id=sub_id, user=user)
    session = AsyncMock()
    message = make_message()
    monkeypatch.setattr(submission_intake, "get_submission_with_user", AsyncMock(return_value=sub))
    monkeypatch.setattr(
        submission_intake.topics,
        "ensure_user_topic",
        AsyncMock(side_effect=RuntimeError("telegram down")),
    )
    monkeypatch.setattr(submission_intake, "_try_answer", AsyncMock())

    await submission_intake._publish_submission_to_topic(message, session, sub_id, user.id)

    assert not submission_intake.is_intake_in_flight(sub_id)


async def test_recover_cardless_skips_inflight_and_processes_neighbor(monkeypatch) -> None:
    inflight_id = 24
    neighbor_id = 25
    inflight = make_submission(sub_id=inflight_id)
    neighbor = make_submission(sub_id=neighbor_id)
    repost = AsyncMock()
    monkeypatch.setattr(
        recover,
        "list_active_submissions_without_card",
        AsyncMock(return_value=[inflight, neighbor]),
    )
    monkeypatch.setattr(
        recover,
        "get_submission_with_user",
        AsyncMock(side_effect=[inflight, neighbor]),
    )
    monkeypatch.setattr(
        recover,
        "is_intake_in_flight",
        lambda sub_id: sub_id == inflight_id,
    )
    monkeypatch.setattr(recover, "_repost_card", repost)
    monkeypatch.setattr(recover.asyncio, "sleep", AsyncMock())
    bot = make_bot()
    factory = FakeSessionFactory(AsyncMock())

    recovered = await recover.recover_cardless_posts(bot, factory)

    assert recovered == 1
    repost.assert_awaited_once_with(bot, factory, neighbor)
    assert repost.await_args.args[2] is neighbor
