from aiogram.fsm.state import State, StatesGroup


class RegistrationState(StatesGroup):
    first_name = State()
    last_name = State()
    phone = State()
    group_name = State()


class QuizState(StatesGroup):
    active = State()


class AdminState(StatesGroup):
    import_file = State()
    add_subject_name = State()
    edit_subject_name = State()
    add_topic_subject = State()
    add_topic_name = State()
    edit_topic_name = State()

    add_question_subject = State()
    add_question_topic = State()
    add_question_text = State()
    add_question_image_count = State()
    add_question_crop = State()
    add_question_a = State()
    add_question_b = State()
    add_question_c = State()
    add_question_d = State()
    add_question_correct = State()
    add_question_explanation = State()

    edit_question_text = State()
    edit_question_a = State()
    edit_question_b = State()
    edit_question_c = State()
    edit_question_d = State()



class GroupQuizState(StatesGroup):
    waiting_size = State()

class ErrorQuizState(StatesGroup):
    active = State()

