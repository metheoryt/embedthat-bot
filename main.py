import asyncio
import os
import tempfile

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from dotenv import load_dotenv
import logging
from pytubefix import YouTube
from pytubefix.cli import on_progress
import redis.asyncio as redis


log = logging.getLogger(__name__)


dp = Dispatcher()
router = Router()


@router.message(CommandStart())
async def start(message: types.Message):
    await message.bot.send_message(
        message.chat.id, 'Send a link and i will reply with a nice embedding or a video'
    )


@router.message(F.text.regexp(r'^https://(www\.)?youtube\.com/(watch|shorts/)'))
async def embed_youtube_shorts(message: types.Message):
    link = message.text
    log.info('youtube link: %s', link)
    yt = YouTube(link, on_progress_callback=on_progress)
    redis_client = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    if file_id := await redis_client.get(f'yt-tg-file:{yt.video_id}'):
        await redis_client.aclose()
        return await message.bot.send_video(
            message.chat.id,
            file_id,
            reply_to_message_id=message.message_id
        )
    stream = yt.streams.filter(progressive=True, file_extension='mp4').get_highest_resolution()
    with tempfile.TemporaryDirectory() as tmp:
        await asyncio.to_thread(stream.download, output_path=tmp, filename=yt.video_id)
        filename = os.path.join(tmp, yt.video_id)
        rs = await message.bot.send_video(
            message.chat.id,
            types.FSInputFile(filename),
            reply_to_message_id=message.message_id
        )
    await redis_client.set(f'yt-tg-file:{yt.video_id}', rs.video.file_id)
    await redis_client.aclose()


@router.message(F.text.startswith('https://vm.tiktok.com/'))
async def embed_tiktok(message: types.Message):
    link = message.text
    log.info('tiktok link: %s', link)
    await message.reply(link.replace('vm.tiktok', 'vm.vxtiktok'))


@router.message(F.text.startswith('https://www.instagram.com/'))
async def embed_instagram(message: types.Message):
    link = message.text
    log.info('instagram link: %s', link)
    await message.reply(link.replace('www.instagram', 'www.ddinstagram'))


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
    asyncio.run(main())
