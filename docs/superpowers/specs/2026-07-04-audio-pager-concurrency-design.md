# Audio pager UX + concurrent page downloads

## Context

Audio-mode playlist pages (`bot/util/audio/schema.py`, `bot/worker/pipeline.py`,
`bot/handlers.py`) currently:

- Download and dump-chat-upload the 10 tracks of a page strictly sequentially in
  `handle_audio_page`.
- Deliver a page's tracks as `send_media_group` (>1 track) or a single
  `reply_audio`/`send_audio` call (1 track), then send a **second** message
  containing plain text `"Page N/M"` (+ optional "N unavailable" note) and the
  Back/Next inline keyboard.

This is slow (one track downloads/uploads at a time) and produces a second,
mostly-empty message just to carry pager controls. Additionally: Telegram
strips `reply_markup` from forwarded messages, so a forwarded audio page loses
its Back/Next controls entirely, with no way back to the source.

## Goals

1. Download+upload the tracks on a page with bounded concurrency instead of
   one at a time.
2. Drop the standalone "Page N/M" text line; fold the page position into the
   button row itself.
3. Collapse to a single message wherever Telegram's API allows it.
4. Put the original source link in message *text* (not just buttons), so a
   forwarded audio still traces back to its source even though the buttons
   don't survive the forward.

## Non-goals

- No change to caching, waiter/lock coordination, or the actor/worker queuing
  (`bot/worker/actors.py`) — only what happens inside `handle_audio_page` and
  how `AudioRequestData` renders a page.
- No change to how Instagram/TikTok/Twitter/YouTube video links are handled.

## Design

### 1. Concurrent per-page downloads (`bot/worker/pipeline.py::handle_audio_page`)

Extract the current loop body into an inner coroutine:

```python
async def handle_audio_page(bot: Bot, tracks: list[AudioTrackData]) -> int:
    sem = asyncio.Semaphore(3)

    async def process_one(track: AudioTrackData, tmp_path: Path) -> bool:
        async with sem:
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

Notes:

- Concurrency capped at **3 simultaneous tracks** (`asyncio.Semaphore(3)`) —
  fast enough to meaningfully speed up a 10-track page, low risk of
  yt-dlp/YouTube throttling or Telegram flood-control on repeated
  `send_audio` calls to the same dump chat.
- Per-track retry semantics (3 attempts for download, 3 for upload) are
  unchanged; only *how many tracks run at once* changes.
- An unrecoverable `TelegramNetworkError` on the 3rd upload attempt still
  re-raises — same as today, this aborts the whole page rather than just
  failing one track. `asyncio.gather` without `return_exceptions=True`
  preserves that behavior (propagates immediately, other in-flight tracks are
  cancelled).
- Filenames in `download_track` are already unique per track
  (`{extractor}_{id}.%(ext)s`), so no path collisions under concurrency.

### 2. Pager buttons fold in the page count (`AudioRequestData.pager_markup`)

```python
def pager_markup(self, page: int) -> types.InlineKeyboardMarkup | None:
    if self.total_pages <= 1:
        return None
    buttons = []
    if page > 1:
        buttons.append(types.InlineKeyboardButton(text="◀️ Back", callback_data=f"apg:{self.hash16}:{page - 1}"))
    buttons.append(types.InlineKeyboardButton(text=f"{page}/{self.total_pages}", callback_data="apg:noop"))
    if page < self.total_pages:
        buttons.append(types.InlineKeyboardButton(text="Next ▶️", callback_data=f"apg:{self.hash16}:{page + 1}"))
    return types.InlineKeyboardMarkup(inline_keyboard=[buttons])
```

- Single row: `[◀️ Back] [2/5] [Next ▶️]`, with Back/Next present only where
  applicable, matching current behavior.
- Middle button is a no-op: `callback_data="apg:noop"`.
- `bot/handlers.py` gets a new handler registered **before** the existing
  `@router.callback_query(F.data.startswith("apg:"))` handler (aiogram tries
  handlers in registration order, and `"apg:noop"` would otherwise match the
  `startswith("apg:")` filter and crash on `callback.data.split(":")`
  expecting 3 parts):

```python
@router.callback_query(F.data == "apg:noop")
async def noop_page_indicator(callback: types.CallbackQuery):
    await callback.answer()
```

### 3. Collapse to one message where the Bot API allows it

`sendMediaGroup` has no `reply_markup` parameter — Telegram gives no way to
attach buttons to a media-group message. So the existing
`len(deliverable) == 1` vs. `len(deliverable) > 1` branch in
`send_to_chat`/`reply_to` becomes the fork point:

- **`len(deliverable) == 1`** (single audio message): attach `caption` and
  `reply_markup` directly to that call. Single message, no follow-up.
- **`len(deliverable) > 1`** (media group): unavoidably still a second
  message, now carrying only the caption text + buttons (no more bare
  "Page N/M" line — that moved into the buttons per section 2).

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

`send_to_chat` (used by the waiter-notify path in
`bot/worker/actors.py::_notify_audio_page_waiters_success`) mirrors the same
change with `bot.send_audio(...)` / `bot.send_message(...)`.

### 4. Source link in text (`_pager_text` → `_pager_caption`)

```python
def _pager_caption(self, skipped: int) -> str:
    parts = []
    if skipped:
        parts.append(f"⚠️ {skipped} unavailable")
    parts.append(f'🔗 <a href="{html.escape(self.link)}">Source</a>')
    return "\n".join(parts)
```

- `self.link` is the original URL the user sent (already stored on
  `AudioRequestData`, already used to derive `hash16`/`cache_key`).
- HTML-escaped and rendered as a short "Source" hyperlink via
  `parse_mode="HTML"`, with `disable_web_page_preview=True` so it doesn't
  spawn a link-preview card.
- Shown under the same condition as today's pager text (`markup or skipped`)
  — whenever there's pager info worth showing, the source link rides along,
  so a forwarded message (which keeps caption/text but loses `reply_markup`)
  still traces back to origin even without working buttons.

## Files touched

- `bot/util/audio/schema.py` — `pager_markup`, `_pager_text` → `_pager_caption`,
  `send_to_chat`, `reply_to`.
- `bot/worker/pipeline.py` — `handle_audio_page` concurrency.
- `bot/handlers.py` — new `apg:noop` handler, registered before `get_audio_page`.

## Testing

No test suite exists in this repo (per `CLAUDE.md`). Verification will be
manual: send a multi-page playlist link to the bot, page through it, confirm:

- Buttons show `N/M` in the middle, no separate "Page N/M" text message.
- Single-track pages arrive as one message with caption + buttons attached.
- Multi-track pages arrive as a media group + one follow-up message (caption +
  buttons only).
- Tapping the `N/M` button does nothing but clears the loading spinner.
- Forwarding a pager message preserves the "🔗 Source" link (buttons will
  legitimately disappear — expected Telegram behavior).
- A 10-track page downloads noticeably faster than before.
