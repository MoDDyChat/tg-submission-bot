from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from db.models import TagPresetSection, User
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
    get_banned_users,
    get_or_create_user,
    get_publication,
    get_publication_by_submission,
    get_submission,
    get_submission_media,
    get_submission_with_user,
    get_tag_preset,
    get_tag_preset_section,
    get_tag_preset_section_by_label,
    get_unpublished_publications,
    list_tag_preset_sections,
    list_tag_presets,
    list_tag_presets_grouped,
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

    refreshed = await db_session.scalar(select(User).where(User.id == created_user.id))

    assert is_new is True
    assert is_new_second is False
    assert updated_user.id == created_user.id
    assert refreshed is not None
    assert refreshed.username == "second"
    assert refreshed.full_name == "Second Name"


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
