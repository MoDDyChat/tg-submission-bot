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
    mark_dead = AsyncMock(return_value=True)
    notify_admins_mock = AsyncMock()
    monkeypatch.setattr(scheduler, "schedule_post", schedule_post)
    monkeypatch.setattr(scheduler, "_publish_job", publish_job)
    monkeypatch.setattr(scheduler, "mark_publication_dead", mark_dead)
    monkeypatch.setattr(scheduler, "notify_admins", notify_admins_mock)

    await scheduler.recover_scheduled_jobs(bot, factory)

    # future_pub, immediate_pub (re-scheduled at t+1s), naive_pub — all via schedule_post
    assert schedule_post.call_count == 3
    scheduled_ids = {call.args[0] for call in schedule_post.call_args_list}
    assert scheduled_ids == {1, 2, 4}
    # immediate overdue job is rescheduled, NOT published directly
    publish_job.assert_not_awaited()
    # skipped_pub (>24h overdue) is marked dead and reported to admins
    mark_dead.assert_awaited_once_with(session, 3)
    notify_admins_mock.assert_awaited_once()
    assert notify_admins_mock.call_args.kwargs["actor"] is None
    assert "3" in notify_admins_mock.call_args.kwargs["action_text"]


async def test_recover_scheduled_jobs_marks_overdue_dead_and_notifies_once(monkeypatch) -> None:
    """A publication overdue by 25h is marked dead and reported; a repeat run (dead_at
    already set) sends no second notification, since mark_publication_dead returns False."""
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
    dead_pub = make_publication(pub_id=5, submission_id=50, publish_at=fixed_now - timedelta(hours=25))
    recent_pub = make_publication(pub_id=6, submission_id=60, publish_at=fixed_now - timedelta(hours=1))

    monkeypatch.setattr(scheduler, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        scheduler,
        "get_unpublished_publications",
        AsyncMock(return_value=[dead_pub, recent_pub]),
    )
    monkeypatch.setattr(scheduler, "schedule_post", Mock())
    mark_dead = AsyncMock(return_value=False)  # already dead_at set from a prior run
    notify_admins_mock = AsyncMock()
    monkeypatch.setattr(scheduler, "mark_publication_dead", mark_dead)
    monkeypatch.setattr(scheduler, "notify_admins", notify_admins_mock)

    await scheduler.recover_scheduled_jobs(bot, factory)

    mark_dead.assert_awaited_once_with(session, 5)
    # mark_publication_dead returned False (already dead) — no notification is sent
    notify_admins_mock.assert_not_awaited()


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


async def test_topic_cards_recover_job_delegates_and_swallows(monkeypatch) -> None:
    """The job is a thin wrapper: it delegates and never lets an error escape."""
    import handlers.moderator.recover as recover_mod

    recover_cardless = AsyncMock(return_value=0)
    monkeypatch.setattr(recover_mod, "recover_cardless_posts", recover_cardless)
    factory = FakeSessionFactory(AsyncMock())
    bot = make_bot()

    await scheduler.topic_cards_recover_job(bot, factory)

    recover_cardless.assert_awaited_once_with(bot, factory)

    recover_cardless.side_effect = RuntimeError("boom")
    await scheduler.topic_cards_recover_job(bot, factory)


def test_register_scheduled_jobs_includes_author_card_and_dashboard_jobs(mock_scheduler) -> None:
    import core.bot as bot_mod

    bot_mod._register_scheduled_jobs(make_bot(), Mock())

    calls_by_id = {
        call.kwargs["id"]: call for call in mock_scheduler.add_job.call_args_list
    }

    expected = {
        "author_card_render": {"seconds": 60},
        "author_card_reconcile": {"minutes": 10},
        "dashboard_render": {"seconds": 60},
        "topic_cards_recover": {"minutes": 5},
    }
    for job_id, interval_kwargs in expected.items():
        assert job_id in calls_by_id, f"job {job_id} was not registered"
        call = calls_by_id[job_id]
        assert call.args[1] == "interval"
        for key, value in interval_kwargs.items():
            assert call.kwargs[key] == value
        assert call.kwargs["max_instances"] == 1
        assert call.kwargs["replace_existing"] is True
        assert call.kwargs["coalesce"] is True


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
