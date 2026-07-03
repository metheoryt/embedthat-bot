# Audio-only platform support (SoundCloud, Bandcamp, Mixcloud, etc. via yt-dlp)

## Context

Today, every non-YouTube link that isn't already Instagram/TikTok/Twitter-specific falls
through `bot/handlers.py::embed_social`'s catch-all regex and is always treated as **video**:
`process_social_link` → `bot/worker/pipeline.py::handle_social_video` →
`bot/util/social/download.py::download_social_video`, a single yt-dlp call using a
video-oriented format string, always re-encoded to H.264/AAC. There is no path for
audio-only sources (SoundCloud, Bandcamp, Mixcloud, Audiomack, etc.) — such a link today
either fails yt-dlp's video-format selection or produces a degenerate result.

**Goal:** support single tracks and albums/playlists from any yt-dlp-supported audio-only
source, without hardcoding a platform list ("as much as yt-dlp provides"). Existing YouTube
and Instagram/TikTok/Twitter video paths are untouched.

## 1. Detection: decided in the worker, not the handler

The handler can't classify a link as audio-only by URL pattern alone — genericity requires
asking yt-dlp. So the handler stays network-free and defers classification:

- `embed_social` computes both candidate cache keys for the URL — the existing video cache
  `dl:{hash16(url)}` and a new audio cache `da:{hash16(url)}` — and does a plain Redis GET on
  each. A hit on either delivers from cache exactly as today (no network probe).
- On a full miss, it registers a waiter and enqueues the same generic actor
  (`process_social_link`) as today. That actor performs one classification probe: fully
  resolve the *first* item yt-dlp reports for the URL (single item, or first entry if it's a
  playlist) and check whether any entry in `info['formats']` has `vcodec != 'none'`. No
  video-capable format ⇒ audio-only. This intentionally does not trust top-level
  `info['vcodec']`, which is unset before format selection.
  - If video → proceed exactly as `handle_social_video` today (unchanged), cache under `dl:`.
  - If audio → build the track index (see §2) and proceed to page-1 delivery (see §4),
    caching under `da:`.

Only one deep probe is needed regardless of playlist size, because the rest of the track
index is built from yt-dlp's cheap flat/shallow listing (titles, ids, per-track URLs, no
per-entry format resolution) rather than deep-probing every entry up front. Individual
tracks are deep-probed (to get real download URLs/formats) only when actually downloaded,
lazily, page by page.

## 2. Data model: new `bot/util/audio/` module

`SocialVideoData.file_ids` models "parts of *one* video" — a playlist is N distinct tracks
each with their own title/artist/duration, which doesn't fit that shape. New module,
parallel to `util/social/` and `util/youtube/`:

- `bot/util/audio/schema.py`:
  - `AudioTrackData`: `extractor`, `id`, `webpage_url`, `title`, `uploader` (artist),
    `duration`, `file_id: str | None = None`. Doubles as both a list entry and the value
    shape stored in the per-track dedup cache (see §5) — no separate model needed.
  - `AudioRequestData`: `link: str`, `tracks: list[AudioTrackData]`. Cache key
    `da:{hash16(link)}` (mirrors `dl:` for video). Properties: `total_pages` (`ceil(len(tracks)
    / 10)`), `page(n)` (returns the 10-track slice). Implements `reply_to()` /
    `send_to_chat()` mirroring `SocialVideoData`'s convention, so the existing
    `_notify_waiters_success` helper in `actors.py` (which calls `.send_to_chat(...)`
    generically) needs no changes: these methods deliver page 1 (single `send_audio` if one
    track, `send_media_group` of `InputMediaAudio` if more) and then post the pager
    follow-up (§4) if `total_pages > 1`.
- `bot/util/audio/download.py`: `build_track_index(url, max_tracks) -> list[AudioTrackData]`
  (flat/shallow listing, capped) and `download_track(track, output_dir) -> Path` (real
  per-track download).
- `bot/util/audio/exc.py`: `AudioDownloadError`.

## 3. Download specifics

- Format string per track: `bestaudio/best` — no forced re-encode (unlike video's mandatory
  H.264 pass); audio formats upload to Telegram natively.
- Track index capped at `settings.max_playlist_tracks` (default **200**) — a listing-size
  abuse guard only, not a consumption limit, since downloads happen lazily per page.
- Oversized single tracks (e.g. multi-hour DJ mixes >50 MB): **reject with an error**, not
  ffmpeg-split. Splitting mid-track/mid-song is bad UX, unlike video where part-splitting is
  an accepted trade-off for size limits.
- Per-track download retries follow the existing pattern (3 tries, 2s backoff) but a single
  track's final failure **skips that track** rather than failing the whole page — a 10-track
  page shouldn't fail entirely because one track is geo-blocked/removed. The pager control
  message notes any skipped count (e.g. "⚠️ 1 track unavailable").

## 4. Delivery & pagination

Single code path handles every page request — initial page 1, and every Back/Next tap:

1. Given a target page number, check whether **every** track in that page's slice already
   has a cached `file_id` (via the per-track dedup cache, §5).
2. **All cached** → deliver instantly (single `send_audio` or `send_media_group`), no worker
   job. Because you can only reach a page you've already visited going forward (or page 1),
   **Back is always instant**.
3. **Any missing** → register a waiter (key `da:{hash16}:page:{n}`) and enqueue a page-scoped
   dramatiq actor that downloads only the missing tracks, updates both the per-track cache
   and the `AudioRequestData.tracks` entries (so future resends see up-to-date `file_id`s,
   mirroring how the YouTube audio button back-fills `audio_file_id`), then delivers and
   notifies waiters.
4. After delivering any page, a **new pager control message** is sent below it: "◀️ Back" if
   `page > 1`, "Next ▶️" if `page < total_pages`. Omitted entirely when `total_pages == 1`
   (a lone track needs no pager).
5. Tapping Back/Next immediately clears that control message's `reply_markup` (existing
   anti-double-tap pattern, same as the YouTube audio button) before dispatching — cache-hit
   taps resolve synchronously in the handler (same precedent as the YouTube button's
   already-cached fast path); cache-miss taps enqueue the page actor.
6. Callback data: `apg:{hash16}:{page}` (well under the 64-byte limit), used identically for
   both directions — the target page is the only thing that matters, not which button was
   pressed.

A page's tracks send as one `send_audio` call if the page has exactly one track, or one
`send_media_group` of `InputMediaAudio` (carrying per-track title/performer/duration) if
more — same one-vs-many convention `SocialVideoData` already uses for video parts.

## 5. Caching

Two Redis namespaces:

- `da:{hash16(link)}` — the *request*: full `AudioRequestData` (ordered track list +
  whatever `file_id`s have been resolved so far). Enables instant resend of the same
  playlist/track link and survives across page visits.
- `au:{extractor}:{track_id}` — per-*track* dedup cache (`AudioTrackData` JSON incl.
  `file_id`), independent of which playlist/request it was downloaded from. A track
  requested standalone, or reappearing in a different album, reuses the already-uploaded
  `file_id` instead of re-downloading — the same dedup principle the YouTube per-video cache
  already relies on.

## 6. Worker/actor integration

- `bot/worker/pipeline.py`: new `handle_audio_page(bot, request: AudioRequestData, page:
  int)`, used both by `_process_social_link_async` (for page 1, once classification says
  "audio") and by a new dedicated actor for pages ≥2.
- `bot/worker/actors.py`: new `process_audio_page(chat_id, hash16, page,
  reply_to_message_id)` dramatiq actor, `max_retries=2`, `throws=(AudioDownloadError,)`,
  `time_limit=30*60_000` (30 min — a page tops out at 10 individual track downloads +
  dump-chat uploads, heavier than one video but bounded). Reuses the existing
  `register_waiter`/`pop_waiters`/`HeartbeatLock` machinery keyed off
  `da:{hash16}:page:{n}`.
- `bot/handlers.py`: new callback handler on `F.data.startswith("apg:")`, parallel to the
  existing `aud:` (YouTube audio button) handler.

## 7. Config additions

- `settings.max_playlist_tracks: int = 200` (env `MAX_PLAYLIST_TRACKS`).

## 8. Out of scope

- Spotify (yt-dlp cannot fetch audio — DRM).
- Re-encoding/normalizing audio quality (upload whatever yt-dlp's `bestaudio` returns).
- Admin/runtime control of `max_playlist_tracks` (env var only).
- Sharing audio deliveries to the feed channel (`bot/events/handlers/share.py` stays
  video-only; no `on_audio_sent` signal is introduced since nothing would consume it yet).
