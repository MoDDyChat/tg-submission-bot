"""Moderator invite deep-link redemption — open to non-moderators.

Deliberately not filtered by ``IsModerator``: the invitee has no role yet.
The router must be included before ``handlers.moderator.router`` so the
``modinvite_`` deep link is not intercepted by ``cmd_start_review`` or the
generic ``common.cmd_start``.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

import core.messages as msg
from core.logging import get_logger
from db.models import User
from handlers.moderator.management import show_moderator_home
from services import moderator_invites
from utils.formatting import user_mention

logger = get_logger(__name__)

router = Router()


@router.message(CommandStart(deep_link=True, magic=F.args.startswith(moderator_invites.INVITE_PREFIX)))
async def cmd_start_moderator_invite(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user: User,
) -> None:
    """Redeem a one-shot moderator invite from a deep link."""
    token = message.text.split(maxsplit=1)[1].removeprefix(moderator_invites.INVITE_PREFIX)

    if db_user.is_moderator:
        await message.answer(
            msg.MODERATOR_INVITE_ALREADY_MODERATOR.format(user=user_mention(db_user))
        )
        await show_moderator_home(message, state)
        return

    if db_user.is_banned:
        await message.answer(msg.MODERATOR_INVITE_BANNED)
        return

    if not await moderator_invites.redeem_invite(session, message.bot, token=token, user=db_user):
        await message.answer(msg.MODERATOR_INVITE_INVALID)
        return

    # Notifications (admin DM + welcome DM) are sent by redeem_invite after its
    # commit — do not duplicate them here.
    await show_moderator_home(message, state)
