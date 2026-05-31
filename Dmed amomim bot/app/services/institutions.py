from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Institution
from app.services.tokens import generate_institution_token


async def get_active_institution_by_token(session: AsyncSession, token: str) -> Institution | None:
    return await session.scalar(select(Institution).where(Institution.token == token))


async def create_institution(
    session: AsyncSession,
    name: str,
    region: str | None = None,
    address: str | None = None,
) -> Institution:
    institution = Institution(
        name=name.strip(),
        region=region.strip() if region else None,
        address=address.strip() if address else None,
        token=generate_institution_token(),
    )
    session.add(institution)
    await session.flush()
    return institution


async def reissue_token(session: AsyncSession, institution: Institution) -> Institution:
    institution.token = generate_institution_token()
    institution.token_active = True
    await session.flush()
    return institution

