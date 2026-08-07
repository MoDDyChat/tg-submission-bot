"""Shared helpers: constants, tracked-message cleanup, submission view renderer."""

from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

TERMINAL_STATUSES = {"cancelled", "published", "rejected"}

# State data keys that hold message IDs requiring cleanup
_TRACKED_MESSAGE_KEYS = (
    "media_message_ids",     # list[int] — album/media preview
    "actions_message_id",    # int — action buttons message
    "prompt_message_id",     # int — edit/reject prompt
    "caption_copy_message_id",   # int | None — caption copy during editing
    "schedule_message_id",   # int — calendar/time picker
    "wizard_message_id",     # int — tag wizard message
    "management_message_id", # int — moderator home / management menu message
    "media_manager_message_id",  # int | None — media manager screen
    "media_added_message_ids",   # list[int] — newly added media messages
)


async def _delete_tracked_messages(
    bot,
    chat_id: int,
    data: dict,
    *,
    skip_keys: frozenset[str] = frozenset(),
) -> None:
    """Best-effort deletion of all tracked messages from FSM state data."""
    for key in _TRACKED_MESSAGE_KEYS:
        if key in skip_keys:
            continue
        value = data.get(key)
        if value is None:
            continue
        ids = value if isinstance(value, list) else [value]
        for msg_id in ids:
            try:
                await bot.delete_message(chat_id, msg_id)
            except Exception:
                pass


# Imported here so callers that do `from ._helpers import _send_submission_view`
# continue to work unchanged.  New code should import render_submission_view
# directly from handlers.moderator.view.
from .view import render_submission_view  # noqa: E402
_send_submission_view = render_submission_view


async def _drop_pending_publication(session: AsyncSession, sub_id: int) -> int | None:
    """Delete the publication row of *sub_id*; return its id, or None if there was none.

    Used when a submission leaves the ``scheduled`` status through a terminal
    transition (reject) instead of the regular unschedule flow — otherwise the
    publication row survives with ``published_at IS NULL`` and keeps showing up
    in the schedule post, gets its job recreated on every restart and is
    eventually marked dead.

    Only the DB side happens here.  Cancelling the APScheduler job cannot be
    rolled back, so the caller must do it via ``cancel_scheduled`` *after* the
    commit succeeds: a crash in that window merely leaves a job that fires into
    a missing publication (logged and skipped), whereas cancelling first would
    leave a still-``scheduled`` post with no job at all.
    """
    from db.queries import delete_publication, get_publication_by_submission

    pub = await get_publication_by_submission(session, sub_id)
    if pub is None:
        return None

    pub_id = pub.id
    await delete_publication(session, pub_id)
    return pub_id


async def _extend_submission_lock_from_state(
    session: AsyncSession,
    state: FSMContext,
    moderator_id: int,
) -> bool:
    """Extend the submission lock using sub_id from FSM state.

    Returns True if the lock is still owned by *moderator_id*, False if lost/expired.
    Gets ``sub_id`` from FSM state data.
    """
    from core.config import config
    from services import edit_lock

    data = await state.get_data()
    sub_id = data.get("sub_id")
    if not sub_id:
        return False
    return await edit_lock.extend_lock(
        session, "submission", str(sub_id), moderator_id,
        ttl_seconds=config.edit_lock_ttl_seconds,
    )
