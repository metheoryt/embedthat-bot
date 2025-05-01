from aiogram import types

from bot.config import settings
from bot.enum import LinkOrigin
from bot.events.signals import signal_handler, on_link_sent, on_yt_video_sent
from bot.util.youtube.schema import YouTubeVideoData


@signal_handler(on_link_sent)
async def share_link(link: str, message: types.Message, origin: LinkOrigin):
    """Share a TikTok or instagram reel."""
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
async def share_yt_shorts(link: str, message: types.Message, video: YouTubeVideoData, fresh: bool):
    """Share successfully downloaded YouTube shorts to a feed channel."""
    if settings.feed_channel_id and message.chat.type != "private" and fresh and 'shorts' in link:
        await video.send_to_chat(message.bot, settings.feed_channel_id)
