import asyncio
import logging

from aiogram import Bot, Dispatcher

from abooker.bot.handlers import commands, default
from abooker.core.config import settings
from abooker.core.database import init_db, recover_from_json

log = logging.getLogger(__name__)


async def _main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await recover_from_json()

    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(default.router)
    dp.include_router(commands.router)

    log.info("Starting bot polling")
    await dp.start_polling(bot)


def run():
    asyncio.run(_main())


if __name__ == "__main__":
    run()
