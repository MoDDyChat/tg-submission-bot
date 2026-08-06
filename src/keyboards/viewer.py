from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from keyboards.callbacks import ViewerCancelCB


def submission_confirmed_kb(sub_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Отменить предложение",
            callback_data=ViewerCancelCB(sub_id=sub_id).pack(),
        )],
    ])
