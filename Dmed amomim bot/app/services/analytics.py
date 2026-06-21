from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Employee, Feedback, FeedbackType, Institution, LinkVisit
from app.services.feedback import average_rating


def _reviewer_key(feedback: Feedback) -> str:
    return feedback.reviewer_phone or str(feedback.user_id)


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


async def feedback_report_data(
    session: AsyncSession,
    days: int | None = 30,
    institution_id: int | None = None,
) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    conditions = []
    if since is not None:
        conditions.append(Feedback.created_at >= since)
    if institution_id is not None:
        conditions.append(Feedback.institution_id == institution_id)

    query = (
        select(Feedback)
        .options(selectinload(Feedback.employee), selectinload(Feedback.institution), selectinload(Feedback.user))
        .order_by(Feedback.created_at.desc())
    )
    if conditions:
        query = query.where(*conditions)
    feedback_items = list(await session.scalars(query))

    scores = [average_rating(item.ratings) for item in feedback_items]
    employee_items = [item for item in feedback_items if item.feedback_type == FeedbackType.employee]
    implementation_items = [item for item in feedback_items if item.feedback_type == FeedbackType.implementation]
    comments_total = sum(1 for item in feedback_items if item.comment)
    low_score_total = sum(1 for score in scores if score and score <= 2.5)
    unique_reviewers = {_reviewer_key(item) for item in feedback_items}

    institution_scores: dict[int, dict[str, Any]] = {}
    for item in feedback_items:
        bucket = institution_scores.setdefault(
            item.institution_id,
            {
                "id": item.institution_id,
                "name": item.institution.name if item.institution else f"#{item.institution_id}",
                "region": item.institution.region if item.institution else None,
                "scores": [],
                "reviewers": set(),
                "feedback_total": 0,
            },
        )
        bucket["scores"].append(average_rating(item.ratings))
        bucket["reviewers"].add(_reviewer_key(item))
        bucket["feedback_total"] += 1

    institution_rows = []
    for row in institution_scores.values():
        institution_rows.append(
            {
                "id": row["id"],
                "name": row["name"],
                "region": row["region"],
                "feedback_total": row["feedback_total"],
                "reviewers_total": len(row["reviewers"]),
                "average_rating": _mean(row["scores"]),
            }
        )
    institution_rows.sort(key=lambda item: (item["average_rating"], item["feedback_total"]), reverse=True)

    employee_scores: dict[int, dict[str, Any]] = {}
    for item in employee_items:
        if item.employee_id is None:
            continue
        bucket = employee_scores.setdefault(
            item.employee_id,
            {
                "id": item.employee_id,
                "name": item.employee.full_name if item.employee else f"#{item.employee_id}",
                "institution": item.institution.name if item.institution else "-",
                "scores": [],
                "feedback_total": 0,
            },
        )
        bucket["scores"].append(average_rating(item.ratings))
        bucket["feedback_total"] += 1

    employee_rows = [
        {
            "id": row["id"],
            "name": row["name"],
            "institution": row["institution"],
            "feedback_total": row["feedback_total"],
            "average_rating": _mean(row["scores"]),
        }
        for row in employee_scores.values()
    ]
    employee_rows.sort(key=lambda item: (item["average_rating"], item["feedback_total"]), reverse=True)

    visit_query = select(func.count(LinkVisit.id))
    if institution_id is not None:
        visit_query = visit_query.where(LinkVisit.institution_id == institution_id)
    visits_total = await session.scalar(visit_query) or 0

    employees_query = select(func.count(Employee.id)).where(Employee.archived.is_(False))
    if institution_id is not None:
        employees_query = employees_query.where(Employee.institution_id == institution_id)
    active_employees_total = await session.scalar(employees_query) or 0

    institutions_total = await session.scalar(select(func.count(Institution.id)).where(Institution.archived.is_(False))) or 0

    selected_institution = None
    if institution_id is not None:
        selected_institution = await session.get(Institution, institution_id)

    return {
        "days": days,
        "institution": selected_institution,
        "feedback_items": feedback_items,
        "summary": {
            "feedback_total": len(feedback_items),
            "employee_feedback_total": len(employee_items),
            "implementation_feedback_total": len(implementation_items),
            "comments_total": comments_total,
            "unique_reviewers_total": len(unique_reviewers),
            "average_rating": _mean(scores),
            "employee_average_rating": _mean([average_rating(item.ratings) for item in employee_items]),
            "implementation_average_rating": _mean([average_rating(item.ratings) for item in implementation_items]),
            "low_score_total": low_score_total,
            "link_visits_total": visits_total,
            "active_employees_total": active_employees_total,
            "institutions_total": institutions_total,
        },
        "institutions": institution_rows,
        "employees": employee_rows,
    }


async def dashboard_summary(session: AsyncSession, days: int = 30, institution_id: int | None = None) -> str:
    data = await feedback_report_data(session, days=days, institution_id=institution_id)
    summary = data["summary"]
    institution = data["institution"]
    title = f"Статистика за {days} дней"
    if institution is not None:
        title += f"\nУчреждение: {institution.name}"

    lines = [
        title,
        f"Всего отзывов: {summary['feedback_total']}",
        f"Сотрудников, от которых получен фидбек: {summary['unique_reviewers_total']}",
        f"Средняя оценка: {summary['average_rating']}",
        f"Отзывы по сотрудникам: {summary['employee_feedback_total']} (средняя: {summary['employee_average_rating']})",
        f"Отзывы по внедрению: {summary['implementation_feedback_total']} (средняя: {summary['implementation_average_rating']})",
        f"С комментариями: {summary['comments_total']}",
        f"Низкие оценки до 2.5: {summary['low_score_total']}",
        f"Уникальных переходов по ссылкам: {summary['link_visits_total']}",
    ]

    if data["institutions"] and institution is None:
        lines.append("")
        lines.append("Учреждения:")
        for row in data["institutions"][:10]:
            lines.append(
                f"- {row['name']}: отзывов {row['feedback_total']}, сотрудников {row['reviewers_total']}, средняя {row['average_rating']}"
            )

    if data["employees"]:
        lines.append("")
        lines.append("Сотрудники-внедренцы:")
        for row in data["employees"][:10]:
            lines.append(
                f"- {row['name']} ({row['institution']}): отзывов {row['feedback_total']}, средняя {row['average_rating']}"
            )

    return "\n".join(lines)


async def digest_summary(session: AsyncSession) -> str:
    data = await feedback_report_data(session, days=7)
    employees = data["employees"]
    best = employees[:3]
    worst = sorted(employees, key=lambda item: item["average_rating"])[:3] if len(employees) > 3 else []

    lines = ["Еженедельный дайджест DMED", f"Всего отзывов: {data['summary']['feedback_total']}"]
    if best:
        lines.append("")
        lines.append("Топ-3 лучших оценок:")
        for row in best:
            lines.append(f"- {row['name']}: {row['average_rating']} ({row['feedback_total']} отзывов)")
    if worst:
        lines.append("")
        lines.append("Требуют внимания:")
        for row in worst:
            lines.append(f"- {row['name']}: {row['average_rating']} ({row['feedback_total']} отзывов)")
    if len(lines) == 2:
        lines.append("Пока недостаточно отзывов для рейтинга.")
    return "\n".join(lines)
