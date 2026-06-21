from aiogram.fsm.state import State, StatesGroup


class FeedbackFlow(StatesGroup):
    choosing_employee = State()
    rating = State()
    choosing_tags = State()
    contact_full_name = State()
    contact_phone = State()
    comment_choice = State()
    comment_text = State()
    confirm = State()


class CreateInstitution(StatesGroup):
    name = State()
    region = State()
    address = State()


class CreateEmployee(StatesGroup):
    institution_id = State()
    full_name = State()
    position = State()
