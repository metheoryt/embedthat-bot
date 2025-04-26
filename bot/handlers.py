import asyncio
import logging
import os
import tempfile
from pathlib import Path

from aiogram import types, F, enums
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import ErrorEvent
from pytube import Stream
from pytubefix import YouTube

from .dispatcher import router
from .enum import LinkOrigin
from .events import on_yt_video_sent, on_yt_video_fail, on_link_sent, on_link_received
from .util.aiohttp import session
from .util.redis import redis_client
from .util.youtube import check_download_adaptive, get_resolution
from redis.asyncio.lock import Lock
from bot.config import settings
import json

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


@router.message(
    F.text.regexp(r"^https://((www\.)?youtube\.com/(watch|shorts/)|youtu\.be/)")
)
async def embed_youtube_videos(message: types.Message):
    await on_link_received.send(message, LinkOrigin.YOUTUBE)
    link = message.text.split()[0]  # as regex states, we expect first element in text to be a link
    # https://github.com/JuanBindez/pytubefix/pull/209
    yt = YouTube(link, "WEB")

    yt_videos_key = f"yt-videos:{yt.video_id}"
    async with Lock(redis_client, f'yt-lock:{yt.video_id}', timeout=10*60, blocking_timeout=11*60):
        if file_ids := await redis_client.lrange(yt_videos_key, 0, -1):
            log.info("cache hit for %s (%d files)", yt.video_id, len(file_ids))
            file_datas = [json.loads(file_id) for file_id in file_ids]
            try:
                if len(file_datas) > 1:
                    media_group = [
                        types.InputMediaVideo(
                            media=f['file_id'],
                            width=f['width'],
                            height=f['height'],
                            caption=f"{i+1}/{len(file_datas)}",
                        ) for i, f in enumerate(file_datas)
                    ]
                    await message.reply_media_group(media_group)
                else:
                    f = file_datas[0]
                    await message.reply_video(f['file_id'], width=f['width'], height=f['height'])

            except TelegramBadRequest:
                log.info("cached telegram file ids failed to be posted, removing from cache")
                await redis_client.delete(yt_videos_key)
            else:
                await on_yt_video_sent.send(link, message, file_datas=file_datas, fresh=False)
                return

        log.info("cache miss for %s", yt.video_id)
        await message.bot.send_chat_action(message.chat.id, enums.ChatAction.UPLOAD_VIDEO)

        with tempfile.TemporaryDirectory() as tmp:
            success = False
            for i in range(3):
                try:
                    stream, file_paths = await asyncio.to_thread(check_download_adaptive, yt=yt, output_path=tmp)
                    stream: Stream
                    if not stream:
                        log.info('cannot download youtube link %s', link)
                        return
                except Exception:
                    log.exception("failed to download %s on try #%d", yt.video_id, i+1)
                    await asyncio.sleep(2)
                else:
                    success = True
                    break

            if not success:
                log.error("finally failed to download youtube link %s", link)
                await on_yt_video_fail.send(link, message)
                return

            await message.bot.send_chat_action(message.chat.id, enums.ChatAction.UPLOAD_VIDEO)
            width, height = get_resolution(stream)

            if len(file_paths) > 1:
                log.info('too many parts, sending one by one to obtain file ids')
                # If there are more than 1 part, send them one-by-one first, and reuse file ids
                file_ids = []
                for file_path in file_paths:
                    # Send the file and get its file_id
                    media_message = await message.bot.send_video(settings.dump_chat_id, types.FSInputFile(file_path), width=width, height=height)
                    log.info("sent %s", file_path)
                    file_ids.append(media_message.video.file_id)

                media_group = [
                    types.InputMediaVideo(
                        media=file_id,
                        width=width,
                        height=height,
                        caption=f"{i+1}/{len(file_ids)}"
                    )
                    for i, file_id in enumerate(file_ids)
                ]
                await message.reply_media_group(media_group)

            else:
                log.info('sending single video directly')
                rs = await message.reply_video(types.FSInputFile(file_paths[0]), width=width, height=height)
                file_ids = [rs.video.file_id]

        file_datas = [{"file_id": file_id, "width": width, "height": height} for file_id in file_ids]
        file_datas_raw = [json.dumps(file_data) for file_data in file_datas]
        await redis_client.rpush(yt_videos_key, *file_datas_raw)
        log.info("cached %s (%d files)", yt.video_id, len(file_datas))
        await on_yt_video_sent.send(link, message, file_datas=file_datas, fresh=True)


@router.message(F.text.startswith("https://vm.tiktok.com/"))
async def embed_tiktok(message: types.Message):
    await on_link_received.send(message, LinkOrigin.TIKTOK)
    link = message.text
    new_link = link.replace("vm.tiktok", "vm.vxtiktok")
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
