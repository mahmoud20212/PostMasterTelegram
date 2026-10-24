from aiogram.fsm.state import State, StatesGroup


class PublishingSettingsForm(StatesGroup):
    waiting_for_cycle_duration = State()
