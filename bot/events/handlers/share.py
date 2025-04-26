from aiogram import types

from bot.config import settings
from bot.enum import LinkOrigin
from bot.events.signals import signal_handler, on_link_sent, on_yt_video_sent, on_yt_video_fail


@signal_handler(on_link_sent)
async def share_link(link: str, message: types.Message, origin: LinkOrigin):
    """Share a tiktok or instagram reel."""
    if not settings.feed_channel_id or message.chat.type == "private":
        return

    if any(
        [
            origin == LinkOrigin.TIKTOK,
            origin == LinkOrigin.INSTAGRAM and "/reel/" in link,
        ]
    ):
        await message.bot.send_message(settings.feed_channel_id, link)


@signal_handler(on_yt_video_sent)
async def share_yt_shorts(link: str, message: types.Message, file_id: str, fresh: bool):
    """Share successfully downloaded YouTube videos to a debug feed channel."""
    if settings.feed_channel_id and message.chat.type != "private" and fresh:
        await message.bot.send_video(settings.feed_channel_id, file_id, caption=link)
