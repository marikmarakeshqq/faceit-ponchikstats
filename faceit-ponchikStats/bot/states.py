from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    waiting_player_nickname = State()
    waiting_custom_interval = State()
    waiting_faceit_api_key = State()

