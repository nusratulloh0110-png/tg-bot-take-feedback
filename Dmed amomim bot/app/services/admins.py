from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Admin


async def is_admin(session: AsyncSession, user_id: int, settings: Settings) -> bool:
    if user_id in settings.admin_ids:
        return True
    admin = await session.scalar(select(Admin).where(Admin.user_id == user_id))
    return admin is not None


async def add_admin(session: AsyncSession, user_id: int) -> None:
    admin = await session.get(Admin, user_id)
    if admin is None:
        session.add(Admin(user_id=user_id))
        await session.flush()

