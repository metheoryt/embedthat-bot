import os
from pathlib import Path
import math
from pytube import YouTube, Stream
import subprocess
import logging
import ffmpeg


log = logging.getLogger(__name__)


class YouTubeError(Exception): pass


MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50MB

def get_resolution(stream: Stream) -> tuple[int, int]:
    probe = ffmpeg.probe(stream.url, v='error', select_streams='v:0', show_entries='stream=width,height')
    width = probe['streams'][0]['width']
    height = probe['streams'][0]['height']
    return width, height


def pick_stream(yt: YouTube, output_path: Path, min_res: int) -> tuple[Stream, int, Path]:
    # select a stream that can be split into as few parts as possible
    # We always want the highest audio quality
    audio_streams = yt.streams.filter(file_extension='mp4', only_audio=True).order_by('abr').desc()
    log.info('adaptive audio streams: %s', audio_streams)
    audio_stream = audio_streams.first()
    if not audio_stream:
        # we don't want adaptive without a sound
        raise YouTubeError('no adaptive audio stream found')
    log.info('downloading audio stream')
    audio_stream_path = audio_stream.download(output_path=str(output_path), filename=f'{yt.video_id}.audio.mp4')
    audio_size = Path(audio_stream_path).stat().st_size

    video_streams = yt.streams.filter(file_extension='mp4', subtype='mp4', only_video=True).order_by('resolution').desc()
    # filter only supported streams
    video_streams = [
        s for s in video_streams if
        s.resolution
        and int(s.resolution.replace('p', '')) >= min_res
        and 'avc1' in s.codecs[0]
    ]
    log.info('supported adaptive video streams: %s', video_streams)

    for n_parts in range(1, 11):  # 10 max (what an album can fit)
        for stream in video_streams:
            stream: Stream
            total_size = audio_size + stream.filesize

            max_size = MAX_FILE_SIZE_BYTES * 0.98
            # if n_parts == 1:
            #     max_size = MAX_FILE_SIZE_BYTES * 0.95
            # else:
            #     max_size = MAX_FILE_SIZE_BYTES * 0.9  # leave 10% space for split overhead

            log.info(
                "%dMb total size (%dMb/part) for %d parts for %s",
                total_size // 1024 // 1024,
                total_size // 1024 // 1024 // n_parts,
                n_parts,
                stream
            )
            if total_size > max_size * n_parts:
                continue

            # download video stream to assess its real size
            video_stream_filename = Path(f'{yt.video_id}.{stream.resolution}.{stream.codecs[0]}.video.mp4')
            video_stream_path = output_path / video_stream_filename
            if not video_stream_path.exists():
                log.info('downloading %s video stream', video_stream_filename)
                video_stream_path = stream.download(output_path=str(output_path), filename=str(video_stream_filename))
                video_stream_path = Path(video_stream_path)

            log.info('%s size %dMb', video_stream_path, video_stream_path.stat().st_size // 1024 // 1024)
            # merge A+V and recheck the total size
            merged_stream_filename = Path(f'{yt.video_id}.{stream.resolution}.{stream.codecs[0]}.mp4')
            merged_stream_path = output_path / merged_stream_filename
            if not merged_stream_path.exists():
                log.info('merging streams')
                command = [
                    'ffmpeg',
                    '-y',  # Overwrite an output file if exists
                    '-i', str(video_stream_path),
                    '-i', audio_stream_path,
                    '-c:v', 'copy',  # Copy video codec without re-encoding
                    '-c:a', 'copy',  # Same for audio
                    '-movflags', '+faststart',
                    str(merged_stream_path)
                ]
                subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            merged_size = merged_stream_path.stat().st_size
            log.info("%s merged size: %dMb", merged_stream_path, merged_size // 1024 // 1024)

            if merged_size > max_size * n_parts:
                # if the video is too big, don't select it or
                log.info("%s is too big for %d parts, continue", merged_stream_filename, n_parts)
                continue

            log.info('selected stream (%d parts, %dMb merged size): %s', n_parts, merged_size // 1024 // 1024, stream)
            return stream, n_parts, merged_stream_path

    raise YouTubeError('no suitable video stream found for %ss video length', yt.length)


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


def check_download_adaptive(yt: YouTube, output_path: str, min_res: int = 360) -> tuple[Stream, list[Path]]:
    output_path = Path(output_path)

    # pick one that fits best
    video_stream, n_parts, video_path = pick_stream(yt, output_path, min_res)

    video_paths = []
    while True:
        # split the merged file into more and more parts, until every part is < 50Mb
        if video_paths:
            # clean up old split parts
            for file in video_paths:
                file: Path
                file.unlink()

        if n_parts == 1:
            video_paths = [video_path]
        else:
            video_paths = split_video(
                duration_seconds=yt.length,
                input_path=video_path,
                output_dir=output_path,
                n_parts=n_parts
            )

        for file in video_paths:
            log.info('%s size: %dMb', file.name, file.stat().st_size // 1024 // 1024)

        # the second size check is after split
        too_big_files = [file for file in video_paths if file.stat().st_size > MAX_FILE_SIZE_BYTES]
        if too_big_files:
            if n_parts == 10:
                raise YouTubeError("The video is too big and already split for 10 parts.")
            n_parts += 1
            log.info("video part size is too big, splitting for %d parts", n_parts)
            continue

        return video_stream, video_paths
