import logging

import aiohttp

from bot.dispatcher import dp

log = logging.getLogger(__name__)

session: aiohttp.ClientSession = aiohttp.ClientSession()


@dp.shutdown()
async def on_shutdown(*args, **kwargs):
    await session.close()
    log.info("aiohttp session has been closed")
