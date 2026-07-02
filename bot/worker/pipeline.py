import asyncio
import logging
import math
import tempfile
from pathlib import Path

from aiogram import Bot, types
from aiogram.exceptions import TelegramNetworkError

from bot.config import settings
from bot.events.signals import on_yt_video_fail, on_social_video_fail
from bot.util.social.download import download_social_video
from bot.util.social.exc import SocialDownloadError
from bot.util.social.schema import SocialVideoData
from bot.util.youtube.exc import YouTubeError
from bot.util.youtube.schema import YouTubeVideoData
from bot.util.youtube.video import get_resolution, check_download_adaptive, split_video, MAX_FILE_SIZE_BYTES

log = logging.getLogger(__name__)


async def _upload_parts_to_dump_chat(bot: Bot, file_paths: list[Path], width: int, height: int) -> list[str]:
    file_ids = []
    for file_path in file_paths:
        for i in range(3):
            try:
                media_message = await bot.send_video(
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
    return file_ids


async def handle_youtube_video(bot: Bot, video: YouTubeVideoData) -> YouTubeVideoData:
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
            await on_yt_video_fail.send(video.link)
            raise exc

        width, height = get_resolution(stream)
        video.width = width
        video.height = height

        log.info('sending %d part(s) to dump chat to obtain file ids', len(file_paths))
        video.file_ids = await _upload_parts_to_dump_chat(bot, file_paths, width, height)
        return video


async def handle_social_video(bot: Bot, video: SocialVideoData) -> SocialVideoData:
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
            await on_social_video_fail.send(video.link)
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

        log.info("sending %d part(s) to dump chat for %s", len(file_paths), video.link)
        video.file_ids = await _upload_parts_to_dump_chat(bot, file_paths, video.width, video.height)
        return video
