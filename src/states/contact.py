from aiogram.fsm.state import State, StatesGroup


class ContactViewer(StatesGroup):
    writing_message = State()
