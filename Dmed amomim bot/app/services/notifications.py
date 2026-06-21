from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Admin, Feedback, FeedbackType
from app.services.feedback import average_rating


TYPE_LABELS = {
    FeedbackType.employee: "Оценка сотрудника",
    FeedbackType.implementation: "Оценка внедрения",
}


async def admin_ids(session: AsyncSession, settings: Settings) -> set[int]:
    ids = set(settings.admin_ids)
    result = await session.scalars(select(Admin.user_id))
    ids.update(result)
    return ids


async def notify_new_feedback(
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    feedback: Feedback,
) -> None:
    average = average_rating(feedback.ratings)
    low_marker = "\nТребует внимания: низкая оценка" if average <= 2.5 else ""
    text = (
        "Новый отзыв DMED\n"
        f"Тип: {TYPE_LABELS.get(feedback.feedback_type, feedback.feedback_type.value)}\n"
        f"Учреждение: {feedback.institution.name}\n"
        f"Сотрудник: {feedback.employee.full_name if feedback.employee else 'не указан'}\n"
        f"ФИО отправителя: {feedback.reviewer_full_name or '-'}\n"
        f"Телефон: {feedback.reviewer_phone or '-'}\n"
        f"Средняя оценка: {average}\n"
        f"Telegram: {feedback.user.telegram_link or '-'}"
        f"{low_marker}"
    )
    for admin_id in await admin_ids(session, settings):
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            continue
