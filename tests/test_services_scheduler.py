from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from core.exceptions import (
    PublicationNotFoundError,
    PublishFailedError,
    SubmissionCancelledError,
    SubmissionStatusError,
)
from services import scheduler
from tests.helpers import FakeSessionFactory, make_bot, make_publication


def test_job_id_formats_publication_identifier() -> None:
    assert scheduler._job_id(42) == "pub_42"


@pytest.fixture()
def mock_scheduler(monkeypatch):
    """Provide a Mock as services.scheduler.scheduler for unit tests."""
    mock = Mock()
    monkeypatch.setattr(scheduler, "scheduler", mock)
    return mock


def test_schedule_post_registers_job_with_replace_existing(mock_scheduler) -> None:
    publish_at = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
    bot = make_bot()
    factory = FakeSessionFactory(AsyncMock())

    scheduler.schedule_post(5, publish_at, bot, factory, submission_id=9, edited_caption="Caption")

    mock_scheduler.add_job.assert_called_once()
    kwargs = mock_scheduler.add_job.call_args.kwargs
    assert kwargs["id"] == "pub_5"
    assert kwargs["replace_existing"] is True
    assert kwargs["args"] == [5, 9, "Caption", bot, factory]


def test_cancel_scheduled_removes_existing_job(mock_scheduler) -> None:
    mock_scheduler.get_job.return_value = object()

    scheduler.cancel_scheduled(7)

    mock_scheduler.remove_job.assert_called_once_with("pub_7")


def test_cancel_scheduled_is_noop_when_job_is_missing(mock_scheduler) -> None:
    mock_scheduler.get_job.return_value = None

    scheduler.cancel_scheduled(7)

    mock_scheduler.remove_job.assert_not_called()


@pytest.mark.parametrize(
    "exc",
    [
        PublicationNotFoundError(1),
        SubmissionCancelledError(2),
        SubmissionStatusError(3, "pending"),
    ],
)
async def test_publish_job_skips_known_domain_errors(monkeypatch, caplog, exc) -> None:
    monkeypatch.setattr(scheduler, "publish_post", AsyncMock(side_effect=exc))

    with caplog.at_level("INFO"):
        await scheduler._publish_job(1, 2, "caption", make_bot(), FakeSessionFactory(AsyncMock()))

    assert "пропущена" in caplog.text


async def test_publish_job_logs_msbot_errors(monkeypatch, caplog) -> None:
    monkeypatch.setattr(scheduler, "publish_post", AsyncMock(side_effect=PublishFailedError("boom")))

    with caplog.at_level("ERROR"):
        await scheduler._publish_job(1, 2, "caption", make_bot(), FakeSessionFactory(AsyncMock()))

    assert "завершилась с ошибкой" in caplog.text


async def test_recover_scheduled_jobs_restores_future_and_immediate_publications(monkeypatch) -> None:
    fixed_now = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    session = AsyncMock()
    factory = FakeSessionFactory(session)
    bot = make_bot()
    future_pub = make_publication(pub_id=1, submission_id=10, publish_at=fixed_now + timedelta(hours=1))
    immediate_pub = make_publication(pub_id=2, submission_id=20, publish_at=fixed_now - timedelta(hours=2))
    skipped_pub = make_publication(pub_id=3, submission_id=30, publish_at=fixed_now - timedelta(hours=30))
    naive_pub = make_publication(
        pub_id=4,
        submission_id=40,
        publish_at=(fixed_now + timedelta(hours=3)).replace(tzinfo=None),
    )

    monkeypatch.setattr(scheduler, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        scheduler,
        "get_unpublished_publications",
        AsyncMock(return_value=[future_pub, immediate_pub, skipped_pub, naive_pub]),
    )
    schedule_post = Mock()
    publish_job = AsyncMock()
    monkeypatch.setattr(scheduler, "schedule_post", schedule_post)
    monkeypatch.setattr(scheduler, "_publish_job", publish_job)

    await scheduler.recover_scheduled_jobs(bot, factory)

    # future_pub, immediate_pub (re-scheduled at t+1s), naive_pub — all via schedule_post
    assert schedule_post.call_count == 3
    scheduled_ids = {call.args[0] for call in schedule_post.call_args_list}
    assert scheduled_ids == {1, 2, 4}
    # immediate overdue job is rescheduled, NOT published directly
    publish_job.assert_not_awaited()


async def test_queue_render_job_calls_render_queue_with_force_reconcile(monkeypatch) -> None:
    render_queue_mock = AsyncMock()
    render_schedule_mock = AsyncMock()

    # queue_render_job does `from services.topics_queue import render_queue, render_schedule` at runtime
    import services.topics_queue as topics_queue_mod
    monkeypatch.setattr(topics_queue_mod, "render_queue", render_queue_mock)
    monkeypatch.setattr(topics_queue_mod, "render_schedule", render_schedule_mock)

    session = AsyncMock()
    factory = FakeSessionFactory(session)
    bot = make_bot()

    await scheduler.queue_render_job(bot, factory)

    render_queue_mock.assert_awaited_once_with(bot, session, force_reconcile=True)
    render_schedule_mock.assert_awaited_once_with(bot, session, force_reconcile=True)
    session.commit.assert_awaited_once()


async def test_topic_titles_reconcile_job_queues_drift(monkeypatch) -> None:
    import services.topics as topics_mod

    reconcile = AsyncMock()
    monkeypatch.setattr(topics_mod, "reconcile_topic_titles", reconcile)
    session = AsyncMock()
    factory = FakeSessionFactory(session)

    await scheduler.topic_titles_reconcile_job(factory)

    reconcile.assert_awaited_once_with(session)
    session.commit.assert_awaited_once()


async def test_topic_cards_reconcile_job_hands_over_factory(monkeypatch) -> None:
    """Card repairs call Telegram between transactions, so the service owns them."""
    import services.topics as topics_mod

    reconcile = AsyncMock(return_value=0)
    monkeypatch.setattr(topics_mod, "reconcile_submission_cards", reconcile)
    session = AsyncMock()
    factory = FakeSessionFactory(session)
    bot = make_bot()

    await scheduler.topic_cards_reconcile_job(bot, factory)

    reconcile.assert_awaited_once_with(bot, factory)
    session.commit.assert_not_awaited()


async def test_topic_title_sync_job_processes_one_tick(monkeypatch) -> None:
    import services.topics as topics_mod

    process_next = AsyncMock(return_value=True)
    monkeypatch.setattr(
        topics_mod, "process_next_topic_title_sync", process_next
    )
    session = AsyncMock()
    factory = FakeSessionFactory(session)
    bot = make_bot()

    await scheduler.topic_title_sync_job(bot, factory)

    # The worker owns its transactions, so the job hands over the factory itself
    process_next.assert_awaited_once_with(bot, factory)
    session.commit.assert_not_awaited()
