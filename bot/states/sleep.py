from aiogram.fsm.state import State, StatesGroup


class SleepForm(StatesGroup):
    waiting_for_work_hours = State()
    waiting_for_sleep_hours = State()
