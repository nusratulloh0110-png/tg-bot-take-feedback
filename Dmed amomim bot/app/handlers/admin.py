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
from app.states import AdminAuth, CreateEmployee, CreateInstitution

router = Router(name="admin")


async def _is_admin_message(message: Message, session: AsyncSession, settings: Settings) -> bool:
    allowed = await is_admin(session, message.from_user.id, settings)
    if not allowed:
        await message.answer("Нет доступа. Используйте /admin и пароль администратора.")
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


@router.message(Command("admin"))
async def admin_login(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    if await is_admin(session, message.from_user.id, settings):
        await state.clear()
        await message.answer("Админ-панель:", reply_markup=admin_menu_keyboard())
        return

    password = command.args.strip() if command.args else None
    if password and password == settings.admin_password:
        await add_admin(session, message.from_user.id)
        await state.clear()
        await message.answer("Доступ администратора сохранён.", reply_markup=admin_menu_keyboard())
        return

    await state.set_state(AdminAuth.password)
    await message.answer("Введите пароль администратора:")


@router.message(AdminAuth.password)
async def admin_password(message: Message, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    if message.text and message.text.strip() == settings.admin_password:
        await add_admin(session, message.from_user.id)
        await state.clear()
        await message.answer("Доступ администратора сохранён.", reply_markup=admin_menu_keyboard())
        return
    await message.answer("Неверный пароль.")


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


@router.message(CreateInstitution.name)
async def create_institution_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text.strip())
    await state.set_state(CreateInstitution.region)
    await message.answer("Регион (или '-' чтобы пропустить):")


@router.message(CreateInstitution.region)
async def create_institution_region(message: Message, state: FSMContext) -> None:
    region = None if message.text.strip() == "-" else message.text.strip()
    await state.update_data(region=region)
    await state.set_state(CreateInstitution.address)
    await message.answer("Адрес (или '-' чтобы пропустить):")


@router.message(CreateInstitution.address)
async def create_institution_address(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    address = None if message.text.strip() == "-" else message.text.strip()
    institution = await create_institution(session, data["name"], data.get("region"), address)
    await state.clear()
    await _send_institution_card(message, bot, settings, institution)


async def _send_institution_card(message: Message, bot: Bot, settings: Settings, institution: Institution) -> None:
    link = await _institution_link(bot, settings, institution)
    text = (
        f"Учреждение создано\n"
        f"ID: {institution.id}\n"
        f"Название: {institution.name}\n"
        f"Регион: {institution.region or '-'}\n"
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
        institution = await session.get(Institution, int(parts[0]))
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
    try:
        institution_id = int(message.text.strip())
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
    await state.update_data(full_name=message.text.strip())
    await state.set_state(CreateEmployee.position)
    await message.answer("Должность (или '-' чтобы пропустить):")


@router.message(CreateEmployee.position)
async def create_employee_position(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    position = None if message.text.strip() == "-" else message.text.strip()
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
    await callback.message.answer("Статус сотрудника обновлён.")


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
    link = await _institution_link(bot, settings, institution)
    text = (
        f"#{institution.id} {institution.name}\n"
        f"Регион: {institution.region or '-'}\n"
        f"Адрес: {institution.address or '-'}\n"
        f"Токен активен: {'да' if institution.token_active else 'нет'}\n"
        f"Архив: {'да' if institution.archived else 'нет'}\n"
        f"Ссылка: {link}"
    )
    await callback.message.answer(text, reply_markup=institution_actions_keyboard(institution.id, link))


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
async def reviews_command(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not await _is_admin_message(message, session, settings):
        return
    await _send_recent_reviews(message, session)


@router.callback_query(F.data == "admin:reviews")
async def reviews_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    await _send_recent_reviews(callback.message, session)


async def _send_recent_reviews(target: Message, session: AsyncSession) -> None:
    reviews = await list_recent_feedback(session, limit=10)
    if not reviews:
        await target.answer("Отзывов пока нет.")
        return
    for item in reviews:
        text = (
            f"Отзыв {item.id}\n"
            f"Тип: {item.feedback_type.value}\n"
            f"Учреждение: {item.institution.name}\n"
            f"Сотрудник: {item.employee.full_name if item.employee else 'Не указан'}\n"
            f"Дата: {item.created_at:%Y-%m-%d %H:%M}\n"
            f"Средняя оценка: {average_rating(item.ratings)}\n"
            f"Оценки: {item.ratings}\n"
            f"Метки: {', '.join(item.tags or []) or '-'}\n"
            f"Комментарий: {item.comment or '-'}\n"
            f"Отправитель: {item.user.telegram_link or '-'}\n"
            f"Статус: {item.status.value}"
        )
        await target.answer(text, reply_markup=review_status_keyboard(str(item.id)))


@router.callback_query(F.data.startswith("review_status:"))
async def review_status(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if not await _is_admin_callback(callback, session, settings):
        return
    _, feedback_id, status = callback.data.split(":", 2)
    await update_feedback_status(session, UUID(feedback_id), FeedbackStatus(status))
    await callback.message.answer(f"Статус обновлён: {status}")


@router.message(Command("stats"))
async def stats_command(message: Message, session: AsyncSession, settings: Settings) -> None:
    if not await _is_admin_message(message, session, settings):
        return
    await message.answer(await dashboard_summary(session))


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
    fmt = (command.args or "xlsx").strip().lower()
    await _send_export(message, session, fmt)


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


async def _send_export(target: Message, session: AsyncSession, fmt: str) -> None:
    if fmt == "pdf":
        path = await export_feedback_pdf(session)
        filename = "dmed_feedback.pdf"
    else:
        path = await export_feedback_xlsx(session)
        filename = "dmed_feedback.xlsx"
    try:
        await target.answer_document(FSInputFile(path, filename=filename))
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
