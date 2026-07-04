# Audio Pager UX + Concurrent Page Downloads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold the audio pager's page count into its Back/Next buttons, collapse to a single Telegram message wherever the Bot API allows it, add a source-link that survives forwarding, and make per-page track downloads run concurrently instead of one at a time.

**Architecture:** Two small, independent changes to `bot/util/audio/schema.py` (pager rendering: button layout + caption text + the single-message-vs-media-group fork), one two-line addition to `bot/handlers.py` (a no-op callback handler for the new page-indicator button), and one concurrency change to `bot/worker/pipeline.py::handle_audio_page` (bounded `asyncio.gather` instead of a sequential loop).

**Tech Stack:** Python 3.12, aiogram 3.17 (Telegram Bot API), pydantic (models), yt-dlp (downloads), `uv` (dependency/run manager), pyright (type checking — the only static-analysis tool configured in this repo).

## Global Constraints

- No test suite or linter is configured in this repo (per `CLAUDE.md`). Verification in every task below uses `uv run pyright <file>` for type-checking plus a throwaway verification script run via `uv run python <script>` — these scripts live in the scratchpad temp dir, are never committed, and exist only to exercise the new logic without a live Telegram bot token or network access.
- The verification scripts assert against strings containing emoji (button labels, captions). On a Windows console using a non-UTF-8 codepage (e.g. cp1251), an *assertion failure* message containing emoji can itself raise `UnicodeEncodeError` when Python tries to print the traceback, masking the real failure. Run all verification scripts with `PYTHONIOENCODING=utf-8` set (e.g. `set PYTHONIOENCODING=utf-8 &&` on cmd.exe, `$env:PYTHONIOENCODING="utf-8";` on PowerShell) to avoid this.
- Download concurrency is capped at **3 simultaneous tracks** (`asyncio.Semaphore(3)`).
- The pager keyboard is a single row: `[◀️ Back] [N/M] [Next ▶️]`, Back/Next present only where applicable, and the whole row is omitted (`pager_markup` returns `None`) when `total_pages <= 1`. The middle button's `callback_data` is the literal string `"apg:noop"`.
- The pager caption is HTML (`parse_mode="HTML"`), always includes an HTML-escaped hyperlink to `self.link` labeled "Source", and is sent with `disable_web_page_preview=True`.
- A page whose deliverable-track count is exactly 1 must render as a single Telegram message (caption + buttons attached directly to the audio message) — no follow-up message. A page with more than 1 deliverable track must still send a follow-up message for the caption/buttons, because Telegram's `sendMediaGroup` has no `reply_markup` parameter.
- Existing method signatures (`reply_to(message, page=1)`, `send_to_chat(bot, chat_id, reply_to_message_id=None, page=1)`) do not change — only their internals and `pager_markup`/caption-method internals change.

---

### Task 1: Pager button layout + caption text (`bot/util/audio/schema.py`)

**Files:**
- Modify: `bot/util/audio/schema.py:1` (add `import html`)
- Modify: `bot/util/audio/schema.py:55-73` (replace `pager_markup` and `_pager_text` with `pager_markup` and `_pager_caption`)

**Interfaces:**
- Consumes: `self.hash16 -> str` (existing property, `bot/util/audio/schema.py:40`), `self.total_pages -> int` (existing property, `bot/util/audio/schema.py:48`), `self.link -> str` (existing pydantic field).
- Produces: `pager_markup(self, page: int) -> types.InlineKeyboardMarkup | None` (same name/signature as before, new body: single row `[Back?] [N/M] [Next?]`, `None` when `total_pages <= 1`). `_pager_caption(self, skipped: int) -> str` (**renamed** from `_pager_text(self, page, skipped)` — drops the `page` parameter since the page number no longer appears in the text; Task 2 updates the two call sites).

- [ ] **Step 1: Add the `html` import**

In `bot/util/audio/schema.py`, the current top of the file is:

```python
import hashlib
import math
from typing import cast

from aiogram import types, Bot
from pydantic import BaseModel, Field
```

Change it to:

```python
import hashlib
import html
import math
from typing import cast

from aiogram import types, Bot
from pydantic import BaseModel, Field
```

- [ ] **Step 2: Replace `pager_markup` and `_pager_text`**

Find this block (currently at `bot/util/audio/schema.py:55-73`):

```python
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
```

Replace it with:

```python
    def pager_markup(self, page: int) -> types.InlineKeyboardMarkup | None:
        if self.total_pages <= 1:
            return None
        buttons = []
        if page > 1:
            buttons.append(
                types.InlineKeyboardButton(text="◀️ Back", callback_data=f"apg:{self.hash16}:{page - 1}")
            )
        buttons.append(
            types.InlineKeyboardButton(text=f"{page}/{self.total_pages}", callback_data="apg:noop")
        )
        if page < self.total_pages:
            buttons.append(
                types.InlineKeyboardButton(text="Next ▶️", callback_data=f"apg:{self.hash16}:{page + 1}")
            )
        return types.InlineKeyboardMarkup(inline_keyboard=[buttons])

    def _pager_caption(self, skipped: int) -> str:
        parts = []
        if skipped:
            parts.append(f"⚠️ {skipped} unavailable")
        parts.append(f'🔗 <a href="{html.escape(self.link)}">Source</a>')
        return "\n".join(parts)
```

- [ ] **Step 3: Type-check the file**

Run: `uv run pyright bot/util/audio/schema.py`
Expected: pyright reports errors for the now-unresolved calls to `_pager_text` in `send_to_chat`/`reply_to` (those call sites are fixed in Task 2) — no other errors. If any other error appears, fix it before moving on.

- [ ] **Step 4: Write and run a throwaway verification script**

Save this to the scratchpad temp dir as `verify_pager_markup.py` (not committed — no test suite exists in this repo):

```python
from bot.util.audio.schema import AudioRequestData, AudioTrackData

tracks = [AudioTrackData(extractor="yt", id=str(i), webpage_url=f"https://x/{i}") for i in range(25)]
audio = AudioRequestData(link="https://example.com/playlist?a=1&b=2", tracks=tracks)
assert audio.total_pages == 3, audio.total_pages

# middle page: both Back and Next, plus the N/M indicator
markup = audio.pager_markup(2)
assert markup is not None
row = markup.inline_keyboard[0]
labels = [b.text for b in row]
assert labels == ["◀️ Back", "2/3", "Next ▶️"], labels
assert row[1].callback_data == "apg:noop", row[1].callback_data
assert row[0].callback_data == f"apg:{audio.hash16}:1"
assert row[2].callback_data == f"apg:{audio.hash16}:3"

# first page: no Back button
row1 = audio.pager_markup(1).inline_keyboard[0]
assert [b.text for b in row1] == ["1/3", "Next ▶️"], [b.text for b in row1]

# last page: no Next button
row3 = audio.pager_markup(3).inline_keyboard[0]
assert [b.text for b in row3] == ["◀️ Back", "3/3"], [b.text for b in row3]

# single-page playlist: no markup at all
single = AudioRequestData(link="https://x", tracks=[AudioTrackData(extractor="yt", id="1", webpage_url="https://x/1")])
assert single.pager_markup(1) is None

# caption: escapes the link and reports skipped count
caption = audio._pager_caption(skipped=2)
assert caption == '⚠️ 2 unavailable\n🔗 <a href="https://example.com/playlist?a=1&amp;b=2">Source</a>', caption
caption_no_skip = audio._pager_caption(skipped=0)
assert caption_no_skip == '🔗 <a href="https://example.com/playlist?a=1&amp;b=2">Source</a>', caption_no_skip

print("Task 1 OK")
```

Run: `uv run python <scratchpad_path>/verify_pager_markup.py` (run from the repo root so the `bot` package resolves)
Expected output: `Task 1 OK`

- [ ] **Step 5: Commit**

```bash
git add bot/util/audio/schema.py
git commit -m "Fold audio pager page count into button labels, drop the standalone page-line text"
```

---

### Task 2: Collapse to one message where possible, attach the source-link caption (`bot/util/audio/schema.py`)

**Files:**
- Modify: `bot/util/audio/schema.py:75-98` (`send_to_chat`)
- Modify: `bot/util/audio/schema.py:100-116` (`reply_to`)

**Interfaces:**
- Consumes: `pager_markup(page) -> InlineKeyboardMarkup | None` and `_pager_caption(skipped) -> str` (both from Task 1).
- Produces: `send_to_chat(self, bot: Bot, chat_id: int, reply_to_message_id: int | None = None, page: int = 1) -> None` and `reply_to(self, message: types.Message, page: int = 1) -> None` — same signatures as before; downstream callers (`bot/handlers.py::get_audio_page` calling `audio.reply_to(callback.message, page=page)`, `bot/worker/actors.py::_notify_audio_page_waiters_success` calling `audio.send_to_chat(bot, waiter.chat_id, page=page)`) need no changes.

- [ ] **Step 1: Replace `send_to_chat`**

Find (currently `bot/util/audio/schema.py:75-98`):

```python
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
                chat_id, cast(str, t.file_id), performer=t.uploader, title=t.title, duration=t.duration,
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
```

Replace with:

```python
    async def send_to_chat(self, bot: Bot, chat_id: int, reply_to_message_id: int | None = None, page: int = 1) -> None:
        page_tracks = self.page(page)
        deliverable = [t for t in page_tracks if t.file_id]
        if not deliverable:
            await bot.send_message(
                chat_id, "❌ No tracks on this page could be downloaded.", reply_to_message_id=reply_to_message_id
            )
            return

        skipped = len(page_tracks) - len(deliverable)
        markup = self.pager_markup(page)
        caption = self._pager_caption(skipped) if (markup or skipped) else None

        if len(deliverable) == 1:
            t = deliverable[0]
            await bot.send_audio(
                chat_id, cast(str, t.file_id), performer=t.uploader, title=t.title, duration=t.duration,
                reply_to_message_id=reply_to_message_id,
                caption=caption, parse_mode="HTML" if caption else None, reply_markup=markup,
            )
        else:
            await bot.send_media_group(
                chat_id, [t.as_input_media for t in deliverable], reply_to_message_id=reply_to_message_id
            )
            if caption:
                await bot.send_message(
                    chat_id, caption, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True
                )
```

- [ ] **Step 2: Replace `reply_to`**

Find (currently `bot/util/audio/schema.py:100-116`):

```python
    async def reply_to(self, message: types.Message, page: int = 1) -> None:
        page_tracks = self.page(page)
        deliverable = [t for t in page_tracks if t.file_id]
        if not deliverable:
            await message.reply("❌ No tracks on this page could be downloaded.")
            return

        if len(deliverable) == 1:
            t = deliverable[0]
            await message.reply_audio(cast(str, t.file_id), performer=t.uploader, title=t.title, duration=t.duration)
        else:
            await message.reply_media_group([t.as_input_media for t in deliverable])

        skipped = len(page_tracks) - len(deliverable)
        markup = self.pager_markup(page)
        if markup or skipped:
            await message.reply(self._pager_text(page, skipped), reply_markup=markup)
```

Replace with:

```python
    async def reply_to(self, message: types.Message, page: int = 1) -> None:
        page_tracks = self.page(page)
        deliverable = [t for t in page_tracks if t.file_id]
        if not deliverable:
            await message.reply("❌ No tracks on this page could be downloaded.")
            return

        skipped = len(page_tracks) - len(deliverable)
        markup = self.pager_markup(page)
        caption = self._pager_caption(skipped) if (markup or skipped) else None

        if len(deliverable) == 1:
            t = deliverable[0]
            await message.reply_audio(
                cast(str, t.file_id), performer=t.uploader, title=t.title, duration=t.duration,
                caption=caption, parse_mode="HTML" if caption else None, reply_markup=markup,
            )
        else:
            await message.reply_media_group([t.as_input_media for t in deliverable])
            if caption:
                await message.reply(caption, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
```

- [ ] **Step 3: Type-check the file**

Run: `uv run pyright bot/util/audio/schema.py`
Expected: no errors (the Task 1 `_pager_text` errors are now gone since both call sites use `_pager_caption`).

- [ ] **Step 4: Write and run a throwaway verification script**

Save to the scratchpad temp dir as `verify_reply_to.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

from bot.util.audio.schema import AudioRequestData, AudioTrackData


async def main():
    # 11 tracks, PAGE_SIZE=10 -> page 1 has 10 tracks (media group), page 2 has 1 track (single message)
    tracks = [
        AudioTrackData(extractor="yt", id=str(i), webpage_url=f"https://x/{i}", file_id=f"fid{i}")
        for i in range(11)
    ]
    audio = AudioRequestData(link="https://example.com/playlist", tracks=tracks)

    # --- single-track page (page 2): must collapse to ONE message ---
    msg = MagicMock()
    msg.reply = AsyncMock()
    msg.reply_audio = AsyncMock()
    msg.reply_media_group = AsyncMock()

    await audio.reply_to(msg, page=2)

    assert msg.reply_audio.await_count == 1
    _, kwargs = msg.reply_audio.await_args
    assert kwargs["reply_markup"] is not None, "page 2 has a Back button, markup must be set"
    assert kwargs["parse_mode"] == "HTML"
    assert "Source" in kwargs["caption"]
    assert msg.reply.await_count == 0, "single-track page must NOT send a second message"
    print("single-track page: OK")

    # --- multi-track page (page 1): media group + exactly one follow-up message ---
    msg2 = MagicMock()
    msg2.reply = AsyncMock()
    msg2.reply_audio = AsyncMock()
    msg2.reply_media_group = AsyncMock()

    await audio.reply_to(msg2, page=1)

    assert msg2.reply_media_group.await_count == 1
    assert msg2.reply_audio.await_count == 0
    assert msg2.reply.await_count == 1, "multi-track page must send exactly one follow-up message"
    call = msg2.reply.await_args
    assert "Source" in call.args[0]
    assert call.kwargs["reply_markup"] is not None
    assert call.kwargs["parse_mode"] == "HTML"
    assert call.kwargs["disable_web_page_preview"] is True
    print("multi-track page: OK")

    print("Task 2 OK")


asyncio.run(main())
```

Run: `uv run python <scratchpad_path>/verify_reply_to.py` (run from the repo root)
Expected output:
```
single-track page: OK
multi-track page: OK
Task 2 OK
```

- [ ] **Step 5: Commit**

```bash
git add bot/util/audio/schema.py
git commit -m "Collapse audio pager to a single message when the page has one deliverable track"
```

---

### Task 3: Handle taps on the page-indicator button (`bot/handlers.py`)

**Files:**
- Modify: `bot/handlers.py:140` (insert a new handler immediately before the existing `@router.callback_query(F.data.startswith("apg:"))` handler)

**Interfaces:**
- Consumes: the `callback_data == "apg:noop"` literal produced by Task 1's `pager_markup`.
- Produces: `noop_page_indicator(callback: types.CallbackQuery) -> None` (registered as an aiogram callback-query handler; not called directly by any other code).

- [ ] **Step 1: Insert the no-op handler before `get_audio_page`**

In `bot/handlers.py`, find:

```python
@router.callback_query(F.data.startswith("apg:"))
async def get_audio_page(callback: types.CallbackQuery):
```

Insert immediately above it (aiogram tries handlers in registration order — this must come first so `"apg:noop"` doesn't reach `get_audio_page`'s `callback.data.split(":")`, which expects exactly 3 colon-separated parts and would raise `ValueError` on `"apg:noop"`):

```python
@router.callback_query(F.data == "apg:noop")
async def noop_page_indicator(callback: types.CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("apg:"))
async def get_audio_page(callback: types.CallbackQuery):
```

- [ ] **Step 2: Type-check the file**

Run: `uv run pyright bot/handlers.py`
Expected: no errors.

- [ ] **Step 3: Write and run a throwaway verification script**

This checks aiogram's filter objects behave as expected in isolation (no bot token / network needed). `F.data == "..."` and `F.data.startswith("...")` produce `aiogram.utils.magic_filter.MagicFilter` objects, evaluated synchronously via `.resolve(event)`. Save to the scratchpad temp dir as `verify_noop_filter.py`:

```python
from aiogram import F
from unittest.mock import MagicMock

noop_filter = F.data == "apg:noop"
prefix_filter = F.data.startswith("apg:")

noop_cb = MagicMock()
noop_cb.data = "apg:noop"
real_cb = MagicMock()
real_cb.data = "apg:abcd1234abcd1234:2"

assert noop_filter.resolve(noop_cb) is True
assert prefix_filter.resolve(noop_cb) is True  # confirms it WOULD also match the old handler
assert noop_filter.resolve(real_cb) is False
assert prefix_filter.resolve(real_cb) is True

print("Task 3 OK: apg:noop matches both filters, so registration order matters and is correct")
```

Run: `uv run python <scratchpad_path>/verify_noop_filter.py`
Expected output: `Task 3 OK: apg:noop matches both filters, so registration order matters and is correct`

- [ ] **Step 4: Commit**

```bash
git add bot/handlers.py
git commit -m "Answer taps on the audio pager's page-indicator button as a no-op"
```

---

### Task 4: Download tracks on a page concurrently (`bot/worker/pipeline.py`)

**Files:**
- Modify: `bot/worker/pipeline.py:136-189` (`handle_audio_page`)

**Interfaces:**
- Consumes: `download_track(track: AudioTrackData, output_dir: Path) -> Path` (existing, `bot/util/audio/download.py:93`, raises `AudioDownloadError` on unrecoverable failure), `bot.send_audio(...)` (aiogram `Bot` method, raises `TelegramNetworkError` on network failure).
- Produces: `handle_audio_page(bot: Bot, tracks: list[AudioTrackData]) -> int` — same signature as before (returns the count of tracks that failed and were skipped); callers (`bot/worker/actors.py::_process_audio_page_async`) need no changes.

- [ ] **Step 1: Replace the sequential loop with bounded concurrency**

Find (currently `bot/worker/pipeline.py:136-189`):

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

Replace with:

```python
async def handle_audio_page(bot: Bot, tracks: list[AudioTrackData]) -> int:
    """
    Downloads and dump-chat-uploads every track in `tracks` missing a file_id,
    mutating each in place. Returns how many tracks failed and were skipped --
    one bad track (geo-blocked/removed) shouldn't take down the whole page.
    Up to 3 tracks are downloaded/uploaded concurrently.
    """
    semaphore = asyncio.Semaphore(3)

    async def process_one(track: AudioTrackData, tmp_path: Path) -> bool:
        async with semaphore:
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
                return False

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
            return True

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pending = [t for t in tracks if not t.file_id]
        results = await asyncio.gather(*(process_one(t, tmp_path) for t in pending))

    return sum(1 for ok in results if not ok)
```

- [ ] **Step 2: Type-check the file**

Run: `uv run pyright bot/worker/pipeline.py`
Expected: no errors.

- [ ] **Step 3: Write and run a throwaway verification script**

This proves the semaphore actually bounds concurrency at 3 (and that it's genuinely concurrent, not accidentally still sequential) using fakes — no network or Telegram token needed. Save to the scratchpad temp dir as `verify_concurrency.py`:

```python
import asyncio
import time
from unittest.mock import MagicMock

import bot.worker.pipeline as pipeline
from bot.util.audio.schema import AudioTrackData


async def main():
    tracks = [
        AudioTrackData(extractor="yt", id=str(i), webpage_url=f"https://x/{i}")
        for i in range(9)
    ]

    concurrent = 0
    max_concurrent = 0

    def fake_download_track(track, tmp_path):
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        time.sleep(0.05)
        concurrent -= 1
        return tmp_path / f"{track.id}.mp3"

    async def fake_send_audio(*args, **kwargs):
        m = MagicMock()
        m.audio.file_id = f"fid-{args[1] if len(args) > 1 else 'x'}"
        return m

    original_download_track = pipeline.download_track
    pipeline.download_track = fake_download_track
    try:
        bot = MagicMock()
        bot.send_audio = fake_send_audio

        start = time.monotonic()
        failed = await pipeline.handle_audio_page(bot, tracks)
        elapsed = time.monotonic() - start
    finally:
        pipeline.download_track = original_download_track

    assert failed == 0, failed
    assert all(t.file_id for t in tracks)
    assert max_concurrent > 1, f"expected concurrent downloads, saw max_concurrent={max_concurrent}"
    assert max_concurrent <= 3, f"expected at most 3 concurrent downloads, saw {max_concurrent}"
    # 9 tracks at 0.05s each, capped at 3 concurrent -> ~3 batches -> ~0.15s, vs ~0.45s if sequential
    assert elapsed < 0.35, f"expected concurrent speedup, took {elapsed:.2f}s"

    print(f"Task 4 OK: max_concurrent={max_concurrent}, elapsed={elapsed:.2f}s")


asyncio.run(main())
```

Run: `uv run python <scratchpad_path>/verify_concurrency.py`
Expected output (numbers will vary slightly): `Task 4 OK: max_concurrent=3, elapsed=0.1Xs`

- [ ] **Step 4: Commit**

```bash
git add bot/worker/pipeline.py
git commit -m "Download and upload a page's audio tracks concurrently, capped at 3 at a time"
```

---

### Task 5: Version bump and manual end-to-end verification

**Files:**
- Modify: `pyproject.toml:5` (`version = "0.4.0"` → `version = "0.4.1"`)

This is a UX/performance refinement of the existing audio-pagination feature (no new command, no new link origin, no cache/schema change) — a patch bump per the versioning rule in `CLAUDE.md`.

- [ ] **Step 1: Bump the version**

In `pyproject.toml`, change:

```toml
version = "0.4.0"
```

to:

```toml
version = "0.4.1"
```

- [ ] **Step 2: Commit the version bump**

```bash
git add pyproject.toml
git commit -m "Bump version to 0.4.1 for the audio pager UX and concurrency changes"
```

- [ ] **Step 3: Manual end-to-end verification against a real bot**

The automated checks in Tasks 1-4 cover the logic in isolation; this step confirms it against real Telegram. Requires `.env` populated (`BOT_TOKEN`, `DUMP_CHAT_ID`) and the bot running (`uv run main.py` or `docker compose up -d`). Send a playlist link with more than 10 tracks (so it spans at least 2 pages) to the bot in a real Telegram chat, then confirm:

- [ ] The buttons show `N/M` in the middle (e.g. `2/3`), and there is no separate "Page N/M" text message anywhere.
- [ ] A page with more than 1 deliverable track arrives as a media group, immediately followed by exactly one more message containing only the "🔗 Source" link (and, if any tracks failed, the "⚠️ N unavailable" line) plus the Back/Next/`N/M` buttons.
- [ ] A page with exactly 1 deliverable track (e.g. the last page of an 11-track playlist) arrives as a single message: the audio itself, with the source-link caption and buttons attached directly — no follow-up message.
- [ ] Tapping the `N/M` button does nothing but clears the loading spinner (no error, no page change).
- [ ] Tapping Back/Next still navigates pages correctly.
- [ ] Forwarding a pager message to another chat: the "🔗 Source" link is still present and clickable in the forwarded message; the Back/Next/`N/M` buttons are gone (this is expected — Telegram strips `reply_markup` on forward, which is exactly why the source link exists in the text).
- [ ] A page with 10 tracks that all need downloading (cache miss) visibly completes faster than before the change (previously ~10x one track's download+upload time sequentially; now ~ceil(10/3)x that time).

If any check fails, fix the relevant task's code before considering this plan complete.
