from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import core.messages as msg
from core.exceptions import PublishFailedError
from handlers.moderator import publish_now
from states.moderator import ModeratorReview
from tests.helpers import (
    FakeSessionFactory,
    FakeState,
    make_callback,
    make_message,
    make_publication,
    make_submission,
    make_user,
)


def _confirm_state() -> FakeState:
    return FakeState(
        {
            "sub_id": 9,
            "media_message_ids": [10],
            "actions_message_id": 11,
            "schedule_message_id": 12,
        }
    )


def _patch_common(monkeypatch, *, submission, transition) -> AsyncMock:
    monkeypatch.setattr(
        publish_now, "get_submission_with_user", AsyncMock(return_value=submission)
    )
    monkeypatch.setattr(publish_now.edit_lock, "extend_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(publish_now, "transition_submission_status", transition)
    delete_tracked = AsyncMock()
    monkeypatch.setattr(publish_now, "_delete_tracked_messages", delete_tracked)
    return delete_tracked


async def test_confirm_wins_race_creates_one_publication_and_publishes_once(monkeypatch) -> None:
    main_session = AsyncMock()
    state = _confirm_state()
    callback = make_callback(message=make_message())
    db_user = make_user(user_id=2, telegram_id=202)
    submission = make_submission(sub_id=9, status="pending")
    publication = make_publication(pub_id=3, submission_id=9)
    transition = AsyncMock(return_value=True)

    delete_tracked = _patch_common(monkeypatch, submission=submission, transition=transition)
    create_pub = AsyncMock(return_value=publication)
    monkeypatch.setattr(publish_now, "create_publication", create_pub)
    publish = AsyncMock()
    monkeypatch.setattr(publish_now, "publish_post", publish)
    monkeypatch.setattr(publish_now.edit_lock, "release_lock", AsyncMock())
    monkeypatch.setattr(publish_now, "_render_queue", AsyncMock())
    monkeypatch.setattr(publish_now, "request_dashboard", MagicMock())
    monkeypatch.setattr(publish_now, "request_author_card", MagicMock())

    await publish_now.handle_publish_now_confirm(callback, main_session, state, db_user)

    # The atomic claim happens before the publication is created.
    assert transition.await_args_list[0].args == (main_session, 9, "scheduled")
    assert transition.await_args_list[0].kwargs == {"expected": publish_now.PUBLISHABLE_STATUSES}
    create_pub.assert_awaited_once()
    publish.assert_awaited_once()
    assert state.cleared is True
    delete_tracked.assert_awaited_once()
    callback.answer.assert_awaited_once_with()


async def test_confirm_loses_race_skips_publication_and_publish(monkeypatch) -> None:
    main_session = AsyncMock()
    state = _confirm_state()
    callback = make_callback(message=make_message())
    db_user = make_user(user_id=2, telegram_id=202)
    submission = make_submission(sub_id=9, status="pending")
    transition = AsyncMock(return_value=False)

    delete_tracked = _patch_common(monkeypatch, submission=submission, transition=transition)
    create_pub = AsyncMock(return_value=make_publication(pub_id=3, submission_id=9))
    monkeypatch.setattr(publish_now, "create_publication", create_pub)
    publish = AsyncMock()
    monkeypatch.setattr(publish_now, "publish_post", publish)

    await publish_now.handle_publish_now_confirm(callback, main_session, state, db_user)

    create_pub.assert_not_awaited()
    publish.assert_not_awaited()
    callback.answer.assert_awaited_once_with(msg.SUBMISSION_NOT_AVAILABLE, show_alert=True)
    delete_tracked.assert_awaited_once()
    assert state.cleared is True


async def test_recovery_after_publish_failure_reverts_and_drops_publication(monkeypatch) -> None:
    main_session = AsyncMock()
    recovery_session = AsyncMock()
    state = _confirm_state()
    callback = make_callback(message=make_message())
    db_user = make_user(user_id=2, telegram_id=202)
    submission = make_submission(sub_id=9, status="pending")
    publication = make_publication(pub_id=3, submission_id=9)
    # First call: main-path claim won; second call: recovery revert also won.
    transition = AsyncMock(side_effect=[True, True])

    _patch_common(monkeypatch, submission=submission, transition=transition)
    monkeypatch.setattr(
        publish_now, "create_publication", AsyncMock(return_value=publication)
    )
    monkeypatch.setattr(
        publish_now, "publish_post", AsyncMock(side_effect=PublishFailedError("boom"))
    )
    get_pub = AsyncMock(return_value=publication)
    monkeypatch.setattr(publish_now, "get_publication", get_pub)
    delete_pub = AsyncMock()
    monkeypatch.setattr(publish_now, "delete_publication", delete_pub)
    monkeypatch.setattr(publish_now, "session_factory", FakeSessionFactory(recovery_session))

    await publish_now.handle_publish_now_confirm(callback, main_session, state, db_user)

    assert transition.await_count == 2
    assert transition.await_args_list[1].args == (recovery_session, 9, "pending")
    assert transition.await_args_list[1].kwargs == {"expected": {"scheduled"}}
    delete_pub.assert_awaited_once_with(recovery_session, publication.id)
    assert state.state == ModeratorReview.viewing_post
    callback.message.answer.assert_awaited_once_with(
        msg.PUBLISH_NOW_FAILED.format(sub_id=9, error="boom")
    )


async def test_recovery_keeps_publication_when_another_actor_left_scheduled(monkeypatch) -> None:
    """A lost revert means a terminal transition happened elsewhere — do not clobber it."""
    main_session = AsyncMock()
    recovery_session = AsyncMock()
    state = _confirm_state()
    callback = make_callback(message=make_message())
    db_user = make_user(user_id=2, telegram_id=202)
    submission = make_submission(sub_id=9, status="pending")
    publication = make_publication(pub_id=3, submission_id=9)
    # Main claim won; the recovery revert lost (e.g. the post was rejected meanwhile).
    transition = AsyncMock(side_effect=[True, False])

    _patch_common(monkeypatch, submission=submission, transition=transition)
    monkeypatch.setattr(
        publish_now, "create_publication", AsyncMock(return_value=publication)
    )
    monkeypatch.setattr(
        publish_now, "publish_post", AsyncMock(side_effect=PublishFailedError("boom"))
    )
    monkeypatch.setattr(publish_now, "get_publication", AsyncMock(return_value=publication))
    delete_pub = AsyncMock()
    monkeypatch.setattr(publish_now, "delete_publication", delete_pub)
    monkeypatch.setattr(publish_now, "session_factory", FakeSessionFactory(recovery_session))

    await publish_now.handle_publish_now_confirm(callback, main_session, state, db_user)

    delete_pub.assert_not_awaited()
    recovery_session.commit.assert_not_awaited()
    assert state.state == ModeratorReview.viewing_post
