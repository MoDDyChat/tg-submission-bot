from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from db.models import Submission, TagPresetSection, User
from db.queries import (
    add_media,
    ban_user,
    create_message,
    create_publication,
    create_submission,
    create_tag_preset,
    create_tag_preset_section,
    delete_tag_preset,
    delete_tag_preset_section,
    find_tag_preset_conflicts,
    get_active_submissions,
    get_author_stats,
    get_banned_users,
    get_or_create_user,
    get_publication,
    get_publication_by_submission,
    get_scheduled_times_between,
    get_submission,
    get_submission_media,
    get_submission_with_user,
    get_tag_preset,
    get_tag_preset_section,
    get_tag_preset_section_by_label,
    get_unpublished_publications,
    get_users_by_username,
    list_tag_preset_sections,
    list_tag_presets,
    list_tag_presets_grouped,
    mark_publication_dead,
    mark_published,
    unban_user,
    update_publication_time,
    update_submission_caption,
    update_submission_status,
    update_submission_tags,
    update_tag_preset,
    update_tag_preset_section,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_get_or_create_user_upserts_existing_row(db_session) -> None:
    created_user, is_new = await get_or_create_user(
        db_session,
        telegram_id=111,
        username="first",
        full_name="First Name",
    )
    await db_session.commit()

    updated_user, is_new_second = await get_or_create_user(
        db_session,
        telegram_id=111,
        username="second",
        full_name="Second Name",
    )
    await db_session.commit()

    # the upsert goes through Core INSERT ... ON CONFLICT, so the instance kept
    # in the identity map is not refreshed automatically (expire_on_commit=False)
    # — populate_existing forces the loaded row to overwrite it
    refreshed = await db_session.scalar(
        select(User)
        .where(User.id == created_user.id)
        .execution_options(populate_existing=True)
    )

    assert is_new is True
    assert is_new_second is False
    assert updated_user.id == created_user.id
    assert refreshed is not None
    assert refreshed.username == "second"
    assert refreshed.full_name == "Second Name"


@pytest.mark.asyncio
async def test_get_users_by_username_flags_duplicate_as_ambiguous(db_session) -> None:
    """A Telegram handle can be released and re-registered by someone else,
    leaving two rows with the same username. Nothing in the row (including
    ``updated_at``, which changes for unrelated reasons like bans or notes)
    identifies the current holder, so the query must surface both rows
    instead of picking one and silently guessing wrong."""
    old_owner, _ = await get_or_create_user(db_session, 333, "reused_nick", "Old Owner")
    await db_session.commit()

    new_owner, _ = await get_or_create_user(db_session, 334, "reused_nick", "New Owner")
    await db_session.commit()

    found = await get_users_by_username(db_session, "reused_nick")

    assert {user.id for user in found} == {old_owner.id, new_owner.id}


@pytest.mark.asyncio
async def test_get_users_by_username_returns_single_match(db_session) -> None:
    user, _ = await get_or_create_user(db_session, 335, "solo_nick", "Solo Owner")
    await db_session.commit()

    found = await get_users_by_username(db_session, "solo_nick")

    assert [item.id for item in found] == [user.id]


@pytest.mark.asyncio
async def test_ban_and_unban_user_roundtrip(db_session) -> None:
    user, _ = await get_or_create_user(db_session, 222, "artist", "Artist")
    await ban_user(db_session, user.id, "spam")
    await db_session.commit()

    banned_users = await get_banned_users(db_session)
    assert [item.id for item in banned_users] == [user.id]
    assert banned_users[0].ban_reason == "spam"

    await unban_user(db_session, user.id)
    await db_session.commit()

    assert await get_banned_users(db_session) == []


@pytest.mark.asyncio
async def test_tag_preset_section_crud_and_generated_keys(db_session) -> None:
    first = await create_tag_preset_section(db_session, "Категория")
    second = await create_tag_preset_section(db_session, "Персонаж", columns=0)
    await db_session.commit()

    assert first.key == "section_1"
    assert second.key == "section_2"
    assert second.columns == 1

    await update_tag_preset_section(db_session, second.key, label="Герой", columns=4)
    await db_session.commit()

    listed = await list_tag_preset_sections(db_session)
    assert [section.key for section in listed] == ["section_1", "section_2"]
    assert (await get_tag_preset_section(db_session, second.key)).label == "Герой"
    assert (await get_tag_preset_section_by_label(db_session, "Герой")).key == second.key


@pytest.mark.asyncio
async def test_tag_preset_queries_detect_conflicts_and_grouping(db_session) -> None:
    section = TagPresetSection(key="category", label="Категория", columns=3, sort_order=1)
    db_session.add(section)
    await db_session.flush()

    first = await create_tag_preset(db_session, "category", "Арт", "MineShieldArt")
    second = await create_tag_preset(db_session, "category", "Видео", "MineShieldVideo")
    await db_session.commit()

    conflicts = await find_tag_preset_conflicts(
        db_session,
        "category",
        label="Арт",
        tag="OtherTag",
    )
    grouped = await list_tag_presets_grouped(db_session)
    listed = await list_tag_presets(db_session, "category")

    assert [item.id for item in conflicts] == [first.id]
    assert [item.id for item in grouped["category"]] == [first.id, second.id]
    assert [item.tag for item in listed] == ["MineShieldArt", "MineShieldVideo"]

    await update_tag_preset(db_session, second.id, label="Видео обновлённое", tag="MineShieldClip")
    await db_session.commit()

    updated = await get_tag_preset(db_session, second.id)
    assert updated.label == "Видео обновлённое"
    assert updated.tag == "MineShieldClip"

    await delete_tag_preset(db_session, second.id)
    await db_session.commit()
    assert await get_tag_preset(db_session, second.id) is None


@pytest.mark.asyncio
async def test_delete_tag_section_cascades_presets_but_keeps_submission_tags(db_session) -> None:
    user, _ = await get_or_create_user(db_session, 333, "artist", "Artist")
    section = TagPresetSection(key="category", label="Категория", columns=3, sort_order=1)
    db_session.add(section)
    await db_session.flush()
    preset = await create_tag_preset(db_session, "category", "Арт", "MineShieldArt")
    submission = await create_submission(db_session, user.id, "Описание", tags=[preset.tag])
    await db_session.commit()

    await delete_tag_preset_section(db_session, "category")
    await db_session.commit()

    reloaded_submission = await get_submission(db_session, submission.id)
    assert reloaded_submission.tags == ["MineShieldArt"]
    assert await list_tag_presets(db_session, "category") == []


@pytest.mark.asyncio
async def test_submission_and_media_queries_load_related_entities(db_session) -> None:
    user, _ = await get_or_create_user(db_session, 444, "viewer", "Viewer")
    submission = await create_submission(db_session, user.id, "Описание", tags=["One"])
    await add_media(db_session, submission.id, "file2", "unique2", "video", sort_order=1)
    await add_media(db_session, submission.id, "file1", "unique1", "photo", sort_order=0)
    await db_session.commit()

    await update_submission_caption(db_session, submission.id, "Новое описание")
    await update_submission_tags(db_session, submission.id, ["One", "Two"])
    await db_session.commit()

    with_user = await get_submission_with_user(db_session, submission.id)
    media = await get_submission_media(db_session, submission.id)

    assert with_user.user.telegram_id == 444
    assert [item.file_id for item in with_user.media] == ["file1", "file2"]
    assert with_user.caption == "Новое описание"
    assert with_user.tags == ["One", "Two"]
    assert [item.sort_order for item in media] == [0, 1]


@pytest.mark.asyncio
async def test_publication_queries_and_active_submission_filters(db_session) -> None:
    user, _ = await get_or_create_user(db_session, 555, "author", "Author")
    pending = await create_submission(db_session, user.id, "Pending")
    scheduled = await create_submission(db_session, user.id, "Scheduled")
    cancelled = await create_submission(db_session, user.id, "Cancelled")
    await update_submission_status(db_session, scheduled.id, "scheduled")
    await update_submission_status(db_session, cancelled.id, "cancelled")

    publish_at = datetime.now(timezone.utc) + timedelta(hours=1)
    publication = await create_publication(db_session, scheduled.id, "Edited", publish_at)
    await db_session.commit()

    active = await get_active_submissions(db_session)
    unpublished = await get_unpublished_publications(db_session)
    by_submission = await get_publication_by_submission(db_session, scheduled.id)

    assert {item.id for item in active} == {pending.id, scheduled.id}
    assert [item.id for item in unpublished] == [publication.id]
    assert by_submission.id == publication.id

    new_publish_at = publish_at + timedelta(hours=2)
    await update_publication_time(db_session, publication.id, new_publish_at)
    await mark_published(db_session, publication.id, channel_message_id=99, channel_message_ids=[99, 100])
    await db_session.commit()

    refreshed = await get_publication(db_session, publication.id)
    assert refreshed.publish_at == new_publish_at
    assert refreshed.channel_message_id == 99
    assert refreshed.channel_message_ids == [99, 100]
    assert refreshed.published_at is not None


@pytest.mark.asyncio
async def test_create_message_persists_conversation_entry(db_session) -> None:
    user, _ = await get_or_create_user(db_session, 666, "user", "User")
    submission = await create_submission(db_session, user.id, "Caption")
    message = await create_message(db_session, submission.id, 666, "Reply text")
    await db_session.commit()

    assert message.id is not None
    assert message.submission_id == submission.id
    assert message.sender_telegram_id == 666
    assert message.text == "Reply text"


@pytest.mark.asyncio
async def test_get_author_stats_counts_statuses_and_first_seen(db_session) -> None:
    user, _ = await get_or_create_user(db_session, 777, "author", "Author")
    statuses = ("pending", "published", "published", "rejected", "scheduled", "cancelled")
    for status in statuses:
        submission = await create_submission(db_session, user.id, status)
        if status != "pending":
            await update_submission_status(db_session, submission.id, status)
    await db_session.commit()

    stats = await get_author_stats(db_session, user.id)
    created_times = (
        await db_session.execute(
            select(Submission.created_at).where(Submission.user_id == user.id)
        )
    ).scalars().all()

    assert stats.total == len(statuses)
    assert stats.pending == 1
    assert stats.published == 2
    assert stats.rejected == 1
    assert stats.scheduled == 1
    assert stats.cancelled == 1
    assert stats.first_seen == min(created_times)


@pytest.mark.asyncio
async def test_get_author_stats_empty_author(db_session) -> None:
    user, _ = await get_or_create_user(db_session, 778, "lone", "Lone")
    await db_session.commit()

    stats = await get_author_stats(db_session, user.id)

    assert stats.total == 0
    assert stats.published == 0
    assert stats.rejected == 0
    assert stats.pending == 0
    assert stats.scheduled == 0
    assert stats.cancelled == 0
    assert stats.first_seen is None


@pytest.mark.asyncio
async def test_mark_publication_dead_is_idempotent(db_session) -> None:
    user, _ = await get_or_create_user(db_session, 779, "author", "Author")
    submission = await create_submission(db_session, user.id, "Scheduled")
    await update_submission_status(db_session, submission.id, "scheduled")
    publication = await create_publication(
        db_session,
        submission.id,
        "Edited",
        datetime.now(timezone.utc) - timedelta(hours=25),
    )
    await db_session.commit()

    assert await mark_publication_dead(db_session, publication.id) is True
    assert await mark_publication_dead(db_session, publication.id) is False
    await db_session.commit()

    refreshed = await get_publication(db_session, publication.id)
    assert refreshed.dead_at is not None


@pytest.mark.asyncio
async def test_update_publication_time_revives_dead_publication(db_session) -> None:
    user, _ = await get_or_create_user(db_session, 780, "author", "Author")
    submission = await create_submission(db_session, user.id, "Scheduled")
    await update_submission_status(db_session, submission.id, "scheduled")
    publication = await create_publication(
        db_session,
        submission.id,
        "Edited",
        datetime.now(timezone.utc) + timedelta(hours=25),
    )
    await db_session.commit()

    await mark_publication_dead(db_session, publication.id)
    await db_session.commit()
    assert (await get_publication(db_session, publication.id)).dead_at is not None

    new_publish_at = datetime.now(timezone.utc) + timedelta(hours=2)
    await update_publication_time(db_session, publication.id, new_publish_at)
    await db_session.commit()

    refreshed = await get_publication(db_session, publication.id)
    assert refreshed.dead_at is None
    assert refreshed.publish_at == new_publish_at


@pytest.mark.asyncio
async def test_create_direct_message_without_submission(db_session) -> None:
    viewer, _ = await get_or_create_user(db_session, 781, "viewer", "Viewer")
    message = await create_message(
        db_session,
        None,
        999,
        "Direct text",
        target_user_id=viewer.id,
    )
    await db_session.commit()

    assert message.id is not None
    assert message.submission_id is None
    assert message.target_user_id == viewer.id
    assert message.sender_telegram_id == 999
    assert message.text == "Direct text"


@pytest.mark.asyncio
async def test_get_scheduled_times_between_filters_and_orders(db_session) -> None:
    user, _ = await get_or_create_user(db_session, 782, "author", "Author")
    window_start = datetime(2026, 4, 15, tzinfo=timezone.utc)
    window_end = datetime(2026, 4, 16, tzinfo=timezone.utc)

    in_window_early = await create_submission(db_session, user.id, "Early")
    in_window_late = await create_submission(db_session, user.id, "Late")
    at_start_boundary = await create_submission(db_session, user.id, "AtStart")
    at_end_boundary = await create_submission(db_session, user.id, "AtEnd")
    already_published = await create_submission(db_session, user.id, "Published")
    dead = await create_submission(db_session, user.id, "Dead")
    excluded = await create_submission(db_session, user.id, "Excluded")
    for sub in (
        in_window_early,
        in_window_late,
        at_start_boundary,
        at_end_boundary,
        already_published,
        dead,
        excluded,
    ):
        await update_submission_status(db_session, sub.id, "scheduled")

    pub_early = await create_publication(
        db_session, in_window_early.id, None, datetime(2026, 4, 15, 9, 0, tzinfo=timezone.utc)
    )
    pub_late = await create_publication(
        db_session, in_window_late.id, None, datetime(2026, 4, 15, 18, 0, tzinfo=timezone.utc)
    )
    pub_at_start = await create_publication(
        db_session, at_start_boundary.id, None, window_start
    )
    pub_at_end = await create_publication(
        db_session, at_end_boundary.id, None, window_end
    )
    pub_published = await create_publication(
        db_session, already_published.id, None, datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
    )
    pub_dead = await create_publication(
        db_session, dead.id, None, datetime(2026, 4, 15, 11, 0, tzinfo=timezone.utc)
    )
    pub_excluded = await create_publication(
        db_session, excluded.id, None, datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
    )
    await db_session.commit()

    await mark_published(db_session, pub_published.id)
    await mark_publication_dead(db_session, pub_dead.id)
    await db_session.commit()

    result = await get_scheduled_times_between(
        db_session, window_start, window_end, exclude_submission_id=excluded.id
    )

    # window is half-open [start, end): the boundary at window_end must be excluded,
    # the one exactly at window_start must be included; published/dead/excluded are dropped.
    assert [sub_id for _publish_at, sub_id in result] == [
        at_start_boundary.id,
        in_window_early.id,
        in_window_late.id,
    ]
    assert [publish_at for publish_at, _sub_id in result] == [
        window_start,
        datetime(2026, 4, 15, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 15, 18, 0, tzinfo=timezone.utc),
    ]

    # sanity: publication ids created but unused as objects above are the ones filtered out
    assert {pub_early.submission_id, pub_late.submission_id, pub_at_start.submission_id} == {
        in_window_early.id,
        in_window_late.id,
        at_start_boundary.id,
    }
    assert pub_at_end.submission_id == at_end_boundary.id
    assert pub_excluded.submission_id == excluded.id
