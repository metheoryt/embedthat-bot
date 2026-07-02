from aiogram import Bot

from bot.config import settings
from bot.events.signals import signal_handler, on_yt_video_sent, on_social_video_sent
from bot.util.social.schema import SocialVideoData
from bot.util.youtube.schema import YouTubeVideoData


@signal_handler(on_social_video_sent)
async def share_social_video(link: str, chat_id: int, chat_type: str, bot: Bot, video: SocialVideoData, fresh: bool):
    """Share fresh TikTok/Instagram downloads to the feed channel."""
    if not settings.feed_channel_id or chat_type == "private" or not fresh:
        return
    await video.send_to_chat(bot, settings.feed_channel_id)


@signal_handler(on_yt_video_sent)
async def share_yt_shorts(link: str, chat_id: int, chat_type: str, bot: Bot, video: YouTubeVideoData, fresh: bool):
    """Share successfully downloaded YouTube shorts to a feed channel."""
    if settings.feed_channel_id and chat_type != "private" and fresh and 'shorts' in link:
        await video.send_to_chat(bot, settings.feed_channel_id)
