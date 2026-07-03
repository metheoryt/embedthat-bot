import logging
import re

from aiogram import types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import ErrorEvent, Message

from .config import settings
from .dispatcher import router
from .enum import LinkOrigin
from .events import on_link_received, on_yt_video_sent, on_social_video_sent
from .util.stats import build_stats_report
from .util.redis import redis_client
from .util.social.schema import SocialVideoData
from .util.youtube.enum import TargetLang
from .util.youtube.schema import YouTubeVideoData
from .worker.actors import process_social_link, process_youtube_audio, process_youtube_link
from .worker.waiters import Waiter, register_waiter

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://\S+")
_YOUTUBE_URL_RE = re.compile(r"https?://((www|m)\.)?youtube\.com/|https?://youtu\.be/")

_YOUTUBE_WAITERS_TTL = 3 * 60 * 60  # generous vs. worst-case retry budget (~2.5h)
_SOCIAL_WAITERS_TTL = 90 * 60  # ~1.5h


@router.error()
async def error_handler(event: ErrorEvent):
    message = event.update.message
    log.critical(
        "Unhandled error while processing update (message text: %r)",
        message.text if message else None,
        exc_info=event.exception,
    )


@router.message(CommandStart())
async def start(message: types.Message):
    await message.reply("Send a link and i will reply with a nice embedding or a video")


@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if not settings.admin_chat_id or message.chat.id != settings.admin_chat_id:
        return
    await message.reply(await build_stats_report())


@router.message(
    F.text.regexp(r"^https://(((www|m)\.)?youtube\.com/(watch|shorts/)|youtu\.be/)")
)
async def embed_youtube_videos(message: types.Message):
    await on_link_received.send(message, LinkOrigin.YOUTUBE)
    link = message.text.split()[0]  # as regex states, we expect the first element in the text to be a link

    if settings.enable_audio_translation:
        log.info('user lang: %s', message.from_user.language_code)
        try:
            target_lang = TargetLang(message.from_user.language_code)
        except ValueError:
            target_lang = TargetLang.ORIGINAL
            log.info('no translation will be performed')
    else:
        target_lang = TargetLang.ORIGINAL

    video = YouTubeVideoData.model_validate(dict(link=link, target_lang=target_lang))

    if video_raw := await redis_client.get(video.cache_key):
        cached = YouTubeVideoData.model_validate_json(video_raw)
        log.info("cache hit for %s", video.cache_key)
        try:
            await cached.reply_to(message)
        except TelegramBadRequest:
            log.info("cached telegram file ids failed to be posted, removing from cache")
            await redis_client.delete(video.cache_key)
        else:
            await on_yt_video_sent.send(link, message.chat.id, message.chat.type, message.bot, cached, False)
            return

    log.info("cache miss for %s, registering waiter", video.cache_key)
    waiter = Waiter(
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        reply_to_message_id=message.message_id,
    )
    is_first = await register_waiter(redis_client, video.cache_key, waiter, _YOUTUBE_WAITERS_TTL)
    if is_first:
        process_youtube_link.send(message.chat.id, link, target_lang.value)


@router.callback_query(F.data.startswith("aud:"))
async def get_audio(callback: types.CallbackQuery):
    await callback.answer()
    if not isinstance(callback.message, types.Message):
        return

    video_id = callback.data.removeprefix("aud:")
    cache_key = f"yt:{video_id}"

    video_raw = await redis_client.get(cache_key)
    if not video_raw:
        await callback.message.reply("❌ This video is no longer cached, please resend the link.")
        return

    # remove the button right away so a repeat tap can't queue/duplicate a delivery
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    video = YouTubeVideoData.model_validate_json(video_raw)
    if video.audio_file_id:
        await callback.message.answer_audio(
            video.audio_file_id,
            performer=video.yt.author,
            title=video.yt.title,
            duration=video.yt.length,
        )
        return

    log.info("cache miss for audio %s, registering waiter", cache_key)
    waiter = Waiter(
        chat_id=callback.message.chat.id,
        chat_type=callback.message.chat.type,
        reply_to_message_id=callback.message.message_id,
    )
    is_first = await register_waiter(redis_client, f"{cache_key}:audio", waiter, _YOUTUBE_WAITERS_TTL)
    if is_first:
        process_youtube_audio.send(callback.message.chat.id, video_id, callback.message.message_id)


async def _process_social_url(message: Message, url: str) -> None:
    video = SocialVideoData.model_validate(dict(link=url))

    if video_raw := await redis_client.get(video.cache_key):
        cached = SocialVideoData.model_validate_json(video_raw)
        log.info("cache hit for %s", video.cache_key)
        try:
            await cached.reply_to(message)
        except TelegramBadRequest:
            log.info("cached file ids invalid, clearing cache for %s", video.cache_key)
            await redis_client.delete(video.cache_key)
        else:
            await on_social_video_sent.send(url, message.chat.id, message.chat.type, message.bot, cached, False)
            return

    log.info("cache miss for %s, registering waiter", video.cache_key)
    waiter = Waiter(
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        reply_to_message_id=message.message_id,
    )
    is_first = await register_waiter(redis_client, video.cache_key, waiter, _SOCIAL_WAITERS_TTL)
    if is_first:
        process_social_link.send(message.chat.id, url)


@router.message(F.text.regexp(r"https?://"))
async def embed_social(message: types.Message):
    urls = [u for u in _URL_RE.findall(message.text) if not _YOUTUBE_URL_RE.match(u)]
    if not urls:
        return

    await on_link_received.send(message, LinkOrigin.SOCIAL)

    for url in urls:
        await _process_social_url(message, url)
