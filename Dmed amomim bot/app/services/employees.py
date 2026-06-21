from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Employee, EmployeeInstitution, Institution


async def normalize_institution_ids(session: AsyncSession, institution_ids: list[int]) -> list[int]:
    clean_ids = list(dict.fromkeys(institution_id for institution_id in institution_ids if institution_id > 0))
    if not clean_ids:
        return []
    existing = await session.scalars(select(Institution.id).where(Institution.id.in_(clean_ids)))
    existing_ids = set(existing)
    return [institution_id for institution_id in clean_ids if institution_id in existing_ids]


async def set_employee_institutions(
    session: AsyncSession,
    employee: Employee,
    institution_ids: list[int],
) -> None:
    clean_ids = await normalize_institution_ids(session, institution_ids)
    if not clean_ids:
        raise ValueError("employee must be linked to at least one institution")

    employee.institution_id = clean_ids[0]
    await session.execute(delete(EmployeeInstitution).where(EmployeeInstitution.employee_id == employee.id))
    for institution_id in clean_ids:
        session.add(EmployeeInstitution(employee_id=employee.id, institution_id=institution_id))
    await session.flush()


async def employee_institution_ids(session: AsyncSession, employee_id: int) -> list[int]:
    result = await session.scalars(
        select(EmployeeInstitution.institution_id)
        .where(EmployeeInstitution.employee_id == employee_id)
        .order_by(EmployeeInstitution.institution_id)
    )
    return list(result)
