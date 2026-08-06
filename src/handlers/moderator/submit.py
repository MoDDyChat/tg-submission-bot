"""Moderator submit mode: receive submissions while in submitting_post state."""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User
from services import submission_intake
from states.moderator import ModeratorReview

router = Router()

_STATE = StateFilter(ModeratorReview.submitting_post)


@router.message(F.media_group_id, F.photo | F.video | F.animation | F.document, _STATE)
async def handle_moderator_media_group(
    message: Message,
    db_user: User,
) -> None:
    await submission_intake.buffer_media_group_message(message, db_user)


@router.message(F.photo | F.video | F.animation | F.document, _STATE)
async def handle_moderator_single_media(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    await submission_intake.submit_single_media(message, session, db_user)


@router.message(F.text & ~F.text.startswith("/"), _STATE)
async def handle_moderator_text(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    await submission_intake.submit_text(message, session, db_user)
