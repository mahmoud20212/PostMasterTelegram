from aiogram.fsm.state import State, StatesGroup


class MessageForm(StatesGroup):
    waiting_for_text = State()
