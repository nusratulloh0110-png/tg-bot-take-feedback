from pathlib import Path
from uuid import UUID

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Employee, FeedbackStatus, Institution
from app.domain.criteria import criterion_label, tag_label
from app.keyboards.admin import (
    admin_menu_keyboard,
    employee_actions_keyboard,
    employees_keyboard,
    institution_actions_keyboard,
    institutions_keyboard,
    review_status_keyboard,
)
from app.services.admins import add_admin, is_admin
from app.services.analytics import dashboard_summary
from app.services.export import export_feedback_pdf, export_feedback_xlsx
from app.services.feedback import average_rating, list_recent_feedback, update_feedback_status
from app.services.institutions import create_institution, reissue_token
from app.services.tokens import build_deep_link
from app.states import CreateEmployee, CreateInstitution

router = Router(name="admin")


STATUS_LABELS = {
    FeedbackStatus.new: "Новый",
    FeedbackStatus.reviewed: "Изучено",
    FeedbackStatus.in_progress: "В работе",
    FeedbackStatus.closed: "Закрыто",
}


TYPE_LABELS = {
    "employee": "Оценка сотрудника",
    "implementation": "Оценка внедрения",
}


async def _is_admin_message(message: Message, session: AsyncSession, settings: Settings) -> bool:
    allowed = await is_admin(session, message.from_user.id, settings)
    if not allowed:
        await message.answer(
            "Нет доступа. Администраторов больше нельзя добавить паролем. "
            "Попросите действующего администратора добавить ваш Telegram ID через /add_admin."
        )
    return allowed


async def _is_admin_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> bool:
    allowed = await is_admin(session, callback.from_user.id, settings)
    if not allowed:
        await callback.answer("Нет доступа", show_alert=True)
    return allowed


async def _bot_username(bot: Bot, settings: Settings) -> str:
    if settings.bot_username:
        return settings.bot_username
    me = await bot.get_me()
    return me.username or ""


async def _institution_link(bot: Bot, settings: Settings, institution: Institution) -> str:
    return build_deep_link(await _bot_username(bot, settings), institution.token)


def _parse_optional_institution_id(args: str | None) -> int | None:
    if not args:
        return None
    try:
        return int(args.strip())
    except ValueError:
        return None


def _message_text(message: Message) -> str | None:
    text = (message.text or "").strip()
    return text or None


def _format_ratings(ratings: dict[str, int], feedback_type: str) -> str:
    if not ratings:
        return "-"
    return "\n".join(
        f"- {criterion_label(code, feedback_type)}: {value}/5"
        for code, value in ratings.items()
    )


@router.message(Command("admin"))
async def admin_login(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not await _is_admin_message(message, session, settings):
        return
    await state.clear()
    await message.answer("Админ-панель:", reply_markup=admin_menu_keyboard())


@router.message(Command("add_admin"))
async def add_admin_command(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin_message(message, session, settings):
        return
    if not command.args:
        await message.answer("Формат: /add_admin telegram_id")
        return
    try:
        user_id = int(command.args.strip())
    except ValueError:
        await message.answer("Telegram ID должен быть числом.")
        return
    await add_admin(session, user_id)
    await message.answer(f"Администратор добавлен: {user_id}")


@router.message(Command("add_institution"))
async def add_institution_command(
    message: Message,
    command: CommandObject,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not await _is_admin_message(message, session, settings):
        return

    if command.args:
        parts = [part.strip() for part in command.args.split("|")]
        if not parts[0]:
            await message.answer("Укажите название учреждения.")
            return
        institution = await create_institution(
            session,
            parts[0],
            parts[1] if len(parts) > 1 else None,
            parts[2] if len(parts) > 2 else None,
        )
        await _send_institution_card(message, bot, settings, institution)
        return

    await state.set_state(CreateInstitution.name)
    await message.answer("Название учреждения:")


@router.message(CreateInstitution.name)
async def create_institution_name(message: Message, state: FSMContext) -> None:
    name = _message_text(message)
    if not name:
        await message.answer("Введите название учреждения текстом.")
        return
    await state.update_data(name=name)
    await state.set_state(CreateInstitution.region)
    await message.answer("Регион или '-' чтобы пропустить:")


@router.message(CreateInstitution.region)
async def create_institution_region(message: Message, state: FSMContext) -> None:
    text = _message_text(message)
    if text is None:
        await message.answer("Введите регион текстом или '-' чтобы пропустить.")
        return
    region = None if text == "-" else text
    await state.update_data(region=region)
    await state.set_state(CreateInstitution.address)
    await message.answer("Адрес или '-' чтобы пропустить:")


@router.message(CreateInstitution.address)
async def create_institution_address(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    text = _message_text(message)
    if text is None:
        await message.answer("Введите адрес текстом или '-' чтобы пропустить.")
        return
    address = None if text == "-" else text
    institution = await create_institution(session, data["name"], data.get("region"), address)
    await state.clear()
    await _send_institution_card(message, bot, settings, institution)


async def _send_institution_card(message: Message, bot: Bot, settings: Settings, institution: Institution) -> None:
    link = await _institution_link(bot, settings, institution)
    text = (
        "Учреждение\n"
        f"ID: {institution.id}\n"
        f"Название: {institution.name}\n"
        f"Регион: {institution.region or '-'}\n"
        f"Адрес: {institution.address or '-'}\n"
        f"Ссылка активна: {'да' if institution.token_active else 'нет'}\n"
        f"Архив: {'да' if institution.archived else 'нет'}\n"
        f"Ссылка: {link}"
    )
    await message.answer(text, reply_markup=institution_actions_keyboard(institution.id, link))


@router.message(Command("add_employee"))
async def add_employee_command(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not await _is_admin_message(message, session, settings):
        return

    if command.args:
        parts = [part.strip() for part in command.args.split("|")]
        if len(parts) < 2:
            await message.answer("Формат: /add_employee institution_id | ФИО | должность")
            return
        try:
            institution_id = int(parts[0])
        except ValueError:
            await message.answer("ID учреждения должен быть числом.")
            return
        if not parts[1]:
            await message.answer("Укажите ФИО сотрудника.")
            return
        institution = await session.get(Institution, institution_id)
        if institution is None:
            await message.answer("Учреждение не найдено.")
            return
        employee = Employee(institution_id=institution.id, full_name=parts[1], position=parts[2] if len(parts) > 2 else None)
        session.add(employee)
        await session.flush()
        await message.answer(f"Сотрудник добавлен: #{employee.id} {employee.full_name}")
        return

    await state.set_state(CreateEmployee.institution_id)
    await message.answer("ID учреждения:")


@router.message(CreateEmployee.institution_id)
async def create_employee_institution(message: Message, session: AsyncSession, state: FSMContext) -> None:
    text = _message_text(message)
    if text is None:
        await message.answer("Введите числовой ID учреждения.")
        return
    try:
        institution_id = int(text)
    except ValueError:
        await message.answer("Введите числовой ID учреждения.")
        return
    institution = await session.get(Institution, institution_id)
    if institution is None:
        await message.answer("Учреждение не найдено.")
        return
    await state.update_data(institution_id=institution_id)
    await state.set_state(CreateEmployee.full_name)
    await message.answer("ФИО сотрудника-внедренца:")


@router.message(CreateEmployee.full_name)
async def create_employee_name(message: Message, state: FSMContext) -> None:
    full_name = _message_text(message)
    if not full_name:
        await message.answer("Введите ФИО сотрудника текстом.")
        return
    await state.update_data(full_name=full_name)
    await state.set_state(CreateEmployee.position)
    await message.answer("Должность или '-' чтобы пропустить:")


@router.message(CreateEmployee.position)
async def create_employee_position(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    text = _message_text(message)
    if text is None:
        await message.answer("Введите должность текстом или '-' чтобы пропустить.")
        return
    position = None if text == "-" else text
    employee = Employee(institution_id=data["institution_id"], full_name=data["full_name"], position=position)
    session.add(employee)
    await session.flush()
    await state.clear()
    await message.answer(f"Сотрудник добавлен: #{employee.id} {employee.full_name}")


@router.message(Command("institutions"))
async def institutions_command(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not await _is_admin_message(message, session, settings):
        return
    await _send_institutions(message, session)


async def _send_institutions(target: Message, session: AsyncSession) -> None:
    result = await session.scalars(select(Institution).order_by(Institution.id.desc()).limit(50))
    institutions = list(result)
    if not institutions:
        await target.answer("Учреждений пока нет. Создайте через /add_institution.")
        return
    await target.answer("Учреждения:", reply_markup=institutions_keyboard(institutions))


@router.callback_query(F.data == "admin:institutions")
async def institutions_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    await _send_institutions(callback.message, session)


@router.message(Command("employees"))
async def employees_command(message: Message, command: CommandObject, session: AsyncSession, settings: Settings) -> None:
    if not await _is_admin_message(message, session, settings):
        return
    try:
        institution_id = int(command.args.strip()) if command.args else None
    except ValueError:
        await message.answer("Формат: /employees institution_id")
        return
    await _send_employees(message, session, institution_id)


@router.callback_query(F.data == "admin:employees")
async def employees_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    await _send_employees(callback.message, session)


async def _send_employees(target: Message, session: AsyncSession, institution_id: int | None = None) -> None:
    query = select(Employee).order_by(Employee.id.desc()).limit(50)
    if institution_id is not None:
        query = select(Employee).where(Employee.institution_id == institution_id).order_by(Employee.id.desc()).limit(50)
    result = await session.scalars(query)
    employees = list(result)
    if not employees:
        await target.answer("Сотрудников пока нет. Добавьте через /add_employee.")
        return
    await target.answer("Сотрудники:", reply_markup=employees_keyboard(employees))


@router.callback_query(F.data.startswith("employee_admin:"))
async def employee_card(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    employee = await session.get(Employee, int(callback.data.split(":", 1)[1]))
    if employee is None:
        await callback.message.answer("Сотрудник не найден.")
        return
    text = (
        f"#{employee.id} {employee.full_name}\n"
        f"Должность: {employee.position or '-'}\n"
        f"Учреждение ID: {employee.institution_id}\n"
        f"Архив: {'да' if employee.archived else 'нет'}"
    )
    await callback.message.answer(text, reply_markup=employee_actions_keyboard(employee.id, employee.archived))


@router.callback_query(F.data.startswith("employee_archive:"))
async def employee_archive(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    employee = await session.get(Employee, int(callback.data.split(":", 1)[1]))
    if employee is None:
        await callback.message.answer("Сотрудник не найден.")
        return
    employee.archived = not employee.archived
    await callback.message.answer("Статус сотрудника обновлен.")


@router.callback_query(F.data.startswith("inst:"))
async def institution_card(callback: CallbackQuery, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    institution_id = int(callback.data.split(":", 1)[1])
    institution = await session.get(Institution, institution_id)
    if institution is None:
        await callback.message.answer("Учреждение не найдено.")
        return
    await _send_institution_card(callback.message, bot, settings, institution)


@router.callback_query(F.data.startswith("inst_deactivate:"))
async def deactivate_institution(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    institution = await session.get(Institution, int(callback.data.split(":", 1)[1]))
    if institution:
        institution.token_active = False
        await callback.message.answer("Ссылка деактивирована.")


@router.callback_query(F.data.startswith("inst_reissue:"))
async def reissue_institution(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    institution = await session.get(Institution, int(callback.data.split(":", 1)[1]))
    if institution is None:
        await callback.message.answer("Учреждение не найдено.")
        return
    await reissue_token(session, institution)
    link = await _institution_link(bot, settings, institution)
    await callback.message.answer(f"Новая ссылка: {link}", reply_markup=institution_actions_keyboard(institution.id, link))


@router.callback_query(F.data.startswith("inst_archive:"))
async def archive_institution(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    institution = await session.get(Institution, int(callback.data.split(":", 1)[1]))
    if institution:
        institution.archived = True
        institution.token_active = False
        await callback.message.answer("Учреждение архивировано.")


@router.message(Command("reviews"))
async def reviews_command(message: Message, command: CommandObject, session: AsyncSession, settings: Settings) -> None:
    if not await _is_admin_message(message, session, settings):
        return
    await _send_recent_reviews(message, session, _parse_optional_institution_id(command.args))


@router.callback_query(F.data == "admin:reviews")
async def reviews_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    await _send_recent_reviews(callback.message, session)


@router.callback_query(F.data.startswith("inst_reviews:"))
async def institution_reviews_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    await _send_recent_reviews(callback.message, session, int(callback.data.split(":", 1)[1]))


async def _send_recent_reviews(target: Message, session: AsyncSession, institution_id: int | None = None) -> None:
    reviews = await list_recent_feedback(session, limit=10, institution_id=institution_id)
    if not reviews:
        await target.answer("Отзывов пока нет.")
        return
    for item in reviews:
        tags = ", ".join(tag_label(code) for code in item.tags or []) or "-"
        text = (
            f"Отзыв {item.id}\n"
            f"Тип: {TYPE_LABELS.get(item.feedback_type.value, item.feedback_type.value)}\n"
            f"Учреждение: {item.institution.name}\n"
            f"Сотрудник: {item.employee.full_name if item.employee else 'не указан'}\n"
            f"Дата: {item.created_at:%Y-%m-%d %H:%M}\n"
            f"ФИО отправителя: {item.reviewer_full_name or '-'}\n"
            f"Телефон: {item.reviewer_phone or '-'}\n"
            f"Средняя оценка: {average_rating(item.ratings)}\n"
            f"Оценки:\n{_format_ratings(item.ratings, item.feedback_type.value)}\n"
            f"Метки: {tags}\n"
            f"Комментарий: {item.comment or '-'}\n"
            f"Telegram: {item.user.telegram_link or '-'}\n"
            f"Статус: {STATUS_LABELS.get(item.status, item.status.value)}"
        )
        await target.answer(text, reply_markup=review_status_keyboard(str(item.id)))


@router.callback_query(F.data.startswith("review_status:"))
async def review_status(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    _, feedback_id, status = callback.data.split(":", 2)
    await update_feedback_status(session, UUID(feedback_id), FeedbackStatus(status))
    await callback.message.answer(f"Статус обновлен: {STATUS_LABELS.get(FeedbackStatus(status), status)}")


@router.message(Command("stats"))
async def stats_command(message: Message, command: CommandObject, session: AsyncSession, settings: Settings) -> None:
    if not await _is_admin_message(message, session, settings):
        return
    await message.answer(await dashboard_summary(session, institution_id=_parse_optional_institution_id(command.args)))


@router.callback_query(F.data == "admin:stats")
async def stats_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    await callback.message.answer(await dashboard_summary(session))


@router.message(Command("export"))
async def export_command(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if not await _is_admin_message(message, session, settings):
        return
    fmt = "xlsx"
    institution_id = None
    if command.args:
        parts = command.args.split()
        if parts[0].lower() in {"xlsx", "excel", "pdf"}:
            fmt = "xlsx" if parts[0].lower() == "excel" else parts[0].lower()
            institution_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        elif parts[0].isdigit():
            institution_id = int(parts[0])
    await _send_export(message, session, fmt, institution_id)


@router.callback_query(F.data == "admin:export_xlsx")
async def export_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    await _send_export(callback.message, session, "xlsx")


@router.callback_query(F.data == "admin:export_pdf")
async def export_pdf_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    await _send_export(callback.message, session, "pdf")


@router.callback_query(F.data.startswith("inst_export_xlsx:"))
async def institution_export_xlsx_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    await _send_export(callback.message, session, "xlsx", int(callback.data.split(":", 1)[1]))


@router.callback_query(F.data.startswith("inst_export_pdf:"))
async def institution_export_pdf_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    await _send_export(callback.message, session, "pdf", int(callback.data.split(":", 1)[1]))


async def _send_export(
    target: Message,
    session: AsyncSession,
    fmt: str,
    institution_id: int | None = None,
) -> None:
    suffix = f"_institution_{institution_id}" if institution_id else ""
    if fmt == "pdf":
        path = await export_feedback_pdf(session, institution_id=institution_id)
        filename = f"dmed_feedback{suffix}.pdf"
    else:
        path = await export_feedback_xlsx(session, institution_id=institution_id)
        filename = f"dmed_feedback{suffix}.xlsx"
    try:
        await target.answer_document(FSInputFile(path, filename=filename))
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
