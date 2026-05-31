from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Admin, Feedback
from app.services.feedback import average_rating


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
    low_marker = "\nТребует внимания ⚠️" if average <= 2 else ""
    text = (
        "Новый отзыв DMED\n"
        f"Тип: {feedback.feedback_type.value}\n"
        f"Учреждение: {feedback.institution.name}\n"
        f"Сотрудник: {feedback.employee.full_name if feedback.employee else 'Не указан'}\n"
        f"Средняя оценка: {average}\n"
        f"Профиль отправителя: {feedback.user.telegram_link or 'нет'}"
        f"{low_marker}"
    )
    for admin_id in await admin_ids(session, settings):
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            continue

