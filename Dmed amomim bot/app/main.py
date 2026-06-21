import asyncio
import logging
from zoneinfo import ZoneInfo

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings, get_settings
from app.db.base import Base
from app.db.session import SessionFactory, engine
from app.handlers import admin, user
from app.middlewares.database import DatabaseMiddleware
from app.services.analytics import digest_summary
from app.services.notifications import admin_ids


async def create_storage(settings: Settings):
    if settings.redis_url:
        return RedisStorage.from_url(settings.redis_url)
    return MemoryStorage()


async def send_weekly_digest(bot: Bot, settings: Settings) -> None:
    async with SessionFactory() as session:
        text = await digest_summary(session)
        for admin_id in await admin_ids(session, settings):
            try:
                await bot.send_message(admin_id, text)
            except Exception:
                logging.exception("Could not send digest to admin %s", admin_id)


async def create_tables_if_needed(settings: Settings) -> None:
    if not settings.auto_create_tables:
        return
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    await create_tables_if_needed(settings)

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=await create_storage(settings))
    dp["settings"] = settings

    db_middleware = DatabaseMiddleware()
    dp.message.middleware(db_middleware)
    dp.callback_query.middleware(db_middleware)

    dp.include_router(admin.router)
    dp.include_router(user.router)

    scheduler_tz = ZoneInfo(settings.timezone)
    scheduler = AsyncIOScheduler(timezone=scheduler_tz)
    if settings.weekly_digest_enabled:
        scheduler.add_job(
            send_weekly_digest,
            CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=scheduler_tz),
            args=[bot, settings],
            id="weekly_digest",
            replace_existing=True,
        )
        scheduler.start()

    web_server: uvicorn.Server | None = None
    web_task: asyncio.Task | None = None
    if settings.web_enabled:
        from app.web import app as web_app

        web_config = uvicorn.Config(
            web_app,
            host=settings.web_host,
            port=settings.web_port,
            log_level="info",
        )
        web_server = uvicorn.Server(web_config)
        web_task = asyncio.create_task(web_server.serve())

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if web_server is not None:
            web_server.should_exit = True
        if web_task is not None:
            await web_task
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
