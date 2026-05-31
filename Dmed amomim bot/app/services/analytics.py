from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Employee, Feedback, FeedbackType, Institution, LinkVisit
from app.services.feedback import average_rating


async def dashboard_summary(session: AsyncSession, days: int = 7) -> str:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    total = await session.scalar(select(func.count(Feedback.id)).where(Feedback.created_at >= since)) or 0
    employee_total = await session.scalar(
        select(func.count(Feedback.id)).where(
            Feedback.created_at >= since,
            Feedback.feedback_type == FeedbackType.employee,
        )
    ) or 0
    implementation_total = await session.scalar(
        select(func.count(Feedback.id)).where(
            Feedback.created_at >= since,
            Feedback.feedback_type == FeedbackType.implementation,
        )
    ) or 0
    comments_total = await session.scalar(
        select(func.count(Feedback.id)).where(Feedback.created_at >= since, Feedback.comment.is_not(None))
    ) or 0
    visits_total = await session.scalar(select(func.count(LinkVisit.id))) or 0

    rows = await session.execute(
        select(Institution.name, Feedback.ratings)
        .join(Feedback, Feedback.institution_id == Institution.id)
        .where(Feedback.created_at >= since)
    )
    institution_scores: dict[str, list[float]] = {}
    for name, ratings in rows:
        institution_scores.setdefault(name, []).append(average_rating(ratings))

    top_institutions = sorted(
        ((name, round(sum(values) / len(values), 2)) for name, values in institution_scores.items()),
        key=lambda item: item[1],
        reverse=True,
    )[:5]

    lines = [
        f"📊 Статистика за {days} дней",
        f"Всего отзывов: {total}",
        f"По сотрудникам: {employee_total}",
        f"По внедрению: {implementation_total}",
        f"С комментариями: {comments_total}",
        f"Уникальных переходов по ссылкам: {visits_total}",
    ]
    if top_institutions:
        lines.append("")
        lines.append("Средние оценки по учреждениям:")
        for name, score in top_institutions:
            lines.append(f"• {name}: {score}")
    return "\n".join(lines)


async def digest_summary(session: AsyncSession) -> str:
    rows = await session.execute(
        select(Employee.full_name, Feedback.ratings)
        .join(Feedback, Feedback.employee_id == Employee.id)
        .where(Feedback.feedback_type == FeedbackType.employee)
    )
    employee_scores: dict[str, list[float]] = {}
    for full_name, ratings in rows:
        employee_scores.setdefault(full_name, []).append(average_rating(ratings))

    scores = sorted(
        ((name, round(sum(values) / len(values), 2)) for name, values in employee_scores.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    best = scores[:3]
    worst = list(reversed(scores[-3:])) if len(scores) > 3 else []

    lines = ["Еженедельный дайджест DMED"]
    if best:
        lines.append("")
        lines.append("Топ-3 лучших оценок:")
        for name, score in best:
            lines.append(f"• {name}: {score}")
    if worst:
        lines.append("")
        lines.append("Топ-3 требующих внимания:")
        for name, score in worst:
            lines.append(f"• {name}: {score}")
    if len(lines) == 1:
        lines.append("Пока недостаточно отзывов для рейтинга.")
    return "\n".join(lines)

