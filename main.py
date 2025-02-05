import asyncio
import os
import tempfile

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from dotenv import load_dotenv
import logging
from pytubefix import YouTube
import redis.asyncio as redis
import aiohttp

log = logging.getLogger(__name__)

dp = Dispatcher()
router = Router()


def log_link(message: types.Message, source: str):
    log.info('%s link: %s', source, message.text)


@router.message(CommandStart())
async def start(message: types.Message):
    await message.reply('Send a link and i will reply with a nice embedding or a video')


@router.message(F.text.regexp(r'^https://((www\.)?youtube\.com/(watch|shorts/)|youtu\.be/)'))
async def embed_youtube_shorts(message: types.Message):
    log_link(message, 'youtube')
    link = message.text
    # https://github.com/JuanBindez/pytubefix/pull/209
    yt = YouTube(link, 'WEB')
    redis_client = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)

    if file_id := await redis_client.get(f'yt-tg-file:{yt.video_id}'):
        log.info('cache hit for %s', yt.video_id)
        await message.reply_video(file_id)
    else:
        log.info('cache miss for %s', yt.video_id)
        with tempfile.TemporaryDirectory() as tmp:
            success = False
            for i in range(3):
                try:
                    # Telegram bot cannot upload a file bigger than 50Mb.
                    # Get the highest available quality under 50Mb.
                    streams = [s for s in yt.streams.filter(progressive=True).order_by('filesize').desc() if s.filesize_mb < 50]
                    if not streams:
                        log.info('no suitable stream is found for %s', yt.video_id)
                        await redis_client.aclose()
                        return
                    stream = streams[-1]
                    await asyncio.to_thread(stream.download, output_path=tmp, filename=yt.video_id)
                    success = True
                except Exception as e:
                    log.error(e)
                    await asyncio.sleep(2)
                else:
                    break
            if not success:
                log.error("failed to download youtube link %s", link)
                await redis_client.aclose()
                return
            filename = os.path.join(tmp, yt.video_id)
            rs = await message.reply_video(types.FSInputFile(filename))
        await redis_client.set(f'yt-tg-file:{yt.video_id}', rs.video.file_id)
        log.info('cached %s', yt.video_id)

    await redis_client.aclose()


@router.message(F.text.startswith('https://vm.tiktok.com/'))
async def embed_tiktok(message: types.Message):
    log_link(message, 'tiktok')
    link = message.text
    await message.reply(link.replace('vm.tiktok', 'vm.vxtiktok'))


@router.message(F.text.startswith('https://www.instagram.com/'))
async def embed_instagram(message: types.Message):
    log_link(message, 'instagram')
    link = message.text
    await message.reply(link.replace('www.instagram', 'www.ddinstagram'))


@router.message(F.text.startswith('https://x.com/'))
async def embed_x(message: types.Message):
    log_link(message, 'x.com')
    link = message.text
    await message.reply(link.replace('https://x.com/', 'https://fixupx.com/'))


@router.message(F.text.startswith('https://twitter.com/'))
async def embed_twitter(message: types.Message):
    log_link(message, 'twitter')
    link = message.text
    await message.reply(link.replace('https://twitter.com/', 'https://fxtwitter.com/'))


async def main():
    token = os.environ['BOT_TOKEN']
    bot = Bot(token, default=DefaultBotProperties(parse_mode='HTML'))
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == '__main__':
    load_dotenv()
    logging.basicConfig(
        level=getattr(logging, os.getenv('LOGLEVEL', 'INFO')),
        format='%(asctime)s %(levelname)-8s %(name)s - %(message)s'
    )
    # disable logs for non-handled events
    logging.getLogger('aiogram.event').setLevel(logging.WARNING)
    asyncio.run(main())
