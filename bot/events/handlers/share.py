from aiogram import types

from bot.config import settings
from bot.enum import LinkOrigin
from bot.events.signals import signal_handler, on_link_sent, on_yt_video_sent


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
    """Share youtube shorts."""
    if not settings.feed_channel_id or message.chat.type == "private":
        return

    if "/shorts/" in link and fresh is True:
        await message.bot.send_video(settings.feed_channel_id, file_id)
