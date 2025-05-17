import logging
import subprocess
from pathlib import Path

import whisper
from pydub import AudioSegment
from pytubefix import YouTube

from .enum import SourceLang
from .schema import YouTubeVideoData


def maybe_translate_audio(
        video: YouTubeVideoData,
        output_dir: str,
        source_audio_path: str,
) -> Path | None:
    video.source_lang = detect_source_lang(source_audio_path)
    if not video.source_lang or video.source_lang == SourceLang.MISSING:
        return None

    if video.source_lang.value == video.target_lang.value:
        log.info('original lang is the same as target, skipping translation')
        return None

    translated_audio_path = translate_audio(video.yt, output_dir, str(video.target_lang))
    if not translated_audio_path:
        return None

    # the video successfully translated to a target language
    video.translated_lang = video.target_lang
    result_audio_path = Path(output_dir) / f"{video.yt.video_id}.translated.{video.target_lang}.mixed.mp3"

    # mix original and translated audios
    mix_audio(source_audio_path, str(translated_audio_path), str(result_audio_path))
    log.info('original and translated files are mixed into %s', result_audio_path)

    return result_audio_path


log = logging.getLogger(__name__)


def detect_source_lang(audio_path: str) -> SourceLang | None:
    model = whisper.load_model("tiny")

    # Load and process the audio file
    audio = whisper.load_audio(audio_path)
    audio = whisper.pad_or_trim(audio)
    mel = whisper.log_mel_spectrogram(audio).to(model.device)

    # Detect language from the audio
    tensor, lang_probs = model.detect_language(mel)
    code, prob = sorted(lang_probs.items(), key=lambda x: x[1], reverse=True)[0]

    if prob < 0.1:
        # Very low confidence, likely music
        log.info('%s lang confidence is %d, assuming music', code, prob)
        return SourceLang.MISSING

    if code not in SourceLang:
        log.info('%s detected but is not supported', code)
        return None

    lang = SourceLang(code)
    log.info('%s detected with %.3f confidence', lang, prob)
    return lang


def translate_audio(yt: YouTube, output_dir: str, target_lang: str) -> Path | None:
    output_file = Path(f"{yt.video_id}.translated.{target_lang}.mp3")
    command = [
        'vot-cli',
        f'--output={output_dir}',
        f'--output-file={output_file}',
        f'--reslang={target_lang}',
        yt.watch_url,
    ]
    try:
        # set a hard limit for a command (in case it hangs for some reason)
        subprocess.run(
            command,
            check=True,
            # stdout=subprocess.DEVNULL,
            # stderr=subprocess.DEVNULL,
            timeout=120
        )
    except subprocess.CalledProcessError:
        # failed to translate
        log.info('failed to translate %s to %s', yt.video_id, target_lang)
        return None
    except subprocess.TimeoutExpired:
        # failed to translate in 2 minutes
        log.info('failed to translate %s to %s', yt.video_id, target_lang)
        return None

    output_path = Path(output_dir) / output_file
    if not output_path.exists():
        log.info('failed to translate %s to %s, no audio file found after calling the subprocess', yt.video_id, target_lang)
        return None

    log.info('translated audio downloaded to %s', output_file)
    return output_path


def mix_audio(
    original_audio_path: str,
    translated_audio_path: str,
    output_path: str,
    original_volume_db=-10,
) -> None:
    """
    Mixes two audio files: the original and the translated, with the original being quieter.

    :param original_audio_path: Path to the original audio file.
    :param translated_audio_path: Path to the translated audio file.
    :param output_path: Path to save the output mixed audio.
    :param original_volume_db: Volume reduction for the original audio (in dB). Default is -10 dB.
    """
    log.info('mixing %s and %s', original_audio_path, translated_audio_path)

    # Load both audio files
    original_audio = AudioSegment.from_file(original_audio_path)
    translated_audio = AudioSegment.from_file(translated_audio_path)

    # Reduce the volume of the original audio
    original_audio = original_audio + original_volume_db  # Reduces volume in dB (e.g., -10 dB to make it quieter)

    # Make sure both audio files are the same length, trimming or padding as necessary
    if len(original_audio) > len(translated_audio):
        translated_audio = translated_audio + AudioSegment.silent(duration=len(original_audio) - len(translated_audio))
    elif len(translated_audio) > len(original_audio):
        original_audio = original_audio + AudioSegment.silent(duration=len(translated_audio) - len(original_audio))

    # Mix both audio files
    mixed_audio = original_audio.overlay(translated_audio)

    # Export the mixed audio to a file
    mixed_audio.export(output_path, format="mp3")

    #
    # Go with pydub because it makes a louder sound overall
    #

    # command = [
    #     'ffmpeg',
    #     '-i', original_audio_path,
    #     '-i', translated_audio_path,
    #     '-filter_complex', f'[0:a]volume=0.3[a0];[a0][1:a]amix=inputs=2:duration=longest[aout]',
    #     '-map', '[aout]',
    #     '-c:a', 'libmp3lame',
    #     '-q:a', '4',
    #     str(output_path)
    # ]
    #
    # subprocess.run(
    #     command,
    #     check=True,
    #     stdout=subprocess.DEVNULL,
    #     stderr=subprocess.DEVNULL,
    # )
