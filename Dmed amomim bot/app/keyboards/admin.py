from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models import Employee, FeedbackStatus, Institution


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Учреждения", callback_data="admin:institutions"),
                InlineKeyboardButton(text="Отзывы", callback_data="admin:reviews"),
            ],
            [
                InlineKeyboardButton(text="Статистика", callback_data="admin:stats"),
                InlineKeyboardButton(text="Экспорт Excel", callback_data="admin:export_xlsx"),
            ],
            [
                InlineKeyboardButton(text="Сотрудники", callback_data="admin:employees"),
                InlineKeyboardButton(text="Экспорт PDF", callback_data="admin:export_pdf"),
            ],
        ]
    )


def institutions_keyboard(items: list[Institution]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for institution in items:
        status = "🟢" if institution.token_active and not institution.archived else "⚪"
        builder.button(text=f"{status} #{institution.id} {institution.name}", callback_data=f"inst:{institution.id}")
    builder.adjust(1)
    return builder.as_markup()


def employees_keyboard(items: list[Employee]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for employee in items:
        status = "⚪" if employee.archived else "🟢"
        builder.button(text=f"{status} #{employee.id} {employee.full_name}", callback_data=f"employee_admin:{employee.id}")
    builder.adjust(1)
    return builder.as_markup()


def employee_actions_keyboard(employee_id: int, archived: bool) -> InlineKeyboardMarkup:
    label = "Вернуть из архива" if archived else "Архивировать"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"employee_archive:{employee_id}")]
        ]
    )


def institution_actions_keyboard(institution_id: int, link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть ссылку", url=link)],
            [
                InlineKeyboardButton(text="Деактивировать", callback_data=f"inst_deactivate:{institution_id}"),
                InlineKeyboardButton(text="Перевыпустить", callback_data=f"inst_reissue:{institution_id}"),
            ],
            [InlineKeyboardButton(text="Архивировать", callback_data=f"inst_archive:{institution_id}")],
        ]
    )


def review_status_keyboard(feedback_id: str) -> InlineKeyboardMarkup:
    labels = {
        FeedbackStatus.reviewed: "Изучено",
        FeedbackStatus.in_progress: "В работе",
        FeedbackStatus.closed: "Закрыто",
    }
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"review_status:{feedback_id}:{status.value}",
                )
            ]
            for status, label in labels.items()
        ]
    )
