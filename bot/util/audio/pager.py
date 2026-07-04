import json
import logging

import redis.asyncio as redis
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from bot.util.audio.schema import AudioRequestData

log = logging.getLogger(__name__)


def _messages_key(chat_id: int, root_message_id: int) -> str:
    return f"da:msgs:{chat_id}:{root_message_id}"


async def redeliver_page(
    redis_client: redis.Redis, bot: Bot, chat_id: int, root_message_id: int, audio: AudioRequestData, page: int,
) -> None:
    key = _messages_key(chat_id, root_message_id)
    old_raw = await redis_client.get(key)
    old_ids: list[int] = json.loads(old_raw) if old_raw else []

    new_ids = await audio.send_to_chat(bot, chat_id, reply_to_message_id=root_message_id, page=page)
    await redis_client.set(key, json.dumps(new_ids))

    for message_id in old_ids:
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramBadRequest:
            log.warning("could not delete old page message %s in chat %s", message_id, chat_id)
