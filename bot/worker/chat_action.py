import asyncio
from functools import wraps

from aiogram import Bot
from aiogram.enums import ChatAction

from bot.util.chat_action import send_chat_action_periodically


def with_chat_action(action: ChatAction = ChatAction.UPLOAD_VIDEO):
    def decorator(func):
        @wraps(func)
        async def wrapper(bot: Bot, chat_id: int, *args, **kwargs):
            action_task = await send_chat_action_periodically(bot, chat_id, action)
            try:
                return await func(bot, chat_id, *args, **kwargs)
            finally:
                action_task.cancel()
                try:
                    await action_task
                except asyncio.CancelledError:
                    pass
        return wrapper
    return decorator
