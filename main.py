import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from bot.config import settings
from bot.dispatcher import dp, router
from bot.events import freeze_signals


async def main():
    from bot import handlers  # noqa

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp.include_router(router)

    # freeze signals before starting the polling (non-frozen signals unable to send signals)
    freeze_signals()
    await dp.start_polling(bot)


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(
        level=getattr(logging, settings.loglevel),
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
    )
    # disable logs for non-handled events
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    asyncio.run(main())
