import asyncio
import logging
import os
import tempfile

from aiogram import types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from pytubefix import YouTube

from .dispatcher import router
from .enum import LinkOrigin
from .events import on_yt_video_sent, on_yt_video_fail, on_link_sent, on_link_received
from .util.aiohttp import session
from .util.redis import redis_client
from redis.asyncio.lock import Lock

log = logging.getLogger(__name__)


@router.message(CommandStart())
async def start(message: types.Message):
    await message.reply("Send a link and i will reply with a nice embedding or a video")

@router.message(
    F.text.regexp(r"^https://((www\.)?youtube\.com/(watch|shorts/)|youtu\.be/)")
)
async def embed_youtube_shorts(message: types.Message):
    await on_link_received.send(message, LinkOrigin.YOUTUBE)
    link = message.text.split()[0]  # as regex states, we expect first element in text to be a link
    # https://github.com/JuanBindez/pytubefix/pull/209
    yt = YouTube(link, "WEB")

    async with Lock(redis_client, f'yt-lock:{yt.video_id}', timeout=2*60, blocking_timeout=2*60):
        if file_id := await redis_client.get(f"yt-tg-file:{yt.video_id}"):
            log.info("cache hit for %s", yt.video_id)
            try:
                await message.reply_video(file_id)
            except TelegramBadRequest:
                log.info("telegram file was not found by cached id: %s", file_id)
                await redis_client.delete(f"yt-tg-file:{yt.video_id}")
            else:
                await on_yt_video_sent.send(link, message, file_id=file_id, fresh=False)
                return

        log.info("cache miss for %s", yt.video_id)
        with tempfile.TemporaryDirectory() as tmp:
            success = False
            for i in range(3):
                try:
                    # Telegram bot cannot upload a file bigger than 50Mb.
                    # Get the highest available quality under 50Mb.
                    streams = [
                        s for s in yt.streams.filter(progressive=True)
                        .order_by("filesize")
                        .desc()
                        if s.filesize_mb < 50
                    ]
                    if not streams:
                        log.info("no suitable stream is found for %s", yt.video_id)
                        return
                    stream = streams[-1]
                    await asyncio.to_thread(stream.download, output_path=tmp, filename=yt.video_id)
                    success = True
                except Exception:
                    log.exception("failed to download %s", yt.video_id)
                    await asyncio.sleep(2)
                else:
                    break
            if not success:
                log.error("failed to download youtube link %s", link)
                await on_yt_video_fail.send(link, message)
                return
            filename = os.path.join(tmp, yt.video_id)
            rs = await message.reply_video(types.FSInputFile(filename))
        await redis_client.set(f"yt-tg-file:{yt.video_id}", rs.video.file_id)
        log.info("cached %s", yt.video_id)
        await on_yt_video_sent.send(link, message, file_id=rs.video.file_id, fresh=True)


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
