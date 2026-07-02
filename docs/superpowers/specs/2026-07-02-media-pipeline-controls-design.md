# Media pipeline controls: translation kill switch, 480p hard cap, audio-only downloads

## Context

`CLAUDE.md`'s architecture description is stale: Instagram/TikTok/Twitter no longer get
domain-rewritten to proxy embed links. As of `6fa6a35`/`308a971` (already on `main`),
**all** supported platforms go through a real download-and-reupload pipeline via Dramatiq
workers:

- **YouTube** — `bot/worker/pipeline.py::handle_youtube_video` → `bot/util/youtube/video.py`
  (`check_download_adaptive` → `pick_stream`), adaptive pytubefix streams, with an optional
  audio-translation step (`bot/util/youtube/translate.py`).
- **Social (Instagram/TikTok/Twitter)** — `bot/worker/pipeline.py::handle_social_video` →
  `bot/util/social/download.py::download_social_video`, a single yt-dlp call with a fixed
  `"bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"` format string, always
  re-encoded to iOS-compatible H.264/AAC via `postprocessor_args`.

Both pipelines converge on `_upload_parts_to_dump_chat`, which sends files to
`settings.dump_chat_id` to mint stable Telegram `file_id`s, then cache them in Redis keyed
by a link-derived cache key.

This spec covers three independent-but-related changes to that pipeline. `CLAUDE.md`
should be refreshed separately (not part of this spec) to reflect the real architecture.

## A. Audio-translation feature flag (kill switch, default off)

**Goal:** disable audio translation everywhere without deleting the code, so it can be
re-enabled later via config alone.

- New field in `bot/config.py`: `enable_audio_translation: bool = False`
  (env var `ENABLE_AUDIO_TRANSLATION`).
- **Two gate points:**
  1. `bot/handlers.py::embed_youtube_videos` — when the flag is off, skip resolving
     `target_lang` from `message.from_user.language_code` entirely; always construct
     `YouTubeVideoData` with `target_lang=TargetLang.ORIGINAL`. This avoids fragmenting the
     cache: today `cache_key` factors in `target_lang`, so two users with different
     Telegram client languages requesting the same video would otherwise produce two
     redundant cache entries with identical (untranslated) output.
  2. `bot/util/youtube/video.py::get_audio_stream` — keep the existing
     `video.target_lang != TargetLang.ORIGINAL` check, AND it with
     `settings.enable_audio_translation`, as defense in depth against any other code path
     that might construct a `YouTubeVideoData` with a non-`ORIGINAL` `target_lang`.
- `bot/util/youtube/translate.py` is untouched — fully intact, just unreachable while the
  flag is off.

## B. 480p hard cap on every uploaded video

**Goal:** no uploaded video — YouTube or social — exceeds 480p. When a source's smallest
available stream at or above 480p is larger, downscale via ffmpeg rather than upload as-is.

- New field in `bot/config.py`: `max_video_resolution: int = 480`
  (env var `MAX_VIDEO_RESOLUTION`).

### YouTube (`bot/util/youtube/video.py::pick_stream`)

`pick_stream` already ffprobes each candidate stream's *real* resolution up front (the
`real_res_to_stream` dedup step, before any downloading), so tiering can use real data
rather than trusting `stream.resolution` metadata:

1. Keep the existing candidate filter (mp4/avc1 codec, `resolution >= min_res` floor) and
   the real-resolution probe/dedup step unchanged.
2. Split candidates into two tiers:
   - **Tier 1** — real height `>= max_res`, sorted **ascending** (smallest first: the
     closest fit above target, minimizing re-encode work).
   - **Tier 2** (fallback, used only if Tier 1 is empty) — everything else, sorted
     descending as today. Covers sources that top out below 480p — take the best available
     rather than upscale.
3. Same `n_parts` × candidate outer loop as today, walking the new tier-ordered list.
4. **Merge step** — since real height is already known from the probe:
   - height `<= max_res` → keep today's fast `-c:v copy` path (no re-encode, no quality
     loss).
   - height `> max_res` → swap in `-vf scale=-2:{max_res} -c:v libx264` to downscale.
     Only candidates that actually need it pay the re-encode cost.
5. `check_download_adaptive` gains a `max_res: int = settings.max_video_resolution`
   parameter threaded down from `handle_youtube_video`'s call site.

### Social (`bot/util/social/download.py::download_social_video`)

yt-dlp's `worst[height>=480]` selector does the "smallest stream that still meets the
floor" natively:

```
f"worstvideo[ext=mp4][height>={max_res}]+bestaudio[ext=m4a]/worst[ext=mp4][height>={max_res}]/best[ext=mp4]/best"
```

with a final unconstrained `/best` fallback for sources with no 480p+ tier (no upscale
attempted). Add `scale=-2:'min({max_res},ih)'` to the existing `postprocessor_args` merger
filter chain — since that step already force-re-encodes every video for iOS compatibility
today (there is no copy-path to lose), this is a no-op filter on an already-mandatory
encode, not a new cost.

## C. YouTube "🎵 Get audio" button

**Flow:** video processing and delivery stay fully automatic (no added friction on the
common path). A follow-up message with an inline button offers the audio-only version of
any YouTube video just sent.

- **Cache key simplification (prerequisite):** change `YouTubeVideoData.cache_key` from
  `yt:{video_id}:{translated_lang or target_lang}` to `yt:{video_id}`. `target_lang` and
  `translated_lang` stay as stored fields on the cached JSON; embedding them in the key
  today only exists to avoid confusing an original-language cache hit with a translated
  one, but since target_lang resolution now depends on the (globally-off-by-default)
  translation flag, a single key per video is simpler and lets the audio-button callback
  (which only has the video ID) look up the cache directly. Caveat: if the translation
  flag is ever turned back on, a video already cached as `ORIGINAL` from the flag-off
  period will keep serving that cached (untranslated) copy to later requesters until the
  cache entry expires/evicts, even for users whose `target_lang` would now trigger
  translation. Acceptable given the flag defaults off and this is a one-time transition
  edge case, not steady-state behavior.
- **Delivery — one shared code path:** add the follow-up message with the inline button
  to `YouTubeVideoData.reply_to()` and `.send_to_chat()` in `schema.py`, the two methods
  every delivery path (fresh processing and cache-hit replay) already funnels through.
- **Why a separate message, not `reply_markup` on the video itself:** multi-part videos
  deliver via `bot.send_media_group`, which the Telegram Bot API does not allow
  `reply_markup` on. A small standalone follow-up message with the button works uniformly
  whether the video was one part or several.
- **Callback data:** `aud:{video_id}` (YouTube video IDs are 11 chars, well under the
  64-byte `callback_data` limit).
- **New field on `YouTubeVideoData`:** `audio_file_id: str | None = None`, persisted in
  the same `yt:{video_id}` cache entry as the video — no separate cache namespace.
- **New callback handler** (`bot/handlers.py`) on `aud:{video_id}`:
  1. Ack the callback immediately (`answer()`).
  2. Look up the cached `YouTubeVideoData` via `yt:{video_id}`.
  3. If `audio_file_id` is already set, send it straight from cache (`bot.send_audio`,
     instant, no worker round-trip).
  4. Otherwise, register a waiter and enqueue a new Dramatiq actor
     `process_youtube_audio(chat_id, video_id, reply_to_message_id)` — reusing the same
     `register_waiter`/`pop_waiters` + Redis `Lock`/`HeartbeatLock` machinery
     `_process_youtube_link_async` already uses for the video pipeline, keyed off the
     `yt:{video_id}` cache key. This dedupes concurrent clicks on the same video's button
     (e.g. several chat members clicking around the same time) into a single extraction
     job that fans out to all waiters on completion, instead of enqueuing duplicate work.
     The actor:
     - Reuses `get_audio_stream()` unmodified — this automatically respects the
       translation flag/`target_lang` from Section A, no new translation logic needed.
     - Uploads once to `settings.dump_chat_id` to mint a stable `file_id` (same pattern as
       video upload).
     - Updates the cached `YouTubeVideoData.audio_file_id` and re-saves it to Redis.
     - Delivers via `bot.send_audio(performer=video.yt.author, title=video.yt.title,
       duration=video.yt.length)` to every waiter.

## Out of scope

- Audio-only support for Instagram/TikTok/Twitter (YouTube only, per explicit scoping
  decision).
- Runtime/admin toggling of the translation flag (env var only, requires restart).
- Refreshing `CLAUDE.md`'s architecture section (tracked separately).
