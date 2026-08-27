import asyncio
import logging

from aiogram import Bot, Dispatcher

from abooker.bot.handlers import chapters, commands, default
from abooker.bot.handlers import auth
from abooker.bot.middleware import UserMiddleware
from abooker.core.config import settings
from abooker.core.database import cleanup_orphaned_pending, init_db, recover_from_json
from abooker.core.log import setup_logging

log = logging.getLogger(__name__)


async def _main():
    setup_logging()
    await init_db()
    await recover_from_json()
    await cleanup_orphaned_pending()

    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()

    dp.update.outer_middleware(UserMiddleware())

    dp.include_router(auth.router)
    dp.include_router(default.router)
    dp.include_router(commands.router)
    dp.include_router(chapters.router)

    log.info("Starting bot polling")
    await dp.start_polling(bot)


def run():
    asyncio.run(_main())


if __name__ == "__main__":
    run()
