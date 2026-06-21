from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Employee, FeedbackType, Institution, User
from app.domain.criteria import Criterion, criteria_for_type, tag_label
from app.keyboards.user import (
    comment_keyboard,
    confirm_keyboard,
    contact_keyboard,
    employee_keyboard,
    language_keyboard,
    main_menu_keyboard,
    rating_keyboard,
    tags_keyboard,
)
from app.locales import normalize_lang, t
from app.services.feedback import (
    average_rating,
    create_feedback,
    is_rate_limited,
    list_employees_for_institution,
    list_user_feedback,
)
from app.services.institutions import get_active_institution_by_token
from app.services.notifications import notify_new_feedback
from app.services.users import get_user, record_link_visit, upsert_user
from app.states import FeedbackFlow

router = Router(name="user")


def _criteria(feedback_type: str) -> list[Criterion]:
    return criteria_for_type(feedback_type)


def _criterion_label(criterion: Criterion, language: str | None) -> str:
    return criterion.uz if normalize_lang(language) == "uz" else criterion.ru


async def _bound_user(session: AsyncSession, message: Message | CallbackQuery) -> User | None:
    tg_user = message.from_user
    if tg_user is None:
        return None
    user = await get_user(session, tg_user.id)
    if user is None or user.institution_id is None:
        target = message.message if isinstance(message, CallbackQuery) else message
        await target.answer(t("ru", "not_bound"))
        return None
    return user


async def _show_main_menu(target: Message, user: User, institution: Institution | None = None) -> None:
    text = t(user.language, "main_menu")
    if institution is not None:
        text = f"{institution.name}\n\n{text}"
    await target.answer(text, reply_markup=main_menu_keyboard(user.language))


@router.message(CommandStart())
async def start(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    await state.clear()
    token = command.args.strip() if command.args else None

    if token:
        institution = await get_active_institution_by_token(session, token)
        if institution is None or not institution.token_active:
            await message.answer(t("ru", "invalid_link"))
            return
        if institution.archived:
            await message.answer(t("ru", "archived_institution"))
            return

        is_first_start = await get_user(session, message.from_user.id) is None
        user = await upsert_user(session, message.from_user, institution.id)
        await record_link_visit(session, user.id, institution.id, token)
        await message.answer(t(user.language, "welcome", institution=institution.name))
        if is_first_start:
            await message.answer(t(user.language, "settings_prompt"), reply_markup=language_keyboard())
        await _show_main_menu(message, user)
        return

    existing = await upsert_user(session, message.from_user)
    if existing.institution_id is None:
        await message.answer(t(existing.language, "access_link_required"))
        return
    institution = await session.get(Institution, existing.institution_id)
    await _show_main_menu(message, existing, institution)


@router.message(Command("help"))
async def help_command(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user.id)
    await message.answer(t(user.language if user else "ru", "help"))


@router.message(Command("settings"))
async def settings_command(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user.id)
    await message.answer(t(user.language if user else "ru", "settings_prompt"), reply_markup=language_keyboard())


@router.callback_query(F.data.startswith("lang:"))
async def save_language(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    language = callback.data.split(":", 1)[1]
    user = await upsert_user(session, callback.from_user)
    user.language = normalize_lang(language)
    await callback.message.answer(t(user.language, "language_saved"))
    if user.institution_id:
        await _show_main_menu(callback.message, user)


@router.message(Command("feedback"))
async def feedback_command(message: Message, session: AsyncSession) -> None:
    user = await _bound_user(session, message)
    if user is None:
        return
    await message.answer(t(user.language, "feedback_menu"), reply_markup=main_menu_keyboard(user.language))


@router.callback_query(F.data == "main:settings")
async def settings_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    user = await get_user(session, callback.from_user.id)
    await callback.message.answer(t(user.language if user else "ru", "settings_prompt"), reply_markup=language_keyboard())


@router.callback_query(F.data == "main:my_reviews")
async def my_reviews_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    await _send_my_reviews(callback.message, callback.from_user.id, session)


@router.message(Command("my_reviews"))
async def my_reviews_command(message: Message, session: AsyncSession) -> None:
    await _send_my_reviews(message, message.from_user.id, session)


async def _send_my_reviews(target: Message, user_id: int, session: AsyncSession) -> None:
    user = await get_user(session, user_id)
    items = await list_user_feedback(session, user_id)
    if not items:
        await target.answer(t(user.language if user else "ru", "my_reviews_empty"))
        return

    lines = []
    for item in items:
        title = "сотрудник" if item.feedback_type == FeedbackType.employee else "внедрение"
        subject = item.employee.full_name if item.employee else item.institution.name
        lines.append(
            f"{item.created_at:%Y-%m-%d} | {title} | {subject} | средняя оценка: {average_rating(item.ratings)}"
        )
    await target.answer("\n".join(lines))


@router.callback_query(F.data == "main:employee")
async def start_employee_feedback(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await callback.answer()
    user = await _bound_user(session, callback)
    if user is None:
        return
    employees = await list_employees_for_institution(session, user.institution_id)
    if not employees:
        await callback.message.answer(t(user.language, "no_employees"))
        return

    await state.clear()
    await state.set_state(FeedbackFlow.choosing_employee)
    await state.update_data(
        feedback_type=FeedbackType.employee.value,
        institution_id=user.institution_id,
        ratings={},
        criterion_index=0,
    )
    await callback.message.answer(t(user.language, "identity_banner"))
    await callback.message.answer(t(user.language, "choose_employee"), reply_markup=employee_keyboard(employees, user.language))


@router.callback_query(F.data == "main:implementation")
async def start_implementation_feedback(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    await callback.answer()
    user = await _bound_user(session, callback)
    if user is None:
        return
    limited = await is_rate_limited(session, user.id, user.institution_id, FeedbackType.implementation)
    if limited:
        await callback.message.answer(t(user.language, "spam_ok"), reply_markup=main_menu_keyboard(user.language))
        return

    await state.clear()
    await state.set_state(FeedbackFlow.rating)
    await state.update_data(
        feedback_type=FeedbackType.implementation.value,
        institution_id=user.institution_id,
        ratings={},
        tags=[],
        criterion_index=0,
    )
    await callback.message.answer(t(user.language, "identity_banner"))
    await _ask_current_rating(callback.message, user.language, await state.get_data())


@router.callback_query(FeedbackFlow.choosing_employee, F.data.startswith("employee:"))
async def choose_employee(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await callback.answer()
    user = await _bound_user(session, callback)
    if user is None:
        return
    raw_employee_id = callback.data.split(":", 1)[1]
    employee_id = None if raw_employee_id == "none" else int(raw_employee_id)
    limited = await is_rate_limited(session, user.id, user.institution_id, FeedbackType.employee, employee_id)
    if limited:
        await state.clear()
        await callback.message.answer(t(user.language, "spam_ok"), reply_markup=main_menu_keyboard(user.language))
        return

    await state.set_state(FeedbackFlow.rating)
    await state.update_data(employee_id=employee_id)
    await _ask_current_rating(callback.message, user.language, await state.get_data())


async def _ask_current_rating(target: Message, language: str | None, data: dict[str, Any]) -> None:
    criteria = _criteria(data["feedback_type"])
    index = data.get("criterion_index", 0)
    criterion = criteria[index]
    await target.answer(_criterion_label(criterion, language), reply_markup=rating_keyboard())


@router.callback_query(FeedbackFlow.rating, F.data.startswith("rating:"))
async def handle_rating(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await callback.answer()
    user = await _bound_user(session, callback)
    if user is None:
        return

    value = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    criteria = _criteria(data["feedback_type"])
    index = data.get("criterion_index", 0)
    ratings = dict(data.get("ratings", {}))
    ratings[criteria[index].code] = value

    next_index = index + 1
    await state.update_data(ratings=ratings, criterion_index=next_index)
    if next_index < len(criteria):
        await _ask_current_rating(callback.message, user.language, await state.get_data())
        return

    if data["feedback_type"] == FeedbackType.implementation.value:
        await state.set_state(FeedbackFlow.choosing_tags)
        await state.update_data(tags=[])
        await callback.message.answer(t(user.language, "choose_tags"), reply_markup=tags_keyboard(set(), user.language))
        return

    await _ask_full_name(callback.message, user.language, state)


@router.callback_query(FeedbackFlow.choosing_tags, F.data.startswith("tag:"))
async def choose_tags(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await callback.answer()
    user = await _bound_user(session, callback)
    if user is None:
        return

    code = callback.data.split(":", 1)[1]
    if code == "done":
        await _ask_full_name(callback.message, user.language, state)
        return

    data = await state.get_data()
    tags = set(data.get("tags", []))
    if code in tags:
        tags.remove(code)
    else:
        tags.add(code)
    await state.update_data(tags=list(tags))
    await callback.message.edit_reply_markup(reply_markup=tags_keyboard(tags, user.language))


async def _ask_full_name(target: Message, language: str | None, state: FSMContext) -> None:
    await state.set_state(FeedbackFlow.contact_full_name)
    await target.answer(t(language, "full_name_prompt"))


@router.message(FeedbackFlow.contact_full_name)
async def receive_full_name(message: Message, session: AsyncSession, state: FSMContext) -> None:
    user = await _bound_user(session, message)
    if user is None:
        return
    full_name = (message.text or "").strip()
    if len(full_name) < 2:
        await message.answer(t(user.language, "full_name_invalid"))
        return
    await state.update_data(reviewer_full_name=full_name)
    await state.set_state(FeedbackFlow.contact_phone)
    await message.answer(t(user.language, "phone_prompt"), reply_markup=contact_keyboard(user.language))


@router.message(FeedbackFlow.contact_phone)
async def receive_phone(message: Message, session: AsyncSession, state: FSMContext) -> None:
    user = await _bound_user(session, message)
    if user is None:
        return
    contact = message.contact
    if contact is None:
        await message.answer(t(user.language, "phone_invalid"), reply_markup=contact_keyboard(user.language))
        return
    if contact.user_id != message.from_user.id:
        await message.answer(t(user.language, "phone_not_own"), reply_markup=contact_keyboard(user.language))
        return

    await state.update_data(reviewer_phone=contact.phone_number)
    await message.answer("Номер подтвержден.", reply_markup=ReplyKeyboardRemove())
    await _ask_comment_choice(message, user.language, state)


async def _ask_comment_choice(target: Message, language: str | None, state: FSMContext) -> None:
    await state.set_state(FeedbackFlow.comment_choice)
    await target.answer(t(language, "comment_prompt"), reply_markup=comment_keyboard(language))


@router.callback_query(FeedbackFlow.comment_choice, F.data.startswith("comment:"))
async def comment_choice(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await callback.answer()
    user = await _bound_user(session, callback)
    if user is None:
        return

    choice = callback.data.split(":", 1)[1]
    if choice == "write":
        await state.set_state(FeedbackFlow.comment_text)
        await callback.message.answer(t(user.language, "ask_comment"))
        return
    await state.update_data(comment=None)
    await _show_summary(callback.message, user.language, session, state)


@router.message(FeedbackFlow.comment_text)
async def receive_comment(message: Message, session: AsyncSession, state: FSMContext) -> None:
    user = await _bound_user(session, message)
    if user is None:
        return
    await state.update_data(comment=message.text)
    await _show_summary(message, user.language, session, state)


async def _show_summary(
    target: Message,
    language: str | None,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    await state.set_state(FeedbackFlow.confirm)
    data = await state.get_data()
    criteria = _criteria(data["feedback_type"])
    lines = [t(language, "summary_title")]
    lines.append(f"ФИО: {data.get('reviewer_full_name') or '-'}")
    lines.append(f"Телефон: {data.get('reviewer_phone') or '-'}")
    if data["feedback_type"] == FeedbackType.employee.value:
        employee_id = data.get("employee_id")
        if employee_id:
            employee = await session.get(Employee, employee_id)
            lines.append(f"Сотрудник: {employee.full_name if employee else employee_id}")
        else:
            lines.append("Сотрудник: не указан")
    else:
        selected_tags = data.get("tags", [])
        if selected_tags:
            lines.append("Метки: " + ", ".join(tag_label(tag, normalize_lang(language)) for tag in selected_tags))

    ratings = data.get("ratings", {})
    for criterion in criteria:
        if criterion.code in ratings:
            lines.append(f"{_criterion_label(criterion, language)}: {ratings[criterion.code]}/5")
    lines.append(f"Средняя оценка: {average_rating(ratings)}")
    if data.get("comment"):
        lines.append(f"Комментарий: {data['comment']}")
    await target.answer("\n".join(lines), reply_markup=confirm_keyboard(language))


@router.callback_query(FeedbackFlow.confirm, F.data.startswith("confirm:"))
async def confirm_feedback(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    await callback.answer()
    user = await _bound_user(session, callback)
    if user is None:
        return

    action = callback.data.split(":", 1)[1]
    if action == "edit":
        await state.clear()
        await callback.message.answer(t(user.language, "feedback_menu"), reply_markup=main_menu_keyboard(user.language))
        return

    data = await state.get_data()
    feedback = await create_feedback(
        session=session,
        user_id=user.id,
        institution_id=data["institution_id"],
        feedback_type=FeedbackType(data["feedback_type"]),
        employee_id=data.get("employee_id"),
        reviewer_full_name=data.get("reviewer_full_name"),
        reviewer_phone=data.get("reviewer_phone"),
        ratings=data.get("ratings", {}),
        tags=data.get("tags", []),
        comment=data.get("comment"),
    )
    await notify_new_feedback(bot, session, settings, feedback)
    await state.clear()
    await callback.message.answer(t(user.language, "thanks"), reply_markup=main_menu_keyboard(user.language))
