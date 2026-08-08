"""Tag preset and tag preset section queries.

Advisory xact locks serialise concurrent MAX(sort_order)+INSERT operations so
that two simultaneous creates never race on the same sort_order value.
"""

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from db.models import TagPresetEntry, TagPresetSection


async def list_tag_preset_sections(session: AsyncSession) -> list[TagPresetSection]:
    stmt = (
        select(TagPresetSection)
        .order_by(TagPresetSection.sort_order.asc(), TagPresetSection.key.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_tag_preset_section(
    session: AsyncSession,
    section_key: str,
) -> TagPresetSection | None:
    stmt = select(TagPresetSection).where(TagPresetSection.key == section_key)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_tag_preset_section_by_label(
    session: AsyncSession,
    label: str,
) -> TagPresetSection | None:
    stmt = select(TagPresetSection).where(TagPresetSection.label == label)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _build_next_tag_preset_section_key(session: AsyncSession) -> str:
    """Return the next unused ``section_N`` key.

    Must be called inside the ``pg_advisory_xact_lock`` acquired by
    ``create_tag_preset_section`` to be race-safe.
    """
    result = await session.execute(
        select(TagPresetSection.key).where(TagPresetSection.key.like("section_%"))
    )
    existing_keys = set(result.scalars().all())

    index = 1
    while True:
        candidate = f"section_{index}"
        if candidate not in existing_keys:
            return candidate
        index += 1


async def create_tag_preset_section(
    session: AsyncSession,
    label: str,
    *,
    columns: int = 3,
) -> TagPresetSection:
    """Create a new tag preset section.

    Uses ``pg_advisory_xact_lock`` to serialise concurrent MAX(sort_order)+INSERT
    so that two simultaneous creates cannot race on the same sort_order or key.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('tag_preset_section_order'))")
    )

    current_max = await session.execute(select(func.max(TagPresetSection.sort_order)))
    sort_order = (current_max.scalar_one() or 0) + 1

    section = TagPresetSection(
        key=await _build_next_tag_preset_section_key(session),
        label=label,
        columns=max(1, columns),
        sort_order=sort_order,
    )
    session.add(section)
    await session.flush()
    return section


async def update_tag_preset_section(
    session: AsyncSession,
    section_key: str,
    *,
    label: str | None = None,
    columns: int | None = None,
) -> None:
    values: dict[str, object] = {"updated_at": func.now()}
    if label is not None:
        values["label"] = label
    if columns is not None:
        values["columns"] = max(1, columns)

    stmt = update(TagPresetSection).where(TagPresetSection.key == section_key).values(**values)
    await session.execute(stmt)
    await session.flush()


async def delete_tag_preset_section(
    session: AsyncSession,
    section_key: str,
) -> None:
    stmt = delete(TagPresetSection).where(TagPresetSection.key == section_key)
    await session.execute(stmt)
    await session.flush()


async def list_tag_presets(
    session: AsyncSession,
    preset_type: str,
) -> list[TagPresetEntry]:
    stmt = (
        select(TagPresetEntry)
        .where(TagPresetEntry.preset_type == preset_type)
        .order_by(TagPresetEntry.sort_order.asc(), TagPresetEntry.id.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_tag_presets_grouped(
    session: AsyncSession,
) -> dict[str, list[TagPresetEntry]]:
    sections = await list_tag_preset_sections(session)
    grouped: dict[str, list[TagPresetEntry]] = {
        section.key: []
        for section in sections
    }

    stmt = select(TagPresetEntry).order_by(
        TagPresetEntry.sort_order.asc(),
        TagPresetEntry.id.asc(),
    )
    result = await session.execute(stmt)
    for preset in result.scalars().all():
        grouped.setdefault(preset.preset_type, []).append(preset)
    return grouped


async def get_tag_preset(
    session: AsyncSession,
    preset_id: int,
) -> TagPresetEntry | None:
    stmt = select(TagPresetEntry).where(TagPresetEntry.id == preset_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def find_tag_preset_conflicts(
    session: AsyncSession,
    preset_type: str,
    *,
    label: str,
    tag: str,
    exclude_id: int | None = None,
) -> list[TagPresetEntry]:
    stmt = select(TagPresetEntry).where(
        TagPresetEntry.preset_type == preset_type,
        or_(TagPresetEntry.label == label, TagPresetEntry.tag == tag),
    )
    if exclude_id is not None:
        stmt = stmt.where(TagPresetEntry.id != exclude_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


MAX_PRESET_TAG_LENGTH = 255  # длина колонки tag_presets.tag


def _validate_tag_length(tag: str) -> None:
    """Раньше тег упирался в 32 символа из-за callback_data; теперь кнопка несёт id,
    и единственный оставшийся предел — размер колонки."""
    if len(tag) > MAX_PRESET_TAG_LENGTH:
        raise ValueError(
            f"tag must be at most {MAX_PRESET_TAG_LENGTH} characters, got {len(tag)}"
        )


async def create_tag_preset(
    session: AsyncSession,
    preset_type: str,
    label: str,
    tag: str,
) -> TagPresetEntry:
    """Create a new tag preset entry.

    Uses a per-section ``pg_advisory_xact_lock`` to serialise concurrent
    MAX(sort_order)+INSERT within the same section.

    Raises ``ValueError`` if *tag* does not fit the column.
    """
    _validate_tag_length(tag)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:section))").bindparams(
            section=preset_type
        )
    )

    current_max = await session.execute(
        select(func.max(TagPresetEntry.sort_order)).where(
            TagPresetEntry.preset_type == preset_type
        )
    )
    sort_order = (current_max.scalar_one() or 0) + 1

    preset = TagPresetEntry(
        preset_type=preset_type,
        label=label,
        tag=tag,
        sort_order=sort_order,
    )
    session.add(preset)
    await session.flush()
    return preset


async def update_tag_preset(
    session: AsyncSession,
    preset_id: int,
    *,
    label: str | None = None,
    tag: str | None = None,
) -> None:
    values: dict[str, object] = {"updated_at": func.now()}
    if label is not None:
        values["label"] = label
    if tag is not None:
        _validate_tag_length(tag)
        values["tag"] = tag

    stmt = update(TagPresetEntry).where(TagPresetEntry.id == preset_id).values(**values)
    await session.execute(stmt)
    await session.flush()


async def delete_tag_preset(
    session: AsyncSession,
    preset_id: int,
) -> None:
    stmt = delete(TagPresetEntry).where(TagPresetEntry.id == preset_id)
    await session.execute(stmt)
    await session.flush()
