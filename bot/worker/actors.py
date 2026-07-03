import asyncio
import logging
import tempfile
from pathlib import Path

import dramatiq
import redis.asyncio as redis
from aiogram import Bot, types
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from redis.asyncio.lock import Lock

from bot.config import settings
from bot.events.signals import on_yt_video_sent, on_social_video_sent
from bot.util.redis_lock import HeartbeatLock
from bot.util.social.exc import SocialDownloadError
from bot.util.social.schema import SocialVideoData
from bot.util.youtube.enum import TargetLang
from bot.util.youtube.exc import YouTubeError
from bot.util.youtube.schema import YouTubeVideoData
from bot.util.youtube.video import get_audio_stream
from bot.worker.broker import broker  # noqa: F401 -- registers the Redis broker before actors are declared
from bot.worker.chat_action import with_chat_action
from bot.worker.error_reporting import report_actor_failure  # noqa: F401 -- registers the actor with the broker
from bot.worker.pipeline import handle_social_video, handle_youtube_video
from bot.worker.waiters import Waiter, pop_waiters

log = logging.getLogger(__name__)


async def _safe_edit_ack(bot: Bot, chat_id: int, message_id: int | None, text: str) -> None:
    if message_id is None:
        return
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest:
        log.warning("could not edit ack message %s in chat %s", message_id, chat_id)


async def _safe_delete_ack(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if message_id is None:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        log.warning("could not delete ack message %s in chat %s", message_id, chat_id)


async def _pop_waiters(redis_client: redis.Redis, cache_key: str) -> list[Waiter]:
    """pop_waiters, deduped by chat_id so a re-sent link/re-tapped button can't cause a double delivery."""
    waiters = await pop_waiters(redis_client, cache_key)
    seen: set[int] = set()
    deduped = []
    for waiter in waiters:
        if waiter.chat_id not in seen:
            seen.add(waiter.chat_id)
            deduped.append(waiter)
    return deduped


async def _notify_waiters_success(bot: Bot, waiters: list[Waiter], video) -> None:
    for waiter in waiters:
        await _safe_delete_ack(bot, waiter.chat_id, waiter.ack_message_id)
        await video.send_to_chat(bot, waiter.chat_id, reply_to_message_id=waiter.reply_to_message_id)


async def _notify_waiters_failure(bot: Bot, waiters: list[Waiter], text: str) -> None:
    for waiter in waiters:
        if waiter.ack_message_id is not None:
            await _safe_edit_ack(bot, waiter.chat_id, waiter.ack_message_id, text)
        else:
            await bot.send_message(waiter.chat_id, text, reply_to_message_id=waiter.reply_to_message_id)


async def _notify_audio_waiters_success(bot: Bot, waiters: list[Waiter], video: YouTubeVideoData) -> None:
    for waiter in waiters:
        await bot.send_audio(
            waiter.chat_id,
            video.audio_file_id,
            performer=video.yt.author,
            title=video.yt.title,
            duration=video.yt.length,
        )


@with_chat_action()
async def _process_youtube_link_async(bot: Bot, chat_id: int, link: str, target_lang_value: str) -> None:
    redis_client = redis.from_url(str(settings.redis_dsn), decode_responses=True)
    try:
        target_lang = TargetLang(target_lang_value)
        video = YouTubeVideoData.model_validate(dict(link=link, target_lang=target_lang))

        lock = Lock(redis_client, f'{video.cache_key}:lock', timeout=10 * 60, blocking_timeout=11 * 60)
        async with HeartbeatLock(lock):
            try:
                video = await handle_youtube_video(bot, video)
            except YouTubeError as e:
                waiters = await _pop_waiters(redis_client, video.cache_key)
                await _notify_waiters_failure(bot, waiters, f"❌ Couldn't process this video: {e}")
                raise

            await redis_client.set(video.cache_key, video.model_dump_json())
            log.info("cached %s (%d files)", video.cache_key, len(video.file_ids))

            waiters = await _pop_waiters(redis_client, video.cache_key)
            await _notify_waiters_success(bot, waiters, video)
            for waiter in waiters:
                await on_yt_video_sent.send(link, waiter.chat_id, waiter.chat_type, bot, video, True)
    finally:
        await redis_client.aclose()


@dramatiq.actor(
    max_retries=2,
    min_backoff=30_000,
    max_backoff=5 * 60_000,
    time_limit=45 * 60_000,
    throws=(YouTubeError,),
    on_retry_exhausted="report_actor_failure",
)
def process_youtube_link(chat_id: int, link: str, target_lang: str):
    bot = Bot(token=settings.bot_token)
    try:
        asyncio.run(_process_youtube_link_async(bot, chat_id, link, target_lang))
    finally:
        asyncio.run(bot.session.close())


@with_chat_action(ChatAction.UPLOAD_VOICE)
async def _process_youtube_audio_async(bot: Bot, chat_id: int, video_id: str, reply_to_message_id: int) -> None:
    redis_client = redis.from_url(str(settings.redis_dsn), decode_responses=True)
    try:
        cache_key = f"yt:{video_id}"
        audio_waiters_key = f"{cache_key}:audio"

        video_raw = await redis_client.get(cache_key)
        if not video_raw:
            log.error("cache entry %s vanished before audio extraction could run", cache_key)
            waiters = await _pop_waiters(redis_client, audio_waiters_key)
            await _notify_waiters_failure(bot, waiters, "❌ This video is no longer cached, please resend the link.")
            return

        video = YouTubeVideoData.model_validate_json(video_raw)

        lock = Lock(redis_client, f'{audio_waiters_key}:lock', timeout=10 * 60, blocking_timeout=11 * 60)
        async with HeartbeatLock(lock):
            if not video.audio_file_id:
                with tempfile.TemporaryDirectory() as tmp:
                    try:
                        audio_path = await asyncio.to_thread(get_audio_stream, video, Path(tmp))
                    except YouTubeError as e:
                        waiters = await _pop_waiters(redis_client, audio_waiters_key)
                        await _notify_waiters_failure(bot, waiters, f"❌ Couldn't extract audio: {e}")
                        raise

                    for i in range(3):
                        try:
                            media_message = await bot.send_audio(
                                settings.dump_chat_id,
                                types.FSInputFile(audio_path),
                                performer=video.yt.author,
                                title=video.yt.title,
                                duration=video.yt.length,
                            )
                            break
                        except TelegramNetworkError:
                            if i == 2:
                                raise
                            log.warning('failed to send an audio file, retrying in 2 seconds')
                            await asyncio.sleep(2)

                video.audio_file_id = media_message.audio.file_id
                await redis_client.set(cache_key, video.model_dump_json())
                log.info("cached audio for %s", cache_key)

            waiters = await _pop_waiters(redis_client, audio_waiters_key)
            await _notify_audio_waiters_success(bot, waiters, video)
    finally:
        await redis_client.aclose()


@dramatiq.actor(
    max_retries=2,
    min_backoff=30_000,
    max_backoff=5 * 60_000,
    time_limit=20 * 60_000,
    throws=(YouTubeError,),
    on_retry_exhausted="report_actor_failure",
)
def process_youtube_audio(chat_id: int, video_id: str, reply_to_message_id: int):
    bot = Bot(token=settings.bot_token)
    try:
        asyncio.run(_process_youtube_audio_async(bot, chat_id, video_id, reply_to_message_id))
    finally:
        asyncio.run(bot.session.close())


@with_chat_action()
async def _process_social_link_async(bot: Bot, chat_id: int, url: str) -> None:
    redis_client = redis.from_url(str(settings.redis_dsn), decode_responses=True)
    try:
        video = SocialVideoData.model_validate(dict(link=url))

        lock = Lock(redis_client, f'{video.cache_key}:lock', timeout=20 * 60, blocking_timeout=21 * 60)
        async with HeartbeatLock(lock):
            try:
                video = await handle_social_video(bot, video)
            except SocialDownloadError as e:
                waiters = await _pop_waiters(redis_client, video.cache_key)
                await _notify_waiters_failure(bot, waiters, f"❌ Couldn't download this video: {e}")
                raise

            await redis_client.set(video.cache_key, video.model_dump_json())
            log.info("cached %s (%s)", video.cache_key, video.origin)

            waiters = await _pop_waiters(redis_client, video.cache_key)
            await _notify_waiters_success(bot, waiters, video)
            for waiter in waiters:
                await on_social_video_sent.send(url, waiter.chat_id, waiter.chat_type, bot, video, True)
    finally:
        await redis_client.aclose()


@dramatiq.actor(
    max_retries=2,
    min_backoff=30_000,
    max_backoff=5 * 60_000,
    time_limit=25 * 60_000,
    throws=(SocialDownloadError,),
    on_retry_exhausted="report_actor_failure",
)
def process_social_link(chat_id: int, url: str):
    bot = Bot(token=settings.bot_token)
    try:
        asyncio.run(_process_social_link_async(bot, chat_id, url))
    finally:
        asyncio.run(bot.session.close())
