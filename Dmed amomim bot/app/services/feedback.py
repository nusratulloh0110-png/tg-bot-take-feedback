from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Employee, Feedback, FeedbackStatus, FeedbackType


SPAM_WINDOW = timedelta(days=7)


async def list_employees_for_institution(session: AsyncSession, institution_id: int) -> list[Employee]:
    result = await session.scalars(
        select(Employee)
        .where(Employee.institution_id == institution_id, Employee.archived.is_(False))
        .order_by(Employee.full_name)
    )
    return list(result)


async def is_rate_limited(
    session: AsyncSession,
    user_id: int,
    institution_id: int,
    feedback_type: FeedbackType,
    employee_id: int | None = None,
) -> bool:
    cutoff = datetime.now(timezone.utc) - SPAM_WINDOW
    conditions = [
        Feedback.user_id == user_id,
        Feedback.institution_id == institution_id,
        Feedback.feedback_type == feedback_type,
        Feedback.created_at >= cutoff,
    ]
    if feedback_type == FeedbackType.employee:
        if employee_id is None:
            conditions.append(Feedback.employee_id.is_(None))
        else:
            conditions.append(Feedback.employee_id == employee_id)
    query = select(func.count(Feedback.id)).where(and_(*conditions))
    return (await session.scalar(query) or 0) > 0


async def create_feedback(
    session: AsyncSession,
    user_id: int,
    institution_id: int,
    feedback_type: FeedbackType,
    ratings: dict[str, int],
    employee_id: int | None = None,
    tags: list[str] | None = None,
    comment: str | None = None,
) -> Feedback:
    feedback = Feedback(
        user_id=user_id,
        institution_id=institution_id,
        feedback_type=feedback_type,
        employee_id=employee_id,
        ratings=ratings,
        tags=tags or [],
        comment=comment.strip() if comment else None,
    )
    session.add(feedback)
    await session.flush()
    loaded = await session.scalar(
        select(Feedback)
        .where(Feedback.id == feedback.id)
        .options(selectinload(Feedback.employee), selectinload(Feedback.institution), selectinload(Feedback.user))
    )
    return loaded or feedback


async def list_user_feedback(session: AsyncSession, user_id: int) -> list[Feedback]:
    result = await session.scalars(
        select(Feedback)
        .where(Feedback.user_id == user_id)
        .options(selectinload(Feedback.employee), selectinload(Feedback.institution))
        .order_by(Feedback.created_at.desc())
        .limit(20)
    )
    return list(result)


async def list_recent_feedback(session: AsyncSession, limit: int = 10) -> list[Feedback]:
    result = await session.scalars(
        select(Feedback)
        .options(selectinload(Feedback.employee), selectinload(Feedback.institution), selectinload(Feedback.user))
        .order_by(Feedback.created_at.desc())
        .limit(limit)
    )
    return list(result)


async def update_feedback_status(
    session: AsyncSession,
    feedback_id: UUID,
    status: FeedbackStatus,
) -> Feedback | None:
    feedback = await session.get(Feedback, feedback_id)
    if feedback is None:
        return None
    feedback.status = status
    await session.flush()
    return feedback


def average_rating(ratings: dict[str, int]) -> float:
    values = list(ratings.values())
    return round(sum(values) / len(values), 2) if values else 0.0
