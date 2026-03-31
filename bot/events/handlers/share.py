from aiogram import types

from bot.config import settings
from bot.events.signals import signal_handler, on_yt_video_sent, on_social_video_sent
from bot.util.social.schema import SocialVideoData
from bot.util.youtube.schema import YouTubeVideoData


@signal_handler(on_social_video_sent)
async def share_social_video(link: str, message: types.Message, video: SocialVideoData, fresh: bool):
    """Share fresh TikTok/Instagram downloads to the feed channel."""
    if not settings.feed_channel_id or message.chat.type == "private" or not fresh:
        return
    await video.send_to_chat(message.bot, settings.feed_channel_id)


@signal_handler(on_yt_video_sent)
async def share_yt_shorts(link: str, message: types.Message, video: YouTubeVideoData, fresh: bool):
    """Share successfully downloaded YouTube shorts to a feed channel."""
    if settings.feed_channel_id and message.chat.type != "private" and fresh and 'shorts' in link:
        await video.send_to_chat(message.bot, settings.feed_channel_id)
