from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import core.messages as msg
from core.logging import get_logger, fmt_user
from core.rules_config import get_rules
from db.models import User

logger = get_logger(__name__)

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db_user: User) -> None:
    logger.debug("%s вызвал /start", fmt_user(db_user))
    await state.clear()
    await message.answer(get_rules(), parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message, db_user: User) -> None:
    logger.debug("%s вызвал /help", fmt_user(db_user))
    await message.answer(msg.HELP, parse_mode="HTML")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer(msg.CANCEL_NOTHING)
        return
    await state.clear()
    await message.answer(msg.CANCEL_OK)
