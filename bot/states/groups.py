from aiogram.fsm.state import State, StatesGroup


class GroupForm(StatesGroup):
    waiting_for_identifier = State()
