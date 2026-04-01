import asyncio
import hashlib
import logging
import math
import re
import tempfile
from pathlib import Path

from aiogram import types, F
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.filters import Command, CommandStart
from aiogram.types import ErrorEvent, Message
from redis.asyncio.lock import Lock

from .config import settings
from .dispatcher import router
from .enum import LinkOrigin
from .events import (
    on_yt_video_sent, on_yt_video_fail,
    on_social_video_sent, on_social_video_fail,
    on_link_received,
)
from .util.chat_action import send_chat_action_periodically
from .util.stats import build_stats_report
from .util.redis import redis_client
from .util.social import SocialVideoData, SocialDownloadError, download_social_video
from .util.youtube.enum import TargetLang
from .util.youtube.exc import YouTubeError
from .util.youtube.schema import YouTubeVideoData
from .util.youtube.video import get_resolution, check_download_adaptive, split_video, MAX_FILE_SIZE_BYTES

log = logging.getLogger(__name__)


def _social_cache_key(link: str) -> str:
    return f"dl:{hashlib.sha256(link.encode()).hexdigest()[:16]}"


_URL_RE = re.compile(r"https?://\S+")
_YOUTUBE_URL_RE = re.compile(r"https?://((www|m)\.)?youtube\.com/|https?://youtu\.be/")


@router.error()
async def error_handler(event: ErrorEvent):
    log.critical("Critical error caused by %s", event.exception, exc_info=True)
    if settings.admin_chat_id:
        message = event.update.message
        if not message:
            return
        msg = f"""\
            Exception:
            `{event.exception!r}`
            Message text:
            `{message.text}`
            """
        await message.bot.send_message(settings.admin_chat_id, msg, parse_mode="MarkdownV2")


@router.message(CommandStart())
async def start(message: types.Message):
    await message.reply("Send a link and i will reply with a nice embedding or a video")


@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if not settings.admin_chat_id or message.chat.id != settings.admin_chat_id:
        return
    await message.reply(await build_stats_report())


async def handle_youtube_video(message: Message, video: YouTubeVideoData) -> tuple[YouTubeVideoData, bool]:
    with tempfile.TemporaryDirectory() as tmp:
        exc = None
        for i in range(3):
            try:
                stream, file_paths = await asyncio.to_thread(
                    check_download_adaptive,
                    video=video,
                    output_path=tmp,
                )
                exc = None
                break
            except YouTubeError:
                # raise YouTubeError directly (it is an unrecoverable error)
                raise
            except Exception as ex:
                exc = ex
                log.error("failed to download %s on try #%d: %r", video.yt.video_id, i + 1, exc)
                await asyncio.sleep(2)

        if exc:
            log.error("finally failed to download youtube link %s: %r", video.link, exc)
            await on_yt_video_fail.send(video.link, message)
            raise exc

        width, height = get_resolution(stream)
        video.width = width
        video.height = height

        if len(file_paths) == 1:
            log.info('sending single file directly to user')
            for i in range(3):
                try:
                    sent = await message.reply_video(
                        types.FSInputFile(file_paths[0]),
                        width=width,
                        height=height,
                        caption=video.caption,
                    )
                    break
                except TelegramNetworkError:
                    if i == 2:
                        raise
                    log.warning('failed to send a video file, retrying in 2 seconds')
                    await asyncio.sleep(2)
            video.file_ids = [sent.video.file_id]
            return video, True

        log.info('sending %d parts to dump chat to obtain file ids', len(file_paths))
        file_ids = []
        for file_path in file_paths:
            for i in range(3):
                try:
                    media_message = await message.bot.send_video(
                        settings.dump_chat_id,
                        types.FSInputFile(file_path),
                        width=width,
                        height=height,
                    )
                    break
                except TelegramNetworkError:
                    if i == 2:
                        raise
                    log.warning('failed to send a video file, retrying in 2 seconds')
                    await asyncio.sleep(2)

            log.info("sent %s", file_path)
            file_ids.append(media_message.video.file_id)

        video.file_ids = file_ids
        return video, False


async def handle_social_video(message: Message, video: SocialVideoData) -> SocialVideoData:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        exc = None

        for i in range(3):
            try:
                result = await asyncio.to_thread(download_social_video, video.link, tmp_path)
                exc = None
                break
            except SocialDownloadError:
                raise  # unrecoverable — private account, removed video, geo-block
            except Exception as ex:
                exc = ex
                log.error("failed to download social %s on try #%d: %r", video.link, i + 1, exc)
                await asyncio.sleep(2)

        if exc:
            log.error("finally failed to download social link %s: %r", video.link, exc)
            await on_social_video_fail.send(video.link, message)
            raise exc

        video.video_id = result.video_id
        video.width = result.width
        video.height = result.height
        video.title = result.title
        video.origin = result.extractor.lower()

        file_size = result.file_path.stat().st_size
        if file_size <= MAX_FILE_SIZE_BYTES:
            file_paths = [result.file_path]
        else:
            n_parts = math.ceil(file_size / MAX_FILE_SIZE_BYTES)
            file_paths = split_video(
                duration_seconds=result.duration,
                input_path=result.file_path,
                output_dir=tmp_path,
                n_parts=n_parts,
            )
            while any(p.stat().st_size > MAX_FILE_SIZE_BYTES for p in file_paths):
                n_parts += 1
                if n_parts > 10:
                    raise SocialDownloadError("Video too large, cannot split into <= 10 parts")
                file_paths = split_video(
                    duration_seconds=result.duration,
                    input_path=result.file_path,
                    output_dir=tmp_path,
                    n_parts=n_parts,
                )

        if len(file_paths) == 1:
            log.info('sending single file directly to user for %s', video.link)
            for i in range(3):
                try:
                    sent = await message.reply_video(
                        types.FSInputFile(file_paths[0]),
                        width=video.width,
                        height=video.height,
                        caption=video.caption,
                    )
                    break
                except TelegramNetworkError:
                    if i == 2:
                        raise
                    log.warning("TelegramNetworkError sending social video, retrying")
                    await asyncio.sleep(2)
            video.file_ids = [sent.video.file_id]
            return video, True

        log.info("sending %d parts to dump chat for %s", len(file_paths), video.link)
        file_ids = []
        for file_path in file_paths:
            for i in range(3):
                try:
                    media_message = await message.bot.send_video(
                        settings.dump_chat_id,
                        types.FSInputFile(file_path),
                        width=video.width,
                        height=video.height,
                    )
                    break
                except TelegramNetworkError:
                    if i == 2:
                        raise
                    log.warning("TelegramNetworkError uploading social video part, retrying")
                    await asyncio.sleep(2)
            log.info("uploaded %s → file_id %s", file_path.name, media_message.video.file_id)
            file_ids.append(media_message.video.file_id)

        video.file_ids = file_ids
        return video, False


@router.message(
    F.text.regexp(r"^https://(((www|m)\.)?youtube\.com/(watch|shorts/)|youtu\.be/)")
)
async def embed_youtube_videos(message: types.Message):
    await on_link_received.send(message, LinkOrigin.YOUTUBE)
    link = message.text.split()[0]  # as regex states, we expect the first element in the text to be a link

    log.info('user lang: %s', message.from_user.language_code)
    try:
        target_lang = TargetLang(message.from_user.language_code)
    except ValueError:
        target_lang = TargetLang.ORIGINAL
        log.info('no translation will be performed')

    video = YouTubeVideoData.model_validate(dict(link=link, target_lang=target_lang))

    async with Lock(redis_client, f'{video.cache_key}:lock', timeout=10*60, blocking_timeout=11*60):
        if video_raw := await redis_client.get(video.cache_key):
            video = YouTubeVideoData.model_validate_json(video_raw)
            log.info("cache hit for %s", video.cache_key)
            try:
                await video.reply_to(message)
            except TelegramBadRequest:
                log.info("cached telegram file ids failed to be posted, removing from cache")
                await redis_client.delete(video.cache_key)
            else:
                await on_yt_video_sent.send(link, message, video=video, fresh=False)
                return

        log.info("cache miss for %s", video.cache_key)

        action_task = await send_chat_action_periodically(message.bot, message.chat.id, ChatAction.UPLOAD_VIDEO)

        try:
            video, already_sent = await handle_youtube_video(message, video)
        finally:
            action_task.cancel()
            try:
                await action_task
            except asyncio.CancelledError:
                pass

        await redis_client.set(video.cache_key, video.model_dump_json())
        log.info("cached %s (%d files)", video.cache_key, len(video.file_ids))

        if not already_sent:
            await video.reply_to(message)

        await on_yt_video_sent.send(link, message, video=video, fresh=True)


async def _process_social_url(message: Message, url: str) -> None:
    cache_key = _social_cache_key(url)

    async with Lock(redis_client, f'{cache_key}:lock', timeout=20*60, blocking_timeout=21*60):
        if video_raw := await redis_client.get(cache_key):
            video = SocialVideoData.model_validate_json(video_raw)
            log.info("cache hit for %s", cache_key)
            try:
                await video.reply_to(message)
            except TelegramBadRequest:
                log.info("cached file ids invalid, clearing cache for %s", cache_key)
                await redis_client.delete(cache_key)
            else:
                await on_social_video_sent.send(url, message, video=video, fresh=False)
                return

        log.info("cache miss for %s", cache_key)
        try:
            video = SocialVideoData.model_validate(dict(link=url))
            video, already_sent = await handle_social_video(message, video)
        except SocialDownloadError as e:
            log.info("yt-dlp could not download %s: %r", url, e)
            return
        except Exception as e:
            log.error("unexpected error downloading %s: %r", url, e)
            return

        await redis_client.set(cache_key, video.model_dump_json())
        log.info("cached %s (%s)", cache_key, video.origin)
        if not already_sent:
            await video.reply_to(message)
        await on_social_video_sent.send(url, message, video=video, fresh=True)


@router.message(F.text.regexp(r"https?://"))
async def embed_social(message: types.Message):
    urls = [u for u in _URL_RE.findall(message.text) if not _YOUTUBE_URL_RE.match(u)]
    if not urls:
        return

    await on_link_received.send(message, LinkOrigin.SOCIAL)

    action_task = await send_chat_action_periodically(message.bot, message.chat.id, ChatAction.UPLOAD_VIDEO)
    try:
        for url in urls:
            await _process_social_url(message, url)
    finally:
        action_task.cancel()
        try:
            await action_task
        except asyncio.CancelledError:
            pass
