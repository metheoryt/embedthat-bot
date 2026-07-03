# Audio-Only Platform Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** let the bot download and deliver audio from any yt-dlp-supported audio-only
source (SoundCloud, Bandcamp, Mixcloud, etc.) — single tracks send immediately, albums/
playlists paginate 10 tracks per page with Back/Next buttons — without hardcoding a
platform list.

**Architecture:** the existing catch-all "social" link pipeline (`bot/worker/actors.py::
process_social_link` → `bot/worker/pipeline.py::handle_social_video`) gains a
classification step: probe the link with yt-dlp, and if it has no video-capable format,
route to a new parallel `bot/util/audio/` module instead of the video path. A new
`AudioRequestData` model holds the full (capped) track index; a new `process_audio_page`
actor + `apg:` callback handle Back/Next taps, reusing the existing waiter/lock/dump-chat
machinery already proven by the YouTube and social pipelines.

**Tech Stack:** Python 3.12, yt-dlp (already a dependency, `>=2026.3.17`), aiogram 3,
dramatiq, redis, pydantic.

## Global Constraints

- This repo has **no automated test suite** (`pyproject.toml` has no `pytest`/test
  dependency; `CLAUDE.md` states this explicitly) — only `pyright` for static type
  checking. Every task below substitutes real verification for unit tests: a `pyright`
  pass, plus a scratch `uv run python` script (not committed — this repo has no test
  directory to put it in) that exercises the real code against real, small, publicly
  reachable yt-dlp test fixtures. The exact URLs and their **confirmed real output** are
  given in each task — this was verified against the actual installed `yt-dlp==2026.06.09`
  during planning, not guessed.
- Verification scripts require outbound internet access (they hit soundcloud.com /
  bandcamp.com — the same public test URLs yt-dlp's own test suite uses).
- Every new/modified file must pass `uv run pyright` with no new errors before its task is
  considered done.
- Match existing code conventions exactly: pydantic `BaseModel` schemas with a
  `cache_key` property, `reply_to`/`send_to_chat` method pairs on data models (see
  `bot/util/social/schema.py`, `bot/util/youtube/schema.py`), dramatiq actors declared with
  `max_retries=2`, `min_backoff=30_000`, `max_backoff=5*60_000`,
  `on_retry_exhausted="report_actor_failure"`.
- Spec reference: `docs/superpowers/specs/2026-07-03-audio-only-platforms-design.md`.

---

## Task 1: Config field for the playlist track cap

**Files:**
- Modify: `bot/config.py:24` (insert after `max_video_resolution: int = 480`)

**Interfaces:**
- Produces: `settings.max_playlist_tracks: int` (default `200`), read by Task 3's
  `probe_link` to cap how many playlist entries get indexed.

- [ ] **Step 1: Add the field**

Edit `bot/config.py`, inserting a new line immediately after `max_video_resolution: int = 480`:

```python
    max_video_resolution: int = 480
    max_playlist_tracks: int = 200
```

- [ ] **Step 2: Verify it loads**

Run: `uv run python -c "from bot.config import settings; print(settings.max_playlist_tracks)"`
Expected output: `200`

- [ ] **Step 3: Commit**

```bash
git add bot/config.py
git commit -m "Add max_playlist_tracks config for audio playlist cap"
```

---

## Task 2: Audio data model (`bot/util/audio/`)

**Files:**
- Create: `bot/util/audio/__init__.py` (empty, matches `bot/util/social/__init__.py` /
  `bot/util/youtube/__init__.py`)
- Create: `bot/util/audio/exc.py`
- Create: `bot/util/audio/schema.py`

**Interfaces:**
- Produces:
  - `AudioTrackData` (pydantic model): fields `extractor: str`, `id: str`,
    `webpage_url: str`, `title: str | None`, `uploader: str | None`,
    `duration: int | None`, `file_id: str | None`. Property `cache_key -> str`
    (`au:{extractor}:{id}`). Property `as_input_media -> types.InputMediaAudio`.
  - `AudioRequestData` (pydantic model): fields `link: str`, `tracks: list[AudioTrackData]`.
    Property `hash16 -> str`, `cache_key -> str` (`da:{hash16}`),
    `total_pages -> int`. Method `page(page: int) -> list[AudioTrackData]` (1-indexed,
    10 tracks/page — returns a **slice sharing the same `AudioTrackData` instances**, so
    mutating a track via `.page(n)` mutates `self.tracks` too). Method
    `pager_markup(page: int) -> types.InlineKeyboardMarkup | None`. Async methods
    `send_to_chat(bot, chat_id, reply_to_message_id=None, page=1)` and
    `reply_to(message, page=1)`, mirroring `SocialVideoData`'s convention.
  - `AudioDownloadError(Exception)`.
- Consumes: nothing (pure data layer, no I/O).

- [ ] **Step 1: Create `bot/util/audio/__init__.py`**

Empty file.

- [ ] **Step 2: Create `bot/util/audio/exc.py`**

```python
class AudioDownloadError(Exception):
    pass
```

- [ ] **Step 3: Create `bot/util/audio/schema.py`**

```python
import hashlib
import math

from aiogram import types, Bot
from pydantic import BaseModel, Field

PAGE_SIZE = 10  # Telegram's InputMediaAudio media-group cap


class AudioTrackData(BaseModel):
    extractor: str
    id: str
    webpage_url: str
    title: str | None = None
    uploader: str | None = None
    duration: int | None = None
    file_id: str | None = None

    @property
    def cache_key(self) -> str:
        return f"au:{self.extractor}:{self.id}"

    @property
    def as_input_media(self) -> types.InputMediaAudio:
        return types.InputMediaAudio(
            media=self.file_id,
            title=self.title,
            performer=self.uploader,
            duration=self.duration,
        )


class AudioRequestData(BaseModel):
    link: str
    tracks: list[AudioTrackData] = Field(default_factory=list)

    @property
    def hash16(self) -> str:
        return hashlib.sha256(self.link.encode()).hexdigest()[:16]

    @property
    def cache_key(self) -> str:
        return f"da:{self.hash16}"

    @property
    def total_pages(self) -> int:
        return math.ceil(len(self.tracks) / PAGE_SIZE) if self.tracks else 0

    def page(self, page: int) -> list[AudioTrackData]:
        start = (page - 1) * PAGE_SIZE
        return self.tracks[start:start + PAGE_SIZE]

    def pager_markup(self, page: int) -> types.InlineKeyboardMarkup | None:
        buttons = []
        if page > 1:
            buttons.append(
                types.InlineKeyboardButton(text="◀️ Back", callback_data=f"apg:{self.hash16}:{page - 1}")
            )
        if page < self.total_pages:
            buttons.append(
                types.InlineKeyboardButton(text="Next ▶️", callback_data=f"apg:{self.hash16}:{page + 1}")
            )
        if not buttons:
            return None
        return types.InlineKeyboardMarkup(inline_keyboard=[buttons])

    def _pager_text(self, page: int, skipped: int) -> str:
        text = f"Page {page}/{self.total_pages}"
        if skipped:
            text += f" (⚠️ {skipped} unavailable)"
        return text

    async def send_to_chat(self, bot: Bot, chat_id: int, reply_to_message_id: int | None = None, page: int = 1) -> None:
        page_tracks = self.page(page)
        deliverable = [t for t in page_tracks if t.file_id]
        if not deliverable:
            await bot.send_message(
                chat_id, "❌ No tracks on this page could be downloaded.", reply_to_message_id=reply_to_message_id
            )
            return

        if len(deliverable) == 1:
            t = deliverable[0]
            await bot.send_audio(
                chat_id, t.file_id, performer=t.uploader, title=t.title, duration=t.duration,
                reply_to_message_id=reply_to_message_id,
            )
        else:
            await bot.send_media_group(
                chat_id, [t.as_input_media for t in deliverable], reply_to_message_id=reply_to_message_id
            )

        skipped = len(page_tracks) - len(deliverable)
        markup = self.pager_markup(page)
        if markup or skipped:
            await bot.send_message(chat_id, self._pager_text(page, skipped), reply_markup=markup)

    async def reply_to(self, message: types.Message, page: int = 1) -> None:
        page_tracks = self.page(page)
        deliverable = [t for t in page_tracks if t.file_id]
        if not deliverable:
            await message.reply("❌ No tracks on this page could be downloaded.")
            return

        if len(deliverable) == 1:
            t = deliverable[0]
            await message.reply_audio(t.file_id, performer=t.uploader, title=t.title, duration=t.duration)
        else:
            await message.reply_media_group([t.as_input_media for t in deliverable])

        skipped = len(page_tracks) - len(deliverable)
        markup = self.pager_markup(page)
        if markup or skipped:
            await message.reply(self._pager_text(page, skipped), reply_markup=markup)
```

- [ ] **Step 4: Verify with a real script (no network needed)**

Run:

```bash
uv run python -c "
from bot.util.audio.schema import AudioRequestData, AudioTrackData

tracks = [
    AudioTrackData(extractor='Bandcamp', id=str(i), webpage_url=f'https://x/{i}', title=f'Track {i}', file_id=f'FID{i}')
    for i in range(22)
]
req = AudioRequestData(link='https://example.com/album', tracks=tracks)
assert req.total_pages == 3, req.total_pages
assert len(req.page(1)) == 10
assert len(req.page(3)) == 2
assert req.page(1)[0].id == '0'
assert req.page(3)[0].id == '20'

m1 = req.pager_markup(1)
assert m1 is not None and [b.text for b in m1.inline_keyboard[0]] == ['Next ▶️']
m2 = req.pager_markup(2)
assert [b.text for b in m2.inline_keyboard[0]] == ['◀️ Back', 'Next ▶️']
m3 = req.pager_markup(3)
assert [b.text for b in m3.inline_keyboard[0]] == ['◀️ Back']

# mutating a track via .page() must mutate req.tracks too (aliasing, not a copy)
req.page(1)[0].file_id = 'CHANGED'
assert req.tracks[0].file_id == 'CHANGED'

print('OK')
"
```

Expected output: `OK`

- [ ] **Step 5: Typecheck**

Run: `uv run pyright bot/util/audio`
Expected: `0 errors`

- [ ] **Step 6: Commit**

```bash
git add bot/util/audio/__init__.py bot/util/audio/exc.py bot/util/audio/schema.py
git commit -m "Add AudioTrackData/AudioRequestData data model for audio-only downloads"
```

---

## Task 3: Download logic (`bot/util/audio/download.py`)

**Files:**
- Create: `bot/util/audio/download.py`

**Interfaces:**
- Consumes: `AudioTrackData`, `AudioDownloadError` (Task 2); `settings.max_playlist_tracks`
  (Task 1); `bot.util.youtube.video.MAX_FILE_SIZE_BYTES` (existing, `50 * 1024 * 1024`,
  already imported this way by `bot/worker/pipeline.py`).
- Produces:
  - `probe_link(url: str) -> tuple[bool, list[AudioTrackData]]` — classifies the link;
    returns `(False, [])` if it's not audio-only (caller falls back to the video
    pipeline), else `(True, tracks)` with the capped track index. Blocking (does real
    network I/O) — callers must wrap in `asyncio.to_thread`.
  - `download_track(track: AudioTrackData, output_dir: Path) -> Path` — downloads one
    track for real, backfills `track.title`/`track.uploader`/`track.duration` if not
    already set, and returns the downloaded file's path. Raises `AudioDownloadError` on
    yt-dlp failure or if the file exceeds `MAX_FILE_SIZE_BYTES`. Blocking.

**Key facts verified during planning (yt-dlp `2026.06.09`, do not re-derive, they are
counter-intuitive):**
- A **single-item URL** (not a playlist) is *always* fully resolved by
  `extract_info(url, download=False)` regardless of `extract_flat` — `info['formats']` is
  present. Verified against `http://soundcloud.com/ethmusic/lostin-powers-she-so-heavy`
  and `http://youtube-dl.bandcamp.com/track/youtube-dl-test-song`.
- A **playlist URL** with `extract_flat='in_playlist'` returns `info['_type'] ==
  'playlist'` and `info['entries']` as a **shallow** iterator — each entry has `id`,
  `url` (the track's own webpage URL — SoundCloud entries have `webpage_url=None`, use
  `url`), `ie_key` (extractor name), and *sometimes* `title` (Bandcamp has it, SoundCloud
  doesn't) — but **never** `formats`/`vcodec`/`duration`. Verified against
  `https://soundcloud.com/the-concept-band/sets/the-royal-concept-ep` (6 entries) and
  `http://blazo.bandcamp.com/album/jazz-format-mixtape-vol-1` (22 entries).
- Therefore classifying a playlist requires a **second, deep** `extract_info` call on
  just the first entry's URL (no `extract_flat`) to inspect its `formats`.
- After a real download, `info['requested_downloads'][0]['filepath']` is the reliable
  absolute path to the downloaded file (verified: downloading
  `http://youtube-dl.bandcamp.com/track/youtube-dl-test-song` produced
  `{...,"filepath": ".../Bandcamp_1812978515.mp3"}`).

- [ ] **Step 1: Create `bot/util/audio/download.py`**

```python
import itertools
import logging
from pathlib import Path

import yt_dlp

from bot.config import settings
from bot.util.youtube.video import MAX_FILE_SIZE_BYTES

from .exc import AudioDownloadError
from .schema import AudioTrackData

log = logging.getLogger(__name__)


def _is_audio_only(info: dict) -> bool:
    formats = info.get("formats") or [info]
    return not any(f.get("vcodec") not in (None, "none") for f in formats)


def _deep_probe(url: str) -> dict:
    opts = {"quiet": True, "skip_download": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            return ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as e:
            raise AudioDownloadError(str(e)) from e


def probe_link(url: str) -> tuple[bool, list[AudioTrackData]]:
    """
    Classifies `url` as audio-only or not, and builds its (capped) track index.

    Synchronous/blocking -- call via asyncio.to_thread.
    """
    opts = {"quiet": True, "skip_download": True, "extract_flat": "in_playlist", "noplaylist": False}
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as e:
            raise AudioDownloadError(str(e)) from e

    if info.get("_type") == "playlist" or "entries" in info:
        entries = list(itertools.islice(info["entries"], settings.max_playlist_tracks))
        if not entries:
            raise AudioDownloadError("Playlist has no tracks")

        first_url = entries[0].get("url") or entries[0].get("webpage_url")
        if not _is_audio_only(_deep_probe(first_url)):
            return False, []

        tracks = [
            AudioTrackData(
                extractor=e.get("ie_key") or info.get("extractor_key") or "unknown",
                id=str(e["id"]),
                webpage_url=e.get("url") or e.get("webpage_url"),
                title=e.get("title"),
                uploader=e.get("uploader"),
                duration=int(e["duration"]) if e.get("duration") else None,
            )
            for e in entries
        ]
        log.info("classified %s as audio playlist, %d tracks", url, len(tracks))
        return True, tracks

    if not _is_audio_only(info):
        return False, []

    track = AudioTrackData(
        extractor=info.get("extractor_key") or "unknown",
        id=str(info["id"]),
        webpage_url=info.get("webpage_url") or url,
        title=info.get("title"),
        uploader=info.get("uploader"),
        duration=int(info["duration"]) if info.get("duration") else None,
    )
    log.info("classified %s as a single audio track", url)
    return True, [track]


def download_track(track: AudioTrackData, output_dir: Path) -> Path:
    """Synchronous/blocking -- call via asyncio.to_thread."""
    ydl_opts = {
        "outtmpl": str(output_dir / f"{track.extractor}_{track.id}.%(ext)s"),
        "format": "bestaudio/best",
        "quiet": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(track.webpage_url, download=True)
        except yt_dlp.utils.DownloadError as e:
            raise AudioDownloadError(str(e)) from e

    track.title = track.title or info.get("title") or ""
    track.uploader = track.uploader or info.get("uploader") or ""
    track.duration = track.duration or (int(info["duration"]) if info.get("duration") else None)

    file_path = Path(info["requested_downloads"][0]["filepath"])
    if file_path.stat().st_size > MAX_FILE_SIZE_BYTES:
        file_path.unlink(missing_ok=True)
        raise AudioDownloadError(f"{track.title or track.webpage_url} is too large to send (over 50MB)")

    log.info("downloaded track %s -> %s", track.webpage_url, file_path)
    return file_path
```

- [ ] **Step 2: Verify classification + index building with real network calls**

Run:

```bash
uv run python -c "
from bot.util.audio.download import probe_link

is_audio, tracks = probe_link('http://youtube-dl.bandcamp.com/track/youtube-dl-test-song')
assert is_audio is True
assert len(tracks) == 1
assert tracks[0].extractor == 'Bandcamp'
assert tracks[0].duration == 9
print('single track OK:', tracks[0].title)

is_audio, tracks = probe_link('http://blazo.bandcamp.com/album/jazz-format-mixtape-vol-1')
assert is_audio is True
assert len(tracks) == 22, len(tracks)
assert tracks[0].title == 'Intro'
print('playlist OK:', len(tracks), 'tracks')

is_audio, tracks = probe_link('https://soundcloud.com/the-concept-band/sets/the-royal-concept-ep')
assert is_audio is True
assert len(tracks) == 6, len(tracks)
print('soundcloud set OK:', len(tracks), 'tracks')
"
```

Expected output (durations/counts may drift slightly if the fixtures change upstream, but
`is_audio is True` and track counts in this ballpark must hold):

```
single track OK: youtube-dl "'/ä↭ - youtube-dl "'/ä↭ - youtube-dl test song "'/ä↭
playlist OK: 22 tracks
soundcloud set OK: 6 tracks
```

- [ ] **Step 3: Verify the playlist cap actually caps**

Run:

```bash
uv run python -c "
from unittest.mock import patch
from bot.config import settings
from bot.util.audio.download import probe_link

with patch.object(settings, 'max_playlist_tracks', 5):
    is_audio, tracks = probe_link('http://blazo.bandcamp.com/album/jazz-format-mixtape-vol-1')
    assert is_audio is True
    assert len(tracks) == 5, len(tracks)
    print('cap OK')
"
```

Expected output: `cap OK`

- [ ] **Step 4: Verify a real download end-to-end (tiny 9-second fixture)**

Run:

```bash
uv run python -c "
import tempfile
from pathlib import Path
from bot.util.audio.download import probe_link, download_track

is_audio, tracks = probe_link('http://youtube-dl.bandcamp.com/track/youtube-dl-test-song')
track = tracks[0]
with tempfile.TemporaryDirectory() as tmp:
    path = download_track(track, Path(tmp))
    assert path.exists()
    assert path.stat().st_size > 0
    print('downloaded', path.stat().st_size, 'bytes, duration=', track.duration)
"
```

Expected output: `downloaded <nonzero> bytes, duration= 9`

- [ ] **Step 5: Verify a non-audio link classifies as not-audio**

Run against a real YouTube video (any extractor with video-capable formats proves the
classifier correctly says "not audio" — this isn't testing YouTube-specific behavior):

```bash
uv run python -c "
from bot.util.audio.download import probe_link

is_audio, tracks = probe_link('https://www.youtube.com/watch?v=BaW_jenozKc')
assert is_audio is False
assert tracks == []
print('OK not audio')
"
```

Expected output: `OK not audio`

(This URL is only used here to prove the classifier correctly says "not audio" for a
video — production traffic never reaches `probe_link` for youtube.com links, since
`bot/handlers.py`'s YouTube regex intercepts those first.)

- [ ] **Step 6: Typecheck**

Run: `uv run pyright bot/util/audio`
Expected: `0 errors`

- [ ] **Step 7: Commit**

```bash
git add bot/util/audio/download.py
git commit -m "Add yt-dlp-based audio classification and per-track download logic"
```

---

## Task 4: Pipeline orchestration (`bot/worker/pipeline.py`)

**Files:**
- Modify: `bot/worker/pipeline.py` (add after `handle_social_video`, i.e. after line 131)

**Interfaces:**
- Consumes: `AudioTrackData` (Task 2), `download_track` (Task 3),
  `settings.dump_chat_id` (existing).
- Produces: `handle_audio_page(bot: Bot, tracks: list[AudioTrackData]) -> int` — downloads
  and dump-chat-uploads every track in `tracks` that doesn't already have a `file_id`
  (mutating each `AudioTrackData` in place: sets `file_id`, and `download_track` also
  backfills `title`/`uploader`/`duration`). Returns the count of tracks that failed and
  were skipped (does **not** raise on a single track's failure — only propagates
  `TelegramNetworkError` if the final upload retry is exhausted, matching
  `_upload_parts_to_dump_chat`'s existing behavior). Callers are responsible for the
  per-track Redis dedup cache lookup **before** calling this (so already-cached tracks are
  never passed in with an empty `file_id`) — see Task 5.

- [ ] **Step 1: Add imports**

Modify `bot/worker/pipeline.py`'s import block (top of file) to add:

```python
from bot.util.audio.download import download_track
from bot.util.audio.schema import AudioTrackData
```

- [ ] **Step 2: Add `handle_audio_page`**

Insert after `handle_social_video` (after line 131, the end of that function):

```python


async def handle_audio_page(bot: Bot, tracks: list[AudioTrackData]) -> int:
    """
    Downloads and dump-chat-uploads every track in `tracks` missing a file_id,
    mutating each in place. Returns how many tracks failed and were skipped --
    one bad track (geo-blocked/removed) shouldn't take down the whole page.
    """
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for track in tracks:
            if track.file_id:
                continue

            file_path = None
            exc = None
            for i in range(3):
                try:
                    file_path = await asyncio.to_thread(download_track, track, tmp_path)
                    exc = None
                    break
                except AudioDownloadError as ex:
                    exc = ex
                    break  # unrecoverable for this track -- don't retry
                except Exception as ex:
                    exc = ex
                    log.error("failed to download track %s on try #%d: %r", track.webpage_url, i + 1, exc)
                    await asyncio.sleep(2)

            if exc or file_path is None:
                log.error("giving up on track %s: %r", track.webpage_url, exc)
                failed += 1
                continue

            media_message = None
            for i in range(3):
                try:
                    media_message = await bot.send_audio(
                        settings.dump_chat_id,
                        types.FSInputFile(file_path),
                        performer=track.uploader,
                        title=track.title,
                        duration=track.duration,
                    )
                    break
                except TelegramNetworkError:
                    if i == 2:
                        raise
                    log.warning('failed to send an audio track, retrying in 2 seconds')
                    await asyncio.sleep(2)

            track.file_id = media_message.audio.file_id
            log.info("uploaded track %s -> %s", track.webpage_url, track.file_id)

    return failed
```

Add the `AudioDownloadError` import too:

```python
from bot.util.audio.exc import AudioDownloadError
```

- [ ] **Step 3: Typecheck**

Run: `uv run pyright bot/worker/pipeline.py`
Expected: `0 errors`

- [ ] **Step 4: Verify with a real end-to-end call (requires a working `.env` with `BOT_TOKEN` and `DUMP_CHAT_ID`)**

Run:

```bash
uv run python -c "
import asyncio
from aiogram import Bot
from bot.config import settings
from bot.util.audio.download import probe_link
from bot.worker.pipeline import handle_audio_page

async def main():
    is_audio, tracks = probe_link('http://youtube-dl.bandcamp.com/track/youtube-dl-test-song')
    bot = Bot(token=settings.bot_token)
    try:
        failed = await handle_audio_page(bot, tracks)
        assert failed == 0, failed
        assert tracks[0].file_id
        print('uploaded, file_id =', tracks[0].file_id)
    finally:
        await bot.session.close()

asyncio.run(main())
"
```

Expected output: `uploaded, file_id = <a Telegram file_id string>`. Check your configured
`DUMP_CHAT_ID` chat to confirm the tiny test-song audio arrived.

- [ ] **Step 5: Commit**

```bash
git add bot/worker/pipeline.py
git commit -m "Add handle_audio_page pipeline orchestration for audio downloads"
```

---

## Task 5: Wire classification into the worker + add the page-2+ actor

**Files:**
- Modify: `bot/worker/actors.py`
- Modify: `bot/worker/error_reporting.py:13`

**Interfaces:**
- Consumes: `AudioRequestData`, `AudioTrackData` (Task 2), `probe_link`,
  `AudioDownloadError` (Task 3), `handle_audio_page` (Task 4).
- Produces:
  - `process_audio_page(chat_id: int, hash16: str, page: int)` — new dramatiq actor,
    called by the `apg:` callback handler (Task 6) when a requested page isn't fully
    cached yet.
  - `_process_social_link_async` (existing, modified) now classifies before downloading:
    audio links never reach `handle_social_video`.

- [ ] **Step 1: Add imports to `bot/worker/actors.py`**

Add to the import block:

```python
from bot.util.audio.exc import AudioDownloadError
from bot.util.audio.schema import AudioRequestData, AudioTrackData
from bot.util.audio.download import probe_link
from bot.worker.pipeline import handle_audio_page
```

(`handle_audio_page` joins the existing `from bot.worker.pipeline import
handle_social_video, handle_youtube_video` line -- combine into one import.)

- [ ] **Step 2: Add cache-dedup helpers**

Insert after `_notify_audio_waiters_success` (after line 83, before
`_process_youtube_link_async`):

```python


async def _resolve_cached_tracks(redis_client: redis.Redis, tracks: list[AudioTrackData]) -> None:
    """Fills in file_id/title/uploader/duration for any track already present in the
    per-track dedup cache, so handle_audio_page only downloads genuine misses."""
    for track in tracks:
        if track.file_id:
            continue
        cached_raw = await redis_client.get(track.cache_key)
        if not cached_raw:
            continue
        cached = AudioTrackData.model_validate_json(cached_raw)
        track.file_id = cached.file_id
        track.title = track.title or cached.title
        track.uploader = track.uploader or cached.uploader
        track.duration = track.duration or cached.duration


async def _save_tracks_to_cache(redis_client: redis.Redis, tracks: list[AudioTrackData]) -> None:
    for track in tracks:
        if track.file_id:
            await redis_client.set(track.cache_key, track.model_dump_json())


async def _notify_audio_page_waiters_success(bot: Bot, waiters: list[Waiter], audio: AudioRequestData, page: int) -> None:
    for waiter in waiters:
        await audio.send_to_chat(bot, waiter.chat_id, page=page)
```

- [ ] **Step 3: Modify `_process_social_link_async`**

Replace the whole existing decorated function (lines 198-221, from `@with_chat_action()`
through the closing `await redis_client.aclose()`):

```python
@with_chat_action()
async def _process_social_link_async(bot: Bot, chat_id: int, url: str) -> None:
    redis_client = redis.from_url(str(settings.redis_dsn), decode_responses=True)
    try:
        video = SocialVideoData.model_validate(dict(link=url))

        lock = Lock(redis_client, f'{video.cache_key}:lock', timeout=20 * 60, blocking_timeout=21 * 60)
        async with HeartbeatLock(lock):
            try:
                is_audio, tracks = await asyncio.to_thread(probe_link, url)
            except AudioDownloadError as e:
                waiters = await _pop_waiters(redis_client, video.cache_key)
                await _notify_waiters_failure(bot, waiters, f"❌ Couldn't process this link: {e}")
                raise

            if is_audio:
                audio = AudioRequestData(link=url, tracks=tracks)
                page_tracks = audio.page(1)
                await _resolve_cached_tracks(redis_client, page_tracks)
                failed = await handle_audio_page(bot, page_tracks)
                await _save_tracks_to_cache(redis_client, page_tracks)
                await redis_client.set(audio.cache_key, audio.model_dump_json())
                log.info(
                    "cached %s (%d tracks total, page 1 ready, %d failed)",
                    audio.cache_key, len(audio.tracks), failed,
                )

                waiters = await _pop_waiters(redis_client, video.cache_key)
                await _notify_waiters_success(bot, waiters, audio)
                return

            try:
                video = await handle_social_video(bot, video)
            except SocialDownloadError as e:
                waiters = await _pop_waiters(redis_client, video.cache_key)
                await _notify_waiters_failure(bot, waiters, f"❌ Couldn't download this video: {e}")
                raise

            await redis_client.set(video.cache_key, video.model_dump_json())
            log.info("cached %s (%s)", video.cache_key, video.origin)

            waiters = await _pop_waiters(redis_client, video.cache_key)
            await _notify_waiters_success(bot, waiters, video)
            for waiter in waiters:
                await on_social_video_sent.send(url, waiter.chat_id, waiter.chat_type, bot, video, True)
    finally:
        await redis_client.aclose()
```

- [ ] **Step 4: Add the `process_audio_page` actor**

Insert after `process_social_link` (end of file, after line 237):

```python


@with_chat_action(ChatAction.UPLOAD_VOICE)
async def _process_audio_page_async(bot: Bot, chat_id: int, hash16: str, page: int) -> None:
    redis_client = redis.from_url(str(settings.redis_dsn), decode_responses=True)
    try:
        cache_key = f"da:{hash16}"
        page_key = f"{cache_key}:page:{page}"

        audio_raw = await redis_client.get(cache_key)
        if not audio_raw:
            log.error("cache entry %s vanished before page %d could be processed", cache_key, page)
            waiters = await _pop_waiters(redis_client, page_key)
            await _notify_waiters_failure(bot, waiters, "❌ This playlist is no longer cached, please resend the link.")
            return

        audio = AudioRequestData.model_validate_json(audio_raw)

        lock = Lock(redis_client, f'{page_key}:lock', timeout=20 * 60, blocking_timeout=21 * 60)
        async with HeartbeatLock(lock):
            page_tracks = audio.page(page)
            await _resolve_cached_tracks(redis_client, page_tracks)
            failed = await handle_audio_page(bot, page_tracks)
            await _save_tracks_to_cache(redis_client, page_tracks)
            await redis_client.set(cache_key, audio.model_dump_json())
            log.info("cached %s page %d (%d failed)", cache_key, page, failed)

            waiters = await _pop_waiters(redis_client, page_key)
            await _notify_audio_page_waiters_success(bot, waiters, audio, page)
    finally:
        await redis_client.aclose()


@dramatiq.actor(
    max_retries=2,
    min_backoff=30_000,
    max_backoff=5 * 60_000,
    time_limit=30 * 60_000,
    throws=(AudioDownloadError,),
    on_retry_exhausted="report_actor_failure",
)
def process_audio_page(chat_id: int, hash16: str, page: int):
    bot = Bot(token=settings.bot_token)
    try:
        asyncio.run(_process_audio_page_async(bot, chat_id, hash16, page))
    finally:
        asyncio.run(bot.session.close())
```

- [ ] **Step 5: Update `bot/worker/error_reporting.py`'s actor list**

`process_audio_page`'s second positional arg is `hash16`, not a raw link, but it's still
useful correlation info for failure alerts. Modify line 13:

```python
_LINK_ARG_ACTORS = ("process_youtube_link", "process_social_link", "process_audio_page")
```

And update the comment above it (lines 11-12) to:

```python
# the link (or, for process_audio_page, the link's cache hash) is always the second
# positional arg for these actors: process_youtube_link(chat_id, link, target_lang),
# process_social_link(chat_id, url), process_audio_page(chat_id, hash16, page)
```

- [ ] **Step 6: Typecheck**

Run: `uv run pyright bot/worker`
Expected: `0 errors`

- [ ] **Step 7: Verify the classification branch end-to-end (requires working `.env`)**

This exercises the exact code path a real Telegram message would trigger, without needing
a live bot connection -- it calls the private async function directly.

```bash
uv run python -c "
import asyncio
from aiogram import Bot
from bot.config import settings
from bot.worker.actors import _process_social_link_async

async def main():
    bot = Bot(token=settings.bot_token)
    try:
        # a real chat_id isn't needed for this check -- no waiters are registered,
        # so _notify_waiters_success has nothing to iterate and just returns.
        await _process_social_link_async(bot, settings.dump_chat_id, 'http://youtube-dl.bandcamp.com/track/youtube-dl-test-song')
    finally:
        await bot.session.close()

asyncio.run(main())

import redis
r = redis.from_url(str(settings.redis_dsn), decode_responses=True)
from bot.util.audio.schema import AudioRequestData
raw = r.get('da:' + AudioRequestData(link='http://youtube-dl.bandcamp.com/track/youtube-dl-test-song').hash16)
assert raw, 'expected da: cache entry to be set'
audio = AudioRequestData.model_validate_json(raw)
assert audio.tracks[0].file_id
print('OK, cached with file_id', audio.tracks[0].file_id)
"
```

Expected output: `OK, cached with file_id <id>`

- [ ] **Step 8: Commit**

```bash
git add bot/worker/actors.py bot/worker/error_reporting.py
git commit -m "Classify social links as audio vs video; add process_audio_page actor"
```

---

## Task 6: Handler wiring (cache-hit check + Back/Next callback)

**Files:**
- Modify: `bot/handlers.py`

**Interfaces:**
- Consumes: `AudioRequestData` (Task 2), `process_audio_page` (Task 5).
- Produces: user-facing behavior only (no new interfaces consumed by later tasks).

- [ ] **Step 1: Add imports**

Add to the import block at the top of `bot/handlers.py`:

```python
from .util.audio.schema import AudioRequestData
from .worker.actors import process_audio_page
```

(`process_audio_page` joins the existing `from .worker.actors import
process_social_link, process_youtube_audio, process_youtube_link` line.)

- [ ] **Step 2: Add the audio cache-hit check to `_process_social_url`**

Replace lines 135-136 (the function signature, and the first existing body line
`video = SocialVideoData.model_validate(dict(link=url))`) by inserting the audio cache-hit
check between them. Everything else in the function (the video cache-hit block, waiter
registration, `process_social_link.send(...)`) is unmodified and follows immediately
after:

```python
async def _process_social_url(message: Message, url: str) -> None:
    audio = AudioRequestData(link=url)
    if audio_raw := await redis_client.get(audio.cache_key):
        cached_audio = AudioRequestData.model_validate_json(audio_raw)
        log.info("cache hit (audio) for %s", audio.cache_key)
        await cached_audio.reply_to(message, page=1)
        return

    video = SocialVideoData.model_validate(dict(link=url))
```

(everything below this in the existing function -- the video cache-hit check, waiter
registration, and `process_social_link.send(...)` -- stays exactly as-is.)

- [ ] **Step 3: Add the `apg:` callback handler**

Insert after the existing `get_audio` callback handler (after line 132, before
`_process_social_url`):

```python
@router.callback_query(F.data.startswith("apg:"))
async def get_audio_page(callback: types.CallbackQuery):
    await callback.answer()
    if not isinstance(callback.message, types.Message):
        return

    _, hash16, page_str = callback.data.split(":")
    page = int(page_str)
    cache_key = f"da:{hash16}"

    # remove the pager buttons right away so a repeat tap can't queue/duplicate a delivery
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    audio_raw = await redis_client.get(cache_key)
    if not audio_raw:
        await callback.message.reply("❌ This playlist is no longer cached, please resend the link.")
        return

    audio = AudioRequestData.model_validate_json(audio_raw)
    page_tracks = audio.page(page)
    if all(t.file_id for t in page_tracks):
        # callback.message.bot is used implicitly by reply_to()'s bound methods --
        # avoids passing callback.bot (typed Bot | None) where a concrete Bot is required
        await audio.reply_to(callback.message, page=page)
        return

    log.info("cache miss for %s page %d, registering waiter", cache_key, page)
    page_key = f"{cache_key}:page:{page}"
    waiter = Waiter(
        chat_id=callback.message.chat.id,
        chat_type=callback.message.chat.type,
        reply_to_message_id=callback.message.message_id,
    )
    is_first = await register_waiter(redis_client, page_key, waiter, _SOCIAL_WAITERS_TTL)
    if is_first:
        process_audio_page.send(callback.message.chat.id, hash16, page)
```

- [ ] **Step 4: Typecheck**

Run: `uv run pyright bot/handlers.py`
Expected: `0 errors`

- [ ] **Step 5: Manual end-to-end smoke test**

This is the first point where the whole feature can be exercised through real Telegram
messages -- there's no automated harness for aiogram message flows in this repo (same as
every other handler here).

Run the bot locally:

```bash
docker compose up -d redis
uv run main.py
```

In Telegram, message the bot:

1. Send `http://youtube-dl.bandcamp.com/track/youtube-dl-test-song` (single track).
   Expect: one audio message arrives within a few seconds, no pager buttons.
2. Re-send the same link. Expect: instant resend (cache hit), no worker delay.
3. Send `http://blazo.bandcamp.com/album/jazz-format-mixtape-vol-1` (22-track album).
   Expect: a media group of 10 tracks arrives, followed by a "Page 1/3" message with a
   single "Next ▶️" button.
4. Tap "Next ▶️". Expect: the button disappears immediately, then (after a short worker
   delay) a media group of the next 10 tracks arrives with a "Page 2/3 [◀️ Back|Next ▶️]"
   message.
5. Tap "◀️ Back". Expect: **instant** redelivery of page 1's media group (no worker
   delay -- already cached), followed by a fresh "Page 1/3 [Next ▶️]" message.
6. Tap "Next ▶️" twice to reach page 3 (2 tracks). Expect: a 2-item media group (any page
   with more than one track uses the media-group path) with a follow-up message showing
   only a "◀️ Back" button (no "Next", it's the last page).
7. Send a link to a normal video platform already supported (e.g. a YouTube link) and
   confirm it still delivers as a video exactly as before -- the classification change
   must not affect non-audio links.

- [ ] **Step 6: Commit**

```bash
git add bot/handlers.py
git commit -m "Route audio links through the paginated audio pipeline"
```

---

## Task 7: Version bump

**Files:**
- Modify: `pyproject.toml:5`
- Modify: `uv.lock` (regenerated, not hand-edited)

**Interfaces:** none (final task).

- [ ] **Step 1: Bump the version**

Per `CLAUDE.md`'s versioning rules, this is new backward-compatible functionality (new
link origin support) — a **minor** bump. Edit `pyproject.toml`:

```toml
version = "0.4.0"
```

- [ ] **Step 2: Sync `uv.lock`**

Run: `uv sync`
Expected: exits 0; `uv.lock`'s self-entry for `embedthat-bot` now shows `version =
"0.4.0"`.

- [ ] **Step 3: Full repo typecheck**

Run: `uv run pyright`
Expected: `0 errors` (no regressions introduced across the whole change set).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "Bump version to 0.4.0 for audio-only platform support"
```
