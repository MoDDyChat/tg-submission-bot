from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

import core.messages as msg
from core.exceptions import PublishFailedError, PublishStateUnknownError, SubmissionStatusError
from services import publisher
from tests.helpers import FakeSessionFactory, make_bot, make_media, make_publication, make_sent_message, make_submission, make_user


class FakeRetryAfter(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(f"retry_after={retry_after}")


class FakeNetworkError(Exception):
    pass


def _patch_common(monkeypatch, sub, pub):
    """Patch external collaborators; test behavior via mark_published / update_submission_status."""
    monkeypatch.setattr(publisher, "get_publication", AsyncMock(return_value=pub))
    monkeypatch.setattr(publisher, "get_submission_with_user", AsyncMock(return_value=sub))
    mark_published_mock = AsyncMock()
    update_status_mock = AsyncMock()
    finalize = AsyncMock()
    update_title = AsyncMock(return_value=True)
    render_queue = AsyncMock()
    monkeypatch.setattr(publisher, "mark_published", mark_published_mock)
    monkeypatch.setattr(publisher, "update_submission_status", update_status_mock)
    monkeypatch.setattr(publisher.topics_svc, "finalize_submission_card", finalize)
    monkeypatch.setattr(publisher.topics_svc, "request_topic_title_sync", update_title)
    monkeypatch.setattr(publisher.topic_notifications, "notify_published", AsyncMock())
    monkeypatch.setattr(publisher, "_render_queue", render_queue)
    monkeypatch.setattr(publisher, "_render_schedule", AsyncMock())
    return mark_published_mock, update_status_mock, finalize, update_title, render_queue


async def test_publish_post_skips_when_publication_is_already_done(monkeypatch) -> None:
    pub = make_publication(published_at=datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(publisher, "get_publication", AsyncMock(return_value=pub))
    get_sub = AsyncMock()
    monkeypatch.setattr(publisher, "get_submission_with_user", get_sub)

    bot = make_bot()
    await publisher.publish_post(bot, FakeSessionFactory(AsyncMock()), pub.id, pub.submission_id, None)

    get_sub.assert_not_awaited()
    bot.send_message.assert_not_awaited()


async def test_publish_post_text_only_persists_and_notifies_viewer(monkeypatch) -> None:
    user = make_user(telegram_id=999)
    sub = make_submission(user=user, status="scheduled", caption="Описание", tags=["One"], media=[])
    pub = make_publication(pub_id=5, submission_id=sub.id)
    mark_pub, update_status, finalize, update_title, render_queue = _patch_common(monkeypatch, sub, pub)
    render_schedule = AsyncMock()
    monkeypatch.setattr(publisher, "_render_schedule", render_schedule)

    main_session = AsyncMock()
    cleanup_session = AsyncMock()
    factory = FakeSessionFactory(main_session, cleanup_session)
    bot = make_bot()
    bot.send_message.side_effect = [make_sent_message(77), make_sent_message(88)]

    await publisher.publish_post(bot, factory, pub.id, sub.id, None)

    assert bot.send_message.await_args_list[0].args == (
        publisher.config.channel_id,
        "#One\n\nОписание",
    )
    mark_pub.assert_awaited_once_with(main_session, pub.id, 77, [77])
    update_status.assert_awaited_once_with(main_session, sub.id, "published")
    finalize.assert_awaited_once()
    update_title.assert_awaited_once_with(cleanup_session, user.id)
    render_queue.assert_awaited_once_with(bot, cleanup_session)
    render_schedule.assert_awaited_once_with(bot, cleanup_session)
    assert bot.send_message.await_args_list[1].args == (
        user.telegram_id,
        msg.PUBLISHED_NOTIFICATION.format(sub_id=sub.id),
    )


@pytest.mark.parametrize(
    ("media_type", "method_name"),
    [
        ("photo", "send_photo"),
        ("video", "send_video"),
        ("animation", "send_animation"),
        ("document", "send_document"),
    ],
)
async def test_publish_post_single_media_uses_expected_bot_method(
    monkeypatch,
    media_type: str,
    method_name: str,
) -> None:
    sub = make_submission(
        status="scheduled",
        tags=["One"],
        media=[make_media(media_type=media_type)],
    )
    pub = make_publication(pub_id=5, submission_id=sub.id)
    mark_pub, _, _, _, _ = _patch_common(monkeypatch, sub, pub)

    main_session = AsyncMock()
    cleanup_session = AsyncMock()
    factory = FakeSessionFactory(main_session, cleanup_session)
    bot = make_bot()
    getattr(bot, method_name).return_value = make_sent_message(55)
    bot.send_message.return_value = make_sent_message(88)

    await publisher.publish_post(bot, factory, pub.id, sub.id, None)

    getattr(bot, method_name).assert_awaited_once_with(
        publisher.config.channel_id,
        "file-1",
        caption="#One\n\nCaption",
        parse_mode="HTML",
    )
    mark_pub.assert_awaited_once_with(main_session, pub.id, 55, [55])


async def test_publish_post_media_group_tracks_all_message_ids(monkeypatch) -> None:
    sub = make_submission(
        status="scheduled",
        tags=["One"],
        media=[
            make_media(media_id=1, file_id="f1", media_type="photo"),
            make_media(media_id=2, file_id="f2", media_type="video", sort_order=1),
            make_media(media_id=3, file_id="f3", media_type="document", sort_order=2),
        ],
    )
    pub = make_publication(pub_id=5, submission_id=sub.id)
    mark_pub, _, _, _, _ = _patch_common(monkeypatch, sub, pub)

    main_session = AsyncMock()
    cleanup_session = AsyncMock()
    factory = FakeSessionFactory(main_session, cleanup_session)
    bot = make_bot()
    bot.send_media_group.return_value = [make_sent_message(21), make_sent_message(22), make_sent_message(23)]
    bot.send_message.return_value = make_sent_message(88)

    await publisher.publish_post(bot, factory, pub.id, sub.id, None)

    group = bot.send_media_group.await_args.args[1]
    assert [item.media for item in group] == ["f1", "f2", "f3"]
    assert group[0].caption == "#One\n\nCaption"
    assert group[1].caption is None
    mark_pub.assert_awaited_once_with(main_session, pub.id, 21, [21, 22, 23])


async def test_publish_post_retries_on_retry_after(monkeypatch) -> None:
    monkeypatch.setattr(publisher, "TelegramRetryAfter", FakeRetryAfter)
    sleep = AsyncMock()
    monkeypatch.setattr(publisher.asyncio, "sleep", sleep)

    sub = make_submission(status="scheduled", media=[])
    pub = make_publication(pub_id=5, submission_id=sub.id)
    _patch_common(monkeypatch, sub, pub)

    bot = make_bot()
    bot.send_message.side_effect = [FakeRetryAfter(7), make_sent_message(70), make_sent_message(80)]

    await publisher.publish_post(bot, FakeSessionFactory(AsyncMock(), AsyncMock()), pub.id, sub.id, None)

    sleep.assert_awaited_once_with(7)
    assert bot.send_message.await_count == 3


async def test_publish_post_retries_on_network_error(monkeypatch) -> None:
    monkeypatch.setattr(publisher, "TelegramNetworkError", FakeNetworkError)
    sleep = AsyncMock()
    monkeypatch.setattr(publisher.asyncio, "sleep", sleep)

    sub = make_submission(status="scheduled", media=[])
    pub = make_publication(pub_id=5, submission_id=sub.id)
    _patch_common(monkeypatch, sub, pub)

    bot = make_bot()
    bot.send_message.side_effect = [FakeNetworkError("boom"), make_sent_message(70), make_sent_message(80)]

    await publisher.publish_post(bot, FakeSessionFactory(AsyncMock(), AsyncMock()), pub.id, sub.id, None)

    sleep.assert_awaited_once_with(5)
    assert bot.send_message.await_count == 3


async def test_publish_post_raises_after_retry_exhaustion(monkeypatch) -> None:
    monkeypatch.setattr(publisher, "TelegramNetworkError", FakeNetworkError)
    sleep = AsyncMock()
    monkeypatch.setattr(publisher.asyncio, "sleep", sleep)

    sub = make_submission(status="scheduled", media=[])
    pub = make_publication(pub_id=5, submission_id=sub.id)
    mark_pub, _, _, _, _ = _patch_common(monkeypatch, sub, pub)

    bot = make_bot()
    bot.send_message.side_effect = [FakeNetworkError("1"), FakeNetworkError("2"), FakeNetworkError("3")]

    with pytest.raises(PublishFailedError):
        await publisher.publish_post(bot, FakeSessionFactory(AsyncMock()), pub.id, sub.id, None)

    assert sleep.await_count == 2
    mark_pub.assert_not_awaited()


async def test_publish_post_raises_for_non_scheduled_submission(monkeypatch) -> None:
    sub = make_submission(status="pending", media=[])
    pub = make_publication(pub_id=5, submission_id=sub.id)
    monkeypatch.setattr(publisher, "get_publication", AsyncMock(return_value=pub))
    monkeypatch.setattr(publisher, "get_submission_with_user", AsyncMock(return_value=sub))

    with pytest.raises(SubmissionStatusError):
        await publisher.publish_post(make_bot(), FakeSessionFactory(AsyncMock()), pub.id, sub.id, None)


async def test_publish_post_recovers_with_fresh_session_after_commit_failure(monkeypatch) -> None:
    sub = make_submission(status="scheduled", media=[])
    pub = make_publication(pub_id=5, submission_id=sub.id)
    # Fail on first mark_published call (main session), succeed on retry (fresh session)
    mark_pub, _, _, _, _ = _patch_common(monkeypatch, sub, pub)
    mark_pub.side_effect = [Exception("db"), None]

    main_session = AsyncMock()
    cleanup_session = AsyncMock()
    factory = FakeSessionFactory(main_session, cleanup_session)
    bot = make_bot()
    bot.send_message.side_effect = [make_sent_message(77), make_sent_message(88)]

    await publisher.publish_post(bot, factory, pub.id, sub.id, None)

    main_session.rollback.assert_awaited_once()
    assert mark_pub.await_count == 2  # once in main session, once in fresh session
    bot.delete_message.assert_not_awaited()


async def test_publish_post_rolls_back_sent_messages_when_db_sync_fails(monkeypatch) -> None:
    sub = make_submission(status="scheduled", media=[])
    pub = make_publication(pub_id=5, submission_id=sub.id)
    # Both main and fresh session DB calls fail
    mark_pub, _, _, _, _ = _patch_common(monkeypatch, sub, pub)
    mark_pub.side_effect = Exception("db")

    main_session = AsyncMock()
    bot = make_bot()
    bot.send_message.return_value = make_sent_message(77)

    with pytest.raises(PublishFailedError):
        await publisher.publish_post(bot, FakeSessionFactory(main_session), pub.id, sub.id, None)

    main_session.rollback.assert_awaited_once()
    bot.delete_message.assert_awaited_once_with(publisher.config.channel_id, 77)


async def test_publish_post_raises_unknown_state_when_rollback_fails(monkeypatch) -> None:
    sub = make_submission(status="scheduled", media=[])
    pub = make_publication(pub_id=5, submission_id=sub.id)
    mark_pub, _, _, _, _ = _patch_common(monkeypatch, sub, pub)
    mark_pub.side_effect = Exception("db")

    bot = make_bot()
    bot.send_message.return_value = make_sent_message(77)
    bot.delete_message.side_effect = Exception("cannot delete")

    with pytest.raises(PublishStateUnknownError):
        await publisher.publish_post(bot, FakeSessionFactory(AsyncMock()), pub.id, sub.id, None)


async def test_publish_post_finalize_failure_does_not_block_viewer_notification(monkeypatch) -> None:
    sub = make_submission(status="scheduled", user=make_user(telegram_id=404), media=[])
    pub = make_publication(pub_id=5, submission_id=sub.id)
    _patch_common(monkeypatch, sub, pub)
    monkeypatch.setattr(
        publisher.topics_svc,
        "finalize_submission_card",
        AsyncMock(side_effect=Exception("topics down")),
    )

    bot = make_bot()
    bot.send_message.side_effect = [make_sent_message(77), make_sent_message(88)]

    await publisher.publish_post(bot, FakeSessionFactory(AsyncMock(), AsyncMock()), pub.id, sub.id, None)

    assert bot.send_message.await_args_list[1].args == (
        404,
        msg.PUBLISHED_NOTIFICATION.format(sub_id=sub.id),
    )
