"""Shared helpers: constants, tracked-message cleanup, submission view renderer."""

from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

TERMINAL_STATUSES = {"cancelled", "published", "rejected"}

# State data keys that hold message IDs requiring cleanup
_TRACKED_MESSAGE_KEYS = (
    "media_message_ids",     # list[int] — album/media preview
    "actions_message_id",    # int — action buttons message
    "prompt_message_id",     # int — edit/reject prompt
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
