from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock


import core.messages as msg
from core.exceptions import PublishFailedError
from handlers.moderator import _helpers as moderator_helpers
from handlers.moderator import publish_now, review, schedule, view as moderator_view
from handlers.moderator import cmd_moderator_cancel, handle_noop
from services import edit_lock, topics
from states.moderator import ModeratorReview
from tests.helpers import FakeSessionFactory, FakeState, make_callback, make_message, make_publication, make_sent_message, make_submission, make_user


async def test_delete_tracked_messages_removes_all_known_ids_except_skipped() -> None:
    bot = make_message().bot
    data = {
        "media_message_ids": [1, 2],
        "actions_message_id": 3,
        "prompt_message_id": 4,
        "schedule_message_id": 5,
    }

    await moderator_helpers._delete_tracked_messages(
        bot,
        77,
        data,
        skip_keys=frozenset({"actions_message_id"}),
    )

    deleted_ids = [call.args[1] for call in bot.delete_message.await_args_list]
    assert deleted_ids == [1, 2, 4, 5]


async def test_send_submission_view_text_only_updates_state_and_buttons(monkeypatch) -> None:
    bot = make_message().bot
    message = make_message(bot=bot)
    message.answer.side_effect = [make_sent_message(10), make_sent_message(11)]
    state = FakeState({"media_message_ids": [1, 2], "actions_message_id": 3})
    submission = make_submission(sub_id=5, status="pending", media=[], tags=["One"])

    monkeypatch.setattr(moderator_view, "get_submission_with_user", AsyncMock(return_value=submission))

    result = await moderator_helpers._send_submission_view(message, AsyncMock(), 5, state)

    assert result is True
    assert state.state == ModeratorReview.viewing_post
    assert state.data["sub_id"] == 5
    assert state.data["media_message_ids"] == [10]
    assert state.data["actions_message_id"] == 11
    assert [call.args[1] for call in bot.delete_message.await_args_list] == [1, 2, 3]


async def test_cmd_start_review_keeps_pending_status_and_syncs_title(monkeypatch) -> None:
    """Opening a card takes an edit lock but must not change the submission status.

    "A moderator is working on this right now" lives in edit_locks; the status
    stays 'pending' until the post is actually scheduled, published or rejected.
    """
    state = FakeState({"actions_message_id": 3})
    message = make_message(text="/start review_55")
    session = AsyncMock()
    db_user = make_user(user_id=2, telegram_id=202)
    submission = make_submission(sub_id=55, status="pending")

    delete_tracked = AsyncMock()
    send_view = AsyncMock(return_value=True)
    acquire_lock = AsyncMock(return_value=(True, None))
    title_sync = AsyncMock()
    monkeypatch.setattr(review, "get_submission_with_user", AsyncMock(return_value=submission))
    monkeypatch.setattr(review, "get_user_by_id", AsyncMock(return_value=None))
    monkeypatch.setattr(review.edit_lock, "acquire_lock", acquire_lock)
    monkeypatch.setattr(review.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(review.topics, "request_topic_title_sync", title_sync)
    monkeypatch.setattr(review, "_delete_tracked_messages", delete_tracked)
    monkeypatch.setattr(review, "_send_submission_view", send_view)

    await review.cmd_start_review(message, session, state, db_user)

    assert submission.status == "pending"
    acquire_lock.assert_awaited_once()
    title_sync.assert_awaited_once()
    delete_tracked.assert_awaited_once()
    assert state.cleared is True
    send_view.assert_awaited_once()


async def test_cmd_start_review_reports_stale_card_when_submission_is_gone(monkeypatch) -> None:
    """An orphan topic card (row deleted/rolled back) must get a clear "stale card"
    reply instead of the generic "not found", and must not touch the lock or FSM state.
    """
    state = FakeState({"actions_message_id": 3})
    message = make_message(text="/start review_377")
    session = AsyncMock()
    db_user = make_user(user_id=2, telegram_id=202)

    acquire_lock = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(review, "get_submission_with_user", AsyncMock(return_value=None))
    monkeypatch.setattr(review.edit_lock, "acquire_lock", acquire_lock)

    await review.cmd_start_review(message, session, state, db_user)

    message.answer.assert_awaited_once_with(msg.SUBMISSION_CARD_STALE.format(sub_id=377))
    acquire_lock.assert_not_awaited()
    assert state.cleared is False
    assert state.state is None


async def test_cmd_start_review_does_not_re_render_a_just_rendered_post(monkeypatch) -> None:
    """A re-fired deep link (double tap on the lock indicator) must not duplicate the view."""
    state = FakeState({"sub_id": 55, "actions_message_id": 3, "view_rendered_at": time.time()})
    state.state = ModeratorReview.viewing_post.state
    message = make_message(text="/start review_55")
    session = AsyncMock()
    db_user = make_user(user_id=2, telegram_id=202)
    submission = make_submission(sub_id=55, status="pending")

    delete_tracked = AsyncMock()
    send_view = AsyncMock(return_value=True)
    acquire_lock = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(review, "get_submission_with_user", AsyncMock(return_value=submission))
    monkeypatch.setattr(review.edit_lock, "acquire_lock", acquire_lock)
    monkeypatch.setattr(review.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(review.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(review, "_delete_tracked_messages", delete_tracked)
    monkeypatch.setattr(review, "_send_submission_view", send_view)

    await review.cmd_start_review(message, session, state, db_user)

    acquire_lock.assert_awaited_once()  # the lock is still extended
    send_view.assert_not_awaited()
    delete_tracked.assert_not_awaited()
    assert state.cleared is False


async def test_cmd_start_review_re_renders_a_stale_view_of_the_same_post(monkeypatch) -> None:
    """Re-entering later must still redraw — that is how a lost view is recovered."""
    state = FakeState(
        {"sub_id": 55, "actions_message_id": 3, "view_rendered_at": time.time() - 3600}
    )
    state.state = ModeratorReview.viewing_post.state
    message = make_message(text="/start review_55")
    session = AsyncMock()
    db_user = make_user(user_id=2, telegram_id=202)
    submission = make_submission(sub_id=55, status="pending")

    send_view = AsyncMock(return_value=True)
    delete_tracked = AsyncMock()
    monkeypatch.setattr(review, "get_submission_with_user", AsyncMock(return_value=submission))
    monkeypatch.setattr(review.edit_lock, "acquire_lock", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(review.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(review.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(review, "_delete_tracked_messages", delete_tracked)
    monkeypatch.setattr(review, "_send_submission_view", send_view)

    await review.cmd_start_review(message, session, state, db_user)

    delete_tracked.assert_awaited_once()
    send_view.assert_awaited_once()


async def test_cmd_start_review_re_renders_when_another_post_is_open(monkeypatch) -> None:
    state = FakeState({"sub_id": 12, "actions_message_id": 3, "view_rendered_at": time.time()})
    state.state = ModeratorReview.viewing_post.state
    message = make_message(text="/start review_55")
    session = AsyncMock()
    db_user = make_user(user_id=2, telegram_id=202)
    submission = make_submission(sub_id=55, status="pending")

    send_view = AsyncMock(return_value=True)
    monkeypatch.setattr(review, "get_submission_with_user", AsyncMock(return_value=submission))
    monkeypatch.setattr(review.edit_lock, "acquire_lock", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(review.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(review.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(review, "_delete_tracked_messages", AsyncMock())
    monkeypatch.setattr(review, "_send_submission_view", send_view)

    await review.cmd_start_review(message, session, state, db_user)

    send_view.assert_awaited_once()


async def test_cmd_start_review_serializes_concurrent_deep_links(monkeypatch) -> None:
    """Two deep links racing while the first album is still uploading render once."""
    state = FakeState()
    message = make_message(text="/start review_55")
    session = AsyncMock()
    db_user = make_user(user_id=2, telegram_id=202)
    submission = make_submission(sub_id=55, status="pending")

    async def slow_render(msg_, session_, sub_id_, state_) -> bool:
        # Mimics render_submission_view: state is written first, message ids only
        # after Telegram accepted the album.
        await state_.set_state(ModeratorReview.viewing_post)
        await state_.update_data(sub_id=sub_id_)
        await asyncio.sleep(0.05)
        await state_.update_data(actions_message_id=99)
        return True

    send_view = AsyncMock(side_effect=slow_render)
    delete_tracked = AsyncMock()
    monkeypatch.setattr(review, "get_submission_with_user", AsyncMock(return_value=submission))
    monkeypatch.setattr(review.edit_lock, "acquire_lock", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(review.topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(review.topics, "request_topic_title_sync", AsyncMock())
    monkeypatch.setattr(review, "_delete_tracked_messages", delete_tracked)
    monkeypatch.setattr(review, "_send_submission_view", send_view)
    review._render_locks.pop(message.chat.id, None)

    await asyncio.gather(
        review.cmd_start_review(message, session, state, db_user),
        review.cmd_start_review(message, session, state, db_user),
    )

    assert send_view.await_count == 1
    delete_tracked.assert_awaited_once()


async def test_schedule_confirm_yes_rejects_past_time_and_returns_to_minutes(monkeypatch) -> None:
    state = FakeState(
        {
            "sub_id": 7,
            "pub_year": 2000,
            "pub_month": 1,
            "pub_day": 1,
            "pub_hour": 10,
            "pub_minute": 0,
        }
    )
    callback = make_callback(message=make_message())
    db_user = make_user()
    monkeypatch.setattr(
        schedule, "get_day_occupancy", AsyncMock(return_value=schedule.DayOccupancy())
    )

    await schedule.handle_confirm_yes(callback, AsyncMock(), state, db_user)

    assert state.state == ModeratorReview.picking_minute
    callback.answer.assert_awaited_once_with(msg.SCHEDULE_TIME_PAST, show_alert=True)
    callback.message.edit_text.assert_awaited_once()


async def test_publish_now_confirm_recovers_state_after_publish_failure(monkeypatch) -> None:
    main_session = AsyncMock()
    recovery_session = AsyncMock()
    state = FakeState(
        {
            "sub_id": 9,
            "media_message_ids": [10],
            "actions_message_id": 11,
            "schedule_message_id": 12,
        }
    )
    callback = make_callback(message=make_message())
    db_user = make_user()
    submission = make_submission(sub_id=9, status="pending")
    publication = make_publication(pub_id=3, submission_id=9)
    update_status = AsyncMock()

    monkeypatch.setattr(publish_now, "get_submission_with_user", AsyncMock(return_value=submission))
    monkeypatch.setattr(publish_now, "create_publication", AsyncMock(return_value=publication))
    monkeypatch.setattr(publish_now, "update_submission_status", update_status)
    monkeypatch.setattr(publish_now, "publish_post", AsyncMock(side_effect=PublishFailedError("boom")))
    monkeypatch.setattr(publish_now, "get_publication", AsyncMock(return_value=publication))
    monkeypatch.setattr(publish_now, "delete_publication", AsyncMock())
    monkeypatch.setattr(publish_now.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(publish_now, "session_factory", FakeSessionFactory(recovery_session))

    await publish_now.handle_publish_now_confirm(callback, main_session, state, db_user)

    assert state.state == ModeratorReview.viewing_post
    assert [call.args[2] for call in update_status.await_args_list] == ["scheduled", "pending"]
    publish_now.delete_publication.assert_awaited_once_with(recovery_session, publication.id)
    callback.message.answer.assert_awaited_once_with(
        msg.PUBLISH_NOW_FAILED.format(sub_id=9, error="boom")
    )


# ---------------------------------------------------------------------------
# cmd_moderator_cancel — sub-state with lock success
# ---------------------------------------------------------------------------

async def test_cancel_sub_state_lock_success_returns_to_viewing(monkeypatch) -> None:
    state = FakeState(
        {
            "sub_id": 5,
            "media_message_ids": [10, 11],
            "actions_message_id": 12,
            "prompt_message_id": 99,
        }
    )
    state.state = ModeratorReview.editing_caption.state
    message = make_message()
    db_user = make_user()
    session = AsyncMock()
    submission = make_submission(sub_id=5, status="pending")

    monkeypatch.setattr(edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr("handlers.moderator.get_submission_with_user", AsyncMock(return_value=submission))

    await cmd_moderator_cancel(message, session, state, db_user)

    assert state.state == ModeratorReview.viewing_post
    assert state.data.get("prompt_message_id") is None
    message.answer.assert_awaited_once_with(msg.CANCEL_REVERTED)


async def test_cancel_sub_state_lock_lost_clears_fsm_and_notifies(monkeypatch) -> None:
    state = FakeState(
        {
            "sub_id": 5,
            "media_message_ids": [10, 11],
            "actions_message_id": 12,
            "prompt_message_id": 99,
        }
    )
    state.state = ModeratorReview.editing_caption.state
    message = make_message()
    db_user = make_user()
    session = AsyncMock()

    monkeypatch.setattr(edit_lock, "extend_lock", AsyncMock(return_value=False))

    await cmd_moderator_cancel(message, session, state, db_user)

    assert state.cleared is True
    message.answer.assert_awaited_once_with(msg.MODERATOR_LOCK_LOST)


# ---------------------------------------------------------------------------
# cmd_moderator_cancel — viewing_post closes cleanly
# ---------------------------------------------------------------------------

async def test_cancel_viewing_post_releases_lock_and_clears(monkeypatch) -> None:
    state = FakeState(
        {
            "sub_id": 5,
            "media_message_ids": [10],
            "actions_message_id": 12,
        }
    )
    state.state = ModeratorReview.viewing_post.state
    message = make_message()
    db_user = make_user()
    session = AsyncMock()
    submission = make_submission(sub_id=5, status="pending")

    release = AsyncMock()
    monkeypatch.setattr(edit_lock, "release_lock", release)
    monkeypatch.setattr("handlers.moderator.get_submission_with_user", AsyncMock(return_value=submission))
    monkeypatch.setattr(topics, "update_submission_card", AsyncMock())
    monkeypatch.setattr(topics, "request_topic_title_sync", AsyncMock())

    await cmd_moderator_cancel(message, session, state, db_user)

    assert state.cleared is True
    release.assert_awaited_once_with(session, "submission", "5", db_user.id)
    message.answer.assert_awaited_once_with(msg.CANCEL_OK)


# ---------------------------------------------------------------------------
# handle_noop — shows lock owner in alert
# ---------------------------------------------------------------------------

async def test_handle_noop_shows_lock_owner_name(monkeypatch) -> None:
    callback = make_callback(message=make_message())
    callback.message.message_id = 55
    session = AsyncMock()
    submission = make_submission(sub_id=3, status="pending")
    lock = AsyncMock()
    lock.moderator_id = 1
    owner = make_user(user_id=1, telegram_id=101, username="mod_user")

    monkeypatch.setattr("handlers.moderator.get_submission_by_topic_card_id", AsyncMock(return_value=submission))
    monkeypatch.setattr(edit_lock, "get_active_lock", AsyncMock(return_value=lock))
    monkeypatch.setattr("handlers.moderator.get_user_by_id", AsyncMock(return_value=owner))

    await handle_noop(callback, session)

    callback.answer.assert_awaited_once_with(
        msg.LOCK_NOOP_HELD_BY.format(mod="@mod_user"), show_alert=True
    )
    callback.answer.assert_awaited_once()


async def test_handle_noop_redirects_lock_owner_to_bot_chat(monkeypatch) -> None:
    callback = make_callback(message=make_message())
    callback.message.message_id = 55
    callback.bot.me = AsyncMock(return_value=SimpleNamespace(username="arts_bot"))
    session = AsyncMock()
    submission = make_submission(sub_id=3, status="pending")
    lock = AsyncMock()
    lock.moderator_id = 7
    db_user = make_user(user_id=7, telegram_id=101, username="mod_user")

    monkeypatch.setattr("handlers.moderator.get_submission_by_topic_card_id", AsyncMock(return_value=submission))
    monkeypatch.setattr(edit_lock, "get_active_lock", AsyncMock(return_value=lock))

    await handle_noop(callback, session, db_user)

    callback.answer.assert_awaited_once_with(url="https://t.me/arts_bot?start=review_3")