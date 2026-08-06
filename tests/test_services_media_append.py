"""Tests for services/media_append.py."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from tests.helpers import FakeSessionFactory, make_message, make_submission


async def test_append_media_to_empty_submission() -> None:
    """Empty submission.media + one photo → (OK, 1), sort_order=0."""
    import services.media_append as m

    submission = make_submission(sub_id=1, media=[])
    session = AsyncMock()

    with patch.object(m, "add_media", AsyncMock()) as mock_add:
        result, count = await m.append_media_to_submission(
            session, submission, [("fid1", "fuid1", "photo")]
        )

    assert result == m.AppendResult.OK
    assert count == 1
    mock_add.assert_awaited_once_with(
        session, submission_id=1, file_id="fid1",
        file_unique_id="fuid1", media_type="photo", sort_order=0,
    )


async def test_append_video_to_photo_post() -> None:
    """Post with one photo + add video → (OK, 1), sort_order=1."""
    import services.media_append as m

    from tests.helpers import make_media

    existing = [make_media(media_id=1, submission_id=1, sort_order=0)]
    submission = make_submission(sub_id=1, media=existing)
    session = AsyncMock()

    with patch.object(m, "add_media", AsyncMock()) as mock_add:
        result, count = await m.append_media_to_submission(
            session, submission, [("fid2", "fuid2", "video")]
        )

    assert result == m.AppendResult.OK
    assert count == 1
    mock_add.assert_awaited_once_with(
        session, submission_id=1, file_id="fid2",
        file_unique_id="fuid2", media_type="video", sort_order=1,
    )


async def test_append_animation_to_photo_post_invalid() -> None:
    """Post with photo + add animation → INVALID_COMPOSITION, add_media not called."""
    import services.media_append as m

    from tests.helpers import make_media

    existing = [make_media(media_id=1, submission_id=1, sort_order=0, media_type="photo")]
    submission = make_submission(sub_id=1, media=existing)
    session = AsyncMock()

    with patch.object(m, "add_media", AsyncMock()) as mock_add:
        result, count = await m.append_media_to_submission(
            session, submission, [("fid2", "fuid2", "animation")]
        )

    assert result == m.AppendResult.INVALID_COMPOSITION
    assert count == 0
    mock_add.assert_not_called()


async def test_append_media_to_text_post_caption_too_long() -> None:
    """Text-only post with long caption + add photo → CAPTION_TOO_LONG."""
    import services.media_append as m

    submission = make_submission(sub_id=1, caption="Long caption", media=[], tags=[])
    session = AsyncMock()

    with (
        patch.object(m, "validate_caption_length", return_value=False) as mock_val,
        patch.object(m, "add_media", AsyncMock()) as mock_add,
    ):
        result, count = await m.append_media_to_submission(
            session, submission, [("fid1", "fuid1", "photo")]
        )

    assert result == m.AppendResult.CAPTION_TOO_LONG
    assert count == 0
    mock_val.assert_called_once_with([], "Long caption", has_media=True)
    mock_add.assert_not_called()


async def test_append_media_empty_items() -> None:
    """new_items=[] → (UNSUPPORTED, 0)."""
    import services.media_append as m

    submission = make_submission(sub_id=1)
    session = AsyncMock()

    with patch.object(m, "add_media", AsyncMock()) as mock_add:
        result, count = await m.append_media_to_submission(session, submission, [])

    assert result == m.AppendResult.UNSUPPORTED
    assert count == 0
    mock_add.assert_not_called()


def test_reset_append_buffers() -> None:
    """reset_append_buffers() clears all module dicts."""
    import services.media_append as m

    m._append_buffers["x"] = []
    m._append_locks["x"] = asyncio.Lock()
    m._append_timestamps["x"] = 1.0
    m._append_sub_ids["x"] = 1
    m._append_done["x"] = asyncio.Event()
    m._append_cancelled[1] = True
    m._sub_write_locks[1] = asyncio.Lock()

    m.reset_append_buffers()

    assert m._append_buffers == {}
    assert m._append_locks == {}
    assert m._append_timestamps == {}
    assert m._append_sub_ids == {}
    assert m._append_done == {}
    assert m._append_cancelled == {}
    assert m._sub_write_locks == {}


async def test_wait_for_pending_append_empty_returns_immediately() -> None:
    """wait_for_pending_append(42) returns immediately when _append_done is empty."""
    import services.media_append as m

    loop = asyncio.get_running_loop()
    start = loop.time()
    await m.wait_for_pending_append(42, timeout=30.0)
    elapsed = loop.time() - start
    assert elapsed < 1.0, "Should return immediately when no events"


async def test_wait_for_pending_append_waits_for_event() -> None:
    """wait_for_pending_append waits until event is set, then completes."""
    import services.media_append as m

    sub_id = 42
    event = asyncio.Event()
    m._append_done["g1"] = event
    m._append_sub_ids["g1"] = sub_id

    async def waiter():
        await m.wait_for_pending_append(sub_id, timeout=1.0)

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)

    assert not task.done(), "Task should be waiting"

    event.set()

    done = await asyncio.wait_for(task, timeout=2.0)
    assert done is None


async def test_multi_album_cancel() -> None:
    """Two in-flight albums for same sub_id, cancel prevents append_media_to_submission."""
    import services.media_append as m

    sub_id = 42
    mod_id = 1
    g1, g2 = "group1", "group2"

    msg1 = make_message(message_id=1, media_group_id=g1)
    msg2 = make_message(message_id=2, media_group_id=g2)

    m._append_buffers[g1] = [msg1]
    m._append_buffers[g2] = [msg2]
    m._append_locks[g1] = asyncio.Lock()
    m._append_locks[g2] = asyncio.Lock()
    m._append_timestamps[g1] = time.monotonic()
    m._append_timestamps[g2] = time.monotonic()
    m._append_sub_ids[g1] = sub_id
    m._append_sub_ids[g2] = sub_id
    m._append_done[g1] = asyncio.Event()
    m._append_done[g2] = asyncio.Event()

    m.cancel_append_for_sub(sub_id)

    with (
        patch("asyncio.sleep", AsyncMock()),
        patch.object(m, "append_media_to_submission", AsyncMock()) as mock_append,
        patch("services.edit_lock.get_active_lock", AsyncMock()) as mock_lock,
        patch.object(m, "get_submission_with_user", AsyncMock()) as mock_get_sub,
    ):
        mock_lock.return_value = MagicMock(moderator_id=mod_id)
        mock_get_sub.return_value = make_submission(sub_id=sub_id)
        session = AsyncMock()
        sf = FakeSessionFactory(session)

        await m._finalize_append(g1, sf, sub_id, mod_id)
        await m._finalize_append(g2, sf, sub_id, mod_id)

    mock_append.assert_not_called()


def test_sub_write_locks_is_dict() -> None:
    """_sub_write_locks is a dict; _finalize_append uses setdefault."""
    import services.media_append as m

    assert isinstance(m._sub_write_locks, dict)
    assert not m._sub_write_locks


async def test_cancel_flag_isolation() -> None:
    """cancel_append_for_sub(42) sets flag; different sub_id not affected."""
    import services.media_append as m

    m.cancel_append_for_sub(42)
    assert m._append_cancelled.get(42) is True
    assert m._append_cancelled.get(99) is None

    m.cancel_append_for_sub(99)
    assert m._append_cancelled.get(42) is True
    assert m._append_cancelled.get(99) is True


async def test_clear_append_cancel() -> None:
    """clear_append_cancel removes flag from _append_cancelled."""
    import services.media_append as m

    m.cancel_append_for_sub(42)
    assert 42 in m._append_cancelled

    m.clear_append_cancel(42)
    assert 42 not in m._append_cancelled


async def test_buffer_append_lock_creation() -> None:
    """buffer_append_media_group creates a per-group lock."""
    import services.media_append as m

    group_id = "g1"
    msg = make_message(message_id=1, media_group_id=group_id)

    with (
        patch("asyncio.sleep", AsyncMock()),
        patch.object(m, "_finalize_append", AsyncMock()),
    ):
        await m.buffer_append_media_group(msg, sub_id=42, moderator_id=1)

    assert group_id in m._append_locks
    assert isinstance(m._append_locks[group_id], asyncio.Lock)


async def test_buffer_append_accumulates_messages() -> None:
    """Multiple messages with same media_group_id accumulate in _append_buffers."""
    import services.media_append as m

    group_id = "g1"
    msg1 = make_message(message_id=1, media_group_id=group_id)
    msg2 = make_message(message_id=2, media_group_id=group_id)
    msg3 = make_message(message_id=3, media_group_id=group_id)

    with (
        patch("asyncio.sleep", AsyncMock()),
        patch.object(m, "_finalize_append", AsyncMock()),
    ):
        await m.buffer_append_media_group(msg1, sub_id=42, moderator_id=1)
        await m.buffer_append_media_group(msg2, sub_id=42, moderator_id=1)
        await m.buffer_append_media_group(msg3, sub_id=42, moderator_id=1)

    assert group_id in m._append_buffers
    assert len(m._append_buffers[group_id]) == 3
    assert m._append_buffers[group_id] == [msg1, msg2, msg3]


async def test_buffer_append_sleep_before_finalize() -> None:
    """_finalize_append awaits asyncio.sleep(_WAIT) before proceeding."""
    import services.media_append as m

    group_id = "g1"
    sub_id = 42
    msg = make_message(message_id=1, media_group_id=group_id)
    session = AsyncMock()
    sf = FakeSessionFactory(session)

    m._append_buffers[group_id] = [msg]
    m._append_locks[group_id] = asyncio.Lock()
    m._append_timestamps[group_id] = time.monotonic()
    m._append_sub_ids[group_id] = sub_id
    m._append_done[group_id] = asyncio.Event()

    with (
        patch("asyncio.sleep", AsyncMock()) as mock_sleep,
        patch.object(m, "append_media_to_submission", AsyncMock(return_value=(m.AppendResult.OK, 1))),
        patch("services.edit_lock.get_active_lock", AsyncMock()) as mock_lock,
        patch.object(m, "get_submission_with_user", AsyncMock()) as mock_get_sub,
    ):
        mock_lock.return_value = MagicMock(moderator_id=1)
        mock_get_sub.return_value = make_submission(sub_id=sub_id, media=[])
        await m._finalize_append(group_id, sf, sub_id, moderator_id=1)

    mock_sleep.assert_awaited_once_with(m._WAIT)


async def test_buffer_append_done_event_lifecycle() -> None:
    """_append_done event created on first message, removed after finalize."""
    import services.media_append as m

    group_id = "g1"
    sub_id = 42
    msg = make_message(message_id=1, media_group_id=group_id)
    session = AsyncMock()
    sf = FakeSessionFactory(session)

    # Trigger event creation via buffer_append_media_group
    with (
        patch("asyncio.sleep", AsyncMock()),
        patch.object(m, "_finalize_append", AsyncMock()),
    ):
        await m.buffer_append_media_group(msg, sub_id=sub_id, moderator_id=1)

    assert group_id in m._append_done
    assert isinstance(m._append_done[group_id], asyncio.Event)
    assert not m._append_done[group_id].is_set()

    # Now run real _finalize_append and verify cleanup
    with (
        patch("asyncio.sleep", AsyncMock()),
        patch.object(m, "append_media_to_submission", AsyncMock(return_value=(m.AppendResult.OK, 1))),
        patch("services.edit_lock.get_active_lock", AsyncMock()) as mock_lock,
        patch.object(m, "get_submission_with_user", AsyncMock()) as mock_get_sub,
    ):
        mock_lock.return_value = MagicMock(moderator_id=1)
        mock_get_sub.return_value = make_submission(sub_id=sub_id, media=[])
        await m._finalize_append(group_id, sf, sub_id, moderator_id=1)

    assert group_id not in m._append_done


async def test_buffer_append_stale_ttl_cleanup() -> None:
    """Stale buffers with timestamp older than _BUFFER_TTL are cleaned up on finalize."""
    import services.media_append as m

    group_id = "g1"
    fresh_group = "g2"
    sub_id = 42
    msg_stale = make_message(message_id=1, media_group_id=group_id)
    msg_fresh = make_message(message_id=2, media_group_id=fresh_group)
    session = AsyncMock()
    sf = FakeSessionFactory(session)

    # Stale buffer — timestamp older than _BUFFER_TTL (300s)
    m._append_buffers[group_id] = [msg_stale]
    m._append_locks[group_id] = asyncio.Lock()
    m._append_timestamps[group_id] = time.monotonic() - 301
    m._append_sub_ids[group_id] = sub_id
    m._append_done[group_id] = asyncio.Event()

    # Fresh buffer (will be finalized)
    m._append_buffers[fresh_group] = [msg_fresh]
    m._append_locks[fresh_group] = asyncio.Lock()
    m._append_timestamps[fresh_group] = time.monotonic()
    m._append_sub_ids[fresh_group] = sub_id
    m._append_done[fresh_group] = asyncio.Event()

    with (
        patch("asyncio.sleep", AsyncMock()),
        patch.object(m, "append_media_to_submission", AsyncMock(return_value=(m.AppendResult.OK, 1))),
        patch("services.edit_lock.get_active_lock", AsyncMock()) as mock_lock,
        patch.object(m, "get_submission_with_user", AsyncMock()) as mock_get_sub,
    ):
        mock_lock.return_value = MagicMock(moderator_id=1)
        mock_get_sub.return_value = make_submission(sub_id=sub_id, media=[])
        await m._finalize_append(fresh_group, sf, sub_id, moderator_id=1)

    # Stale group must be cleaned up entirely
    assert group_id not in m._append_buffers
    assert group_id not in m._append_locks
    assert group_id not in m._append_timestamps
    assert group_id not in m._append_sub_ids
    assert group_id not in m._append_done


async def test_buffer_append_cancellation_prevents_finalize() -> None:
    """_append_cancelled flag prevents _finalize_append from calling append_media_to_submission."""
    import services.media_append as m

    group_id = "g1"
    sub_id = 42
    msg = make_message(message_id=1, media_group_id=group_id)
    session = AsyncMock()
    sf = FakeSessionFactory(session)

    m._append_buffers[group_id] = [msg]
    m._append_locks[group_id] = asyncio.Lock()
    m._append_timestamps[group_id] = time.monotonic()
    m._append_sub_ids[group_id] = sub_id
    m._append_done[group_id] = asyncio.Event()
    m._append_cancelled[sub_id] = True

    with (
        patch("asyncio.sleep", AsyncMock()),
        patch.object(m, "append_media_to_submission", AsyncMock()) as mock_append,
        patch("services.edit_lock.get_active_lock", AsyncMock()),
        patch.object(m, "get_submission_with_user", AsyncMock()),
    ):
        await m._finalize_append(group_id, sf, sub_id, moderator_id=1)

    mock_append.assert_not_called()
