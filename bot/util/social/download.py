import logging
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from .exc import SocialDownloadError

log = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    file_path: Path
    video_id: str
    width: int
    height: int
    title: str
    duration: int  # seconds


def download_social_video(url: str, output_dir: Path) -> DownloadResult:
    """
    Synchronous yt-dlp download. Call via asyncio.to_thread in the handler.

    Raises SocialDownloadError for unrecoverable failures (private/removed/geo-blocked).
    """
    ydl_opts = {
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as e:
            raise SocialDownloadError(str(e)) from e

    video_id = info["id"]
    file_path = output_dir / f"{video_id}.mp4"
    if not file_path.exists():
        raise SocialDownloadError(f"Downloaded file not found: {file_path}")

    return DownloadResult(
        file_path=file_path,
        video_id=video_id,
        width=info.get("width") or 0,
        height=info.get("height") or 0,
        title=info.get("title") or "",
        duration=int(info.get("duration") or 0),
    )
