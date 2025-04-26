from pathlib import Path
import math
from pytube import YouTube, Stream
import subprocess
import logging
import ffmpeg


log = logging.getLogger(__name__)


MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50MB

def get_resolution(stream: Stream) -> tuple[int, int]:
    probe = ffmpeg.probe(stream.url, v='error', select_streams='v:0', show_entries='stream=width,height')
    width = probe['streams'][0]['width']
    height = probe['streams'][0]['height']
    return width, height


def pick_stream(streams: list[Stream], audio_stream: Stream) -> tuple[Stream, int] | tuple[None, None]:
    # select a stream that can be split into as few parts as possible
    audio_size = audio_stream.filesize
    for n_parts in range(1, 11):  # 10 max (what an album can fit)
        for stream in streams:
            total_size = audio_size + stream.filesize
            if total_size <= MAX_FILE_SIZE_BYTES * 0.9 * n_parts:  # leave 10% for turbulence
                log.info('selected stream (split for %d parts, %dMb size): %s', n_parts, total_size // 1024 // 1024, stream)
                return stream, n_parts

    return None, None


def split_video(duration_seconds: int, input_path: Path, output_dir: Path, n_parts: int) -> list[Path]:
    segment_time = math.ceil(duration_seconds / n_parts)
    log.info('video of %d duration will be split by %d parts of %d seconds', duration_seconds, n_parts, segment_time)
    output_pattern = output_dir / (input_path.stem + "_part_%03d.mp4")

    subprocess.run(
        [
            "ffmpeg",
            "-i", str(input_path),
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(segment_time),
            "-reset_timestamps", "1",
            str(output_pattern)
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    parts = sorted(output_dir.glob(input_path.stem + "_part_*.mp4"))
    return parts


def check_download_adaptive(yt: YouTube, output_path: str, min_res: int = 360) -> tuple[Stream, list[Path]] | tuple[None, None]:
    output_path = Path(output_path)

    # We always want the highest audio quality
    audio_streams = yt.streams.filter(file_extension='mp4', only_audio=True).order_by('abr').desc()
    log.info('adaptive audio streams: %s', audio_streams)
    audio_stream = audio_streams.first()
    if not audio_stream:
        # we don't want adaptive without a sound
        log.info('no adaptive audio stream found')
        return None, None

    video_streams = yt.streams.filter(file_extension='mp4', subtype='mp4', only_video=True).order_by('resolution').desc()
    log.info('adaptive video streams: %s', video_streams)

    # filter only supported streams
    video_streams = [
        s for s in video_streams if
        s.resolution
        and int(s.resolution.replace('p', '')) >= min_res
        and 'avc1' in s.codecs[0]
    ]

    # pick one that fits best
    video_stream, n_parts = pick_stream(video_streams, audio_stream)

    if not video_stream:
        log.info('no adaptive video stream found for %ss video length', yt.length)
        return None, None

    log.info('downloading video stream')
    video_stream_path = video_stream.download(output_path=output_path, filename=f'{yt.video_id}.video.mp4')

    log.info('downloading audio stream')
    audio_stream_path = audio_stream.download(output_path=output_path, filename=f'{yt.video_id}.audio.mp4')

    log.info('merging streams')
    merged_stream_path = Path(output_path) / f"{yt.video_id}.mp4"
    command = [
        'ffmpeg',
        '-y',  # Overwrite an output file if exists
        '-i', video_stream_path,
        '-i', audio_stream_path,
        '-c:v', 'copy',  # Copy video codec without re-encoding
        '-c:a', 'copy',  # Same for audio
        '-movflags', '+faststart',
        str(merged_stream_path)
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if n_parts == 1:
        video_files = [merged_stream_path]
    else:
        video_files = split_video(
            duration_seconds=yt.length,
            input_path=merged_stream_path,
            output_dir=output_path,
            n_parts=n_parts
        )

    for file in video_files:
        log.info("%s size: %dMb", file, file.stat().st_size // 1024 // 1024)

    return video_stream, video_files
