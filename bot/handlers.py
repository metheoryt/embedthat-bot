import asyncio
import logging
import tempfile

from aiogram import types, F
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.filters import CommandStart
from aiogram.types import ErrorEvent, Message
from redis.asyncio.lock import Lock

from .config import settings
from .dispatcher import router
from .enum import LinkOrigin
from .events import on_yt_video_sent, on_yt_video_fail, on_link_sent, on_link_received
from .util.aiohttp import session
from .util.chat_action import send_chat_action_periodically
from .util.redis import redis_client
from .util.youtube.enum import TargetLang
from .util.youtube.exc import YouTubeError
from .util.youtube.schema import YouTubeVideoData
from .util.youtube.video import get_resolution, check_download_adaptive

log = logging.getLogger(__name__)


@router.error()
async def error_handler(event: ErrorEvent):
    log.critical("Critical error caused by %s", event.exception, exc_info=True)
    if settings.admin_chat_id:
        message = event.update.message
        msg = f"""
Exception:
`{event.exception!r}`
Message text:
`{message.text}`
""".strip()
        await message.bot.send_message(settings.admin_chat_id, msg, parse_mode="MarkdownV2")


@router.message(CommandStart())
async def start(message: types.Message):
    await message.reply("Send a link and i will reply with a nice embedding or a video")


async def handle_youtube_video(message: Message, video: YouTubeVideoData) -> YouTubeVideoData:
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

        log.info('sending %d files to dump chat to obtain file ids', len(file_paths))
        # If there are more than 1 part, send them one-by-one first and reuse file ids
        file_ids = []
        for file_path in file_paths:
            # Send the file and get its file_id
            for i in range(3):
                try:
                    media_message = await message.bot.send_video(
                        settings.dump_chat_id,
                        types.FSInputFile(file_path),
                        width=width,
                        height=height
                    )
                    break
                except TelegramNetworkError:
                    if i == 2:
                        raise
                    log.warning('failed to send a video file, retrying in 2 seconds')
                    await asyncio.sleep(2)
                    continue

            log.info("sent %s", file_path)
            file_ids.append(media_message.video.file_id)

        video.file_ids = file_ids
        video.width = width
        video.height = height
        return video


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
            video = await handle_youtube_video(message, video)
        finally:
            action_task.cancel()
            try:
                await action_task
            except asyncio.CancelledError:
                pass

        await redis_client.set(video.cache_key, video.model_dump_json())
        log.info("cached %s (%d files)", video.cache_key, len(video.file_ids))

        # send prepared and cached video to the user
        await video.reply_to(message)

        await on_yt_video_sent.send(link, message, video=video, fresh=True)


@router.message(F.text.startswith("https://vm.tiktok.com/"))
async def embed_tiktok(message: types.Message):
    await on_link_received.send(message, LinkOrigin.TIKTOK)
    link = message.text
    new_link = link.replace("vm.tiktok", "vm.kktiktok")
    await message.reply(new_link)
    await on_link_sent.send(new_link, message, origin=LinkOrigin.TIKTOK)


@router.message(F.text.startswith("https://www.instagram.com/"))
async def embed_instagram(message: types.Message):
    if message.text.startswith("https://www.instagram.com/stories/"):
        # stories require login
        return

    await on_link_received.send(message, LinkOrigin.INSTAGRAM)
    link = message.text
    success = False
    for domain in ['kkinstagram', 'ddinstagram', 'instagramez', 'vxinstagram']:
        new_link = link.replace(f"www.instagram", f"www.{domain}")
        try:
            async with session.get(new_link) as rs:
                rs.raise_for_status()
            success = True
            log.info("chosen: %s", new_link)
            break
        except Exception as e:
            # if the service is not working, do not send anything
            log.warning(e)

    if success:
        await message.reply(new_link)
        await on_link_sent.send(new_link, message, origin=LinkOrigin.INSTAGRAM)


@router.message(F.text.startswith("https://x.com/"))
async def embed_x(message: types.Message):
    await on_link_received.send(message, LinkOrigin.X)
    link = message.text
    new_link = link.replace("https://x.com/", "https://fixupx.com/")
    await message.reply(new_link)
    await on_link_sent.send(new_link, message, origin=LinkOrigin.X)


@router.message(F.text.startswith("https://twitter.com/"))
async def embed_twitter(message: types.Message):
    await on_link_received.send(message, LinkOrigin.TWITTER)
    link = message.text
    new_link = link.replace("https://twitter.com/", "https://fxtwitter.com/")
    await message.reply(new_link)
    await on_link_sent.send(new_link, message, origin=LinkOrigin.TWITTER)
