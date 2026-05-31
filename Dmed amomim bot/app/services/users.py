from aiogram.types import User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LinkVisit, User


def telegram_profile_link(tg_user: TelegramUser) -> str:
    if tg_user.username:
        return f"https://t.me/{tg_user.username}"
    return f"tg://user?id={tg_user.id}"


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def upsert_user(
    session: AsyncSession,
    tg_user: TelegramUser,
    institution_id: int | None = None,
) -> User:
    user = await session.get(User, tg_user.id)
    if user is None:
        user = User(
            id=tg_user.id,
            institution_id=institution_id,
            telegram_link=telegram_profile_link(tg_user),
            username=tg_user.username,
            first_name=tg_user.first_name,
        )
        session.add(user)
    else:
        if institution_id is not None:
            user.institution_id = institution_id
        user.telegram_link = telegram_profile_link(tg_user)
        user.username = tg_user.username
        user.first_name = tg_user.first_name
    await session.flush()
    return user


async def record_link_visit(session: AsyncSession, user_id: int, institution_id: int, token: str) -> None:
    existing = await session.scalar(
        select(LinkVisit).where(
            LinkVisit.user_id == user_id,
            LinkVisit.institution_id == institution_id,
            LinkVisit.token == token,
        )
    )
    if existing is None:
        session.add(LinkVisit(user_id=user_id, institution_id=institution_id, token=token))

