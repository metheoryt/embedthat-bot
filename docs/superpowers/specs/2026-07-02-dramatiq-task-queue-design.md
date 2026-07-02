# Dramatiq task queue for video processing

## Context

The bot currently runs as a single `aiogram` polling process. Heavy pipeline
work (yt-dlp/pytubefix download, Whisper language detection, vot-cli
translation, ffmpeg merge/split, Telegram upload) runs inline inside the
message handler, offloaded from the event loop only via `asyncio.to_thread`.
A `redis.asyncio.lock.Lock` around each cache key prevents duplicate
concurrent processing of the same link.

That lock had a bug (fixed separately, see commit `4492803`): its TTL was
static with no renewal, so jobs longer than the TTL caused
`LockNotOwnedError` on release and could let a second request duplicate the
work. The fix (`HeartbeatLock` in `bot/util/redis_lock.py`) periodically
reacquires the lock while held, and is reused unchanged by this design.

Separately from that bug, four problems remain that this design addresses:

- **No crash/restart durability** — if the bot process dies or is redeployed
  mid-download, the in-flight job is lost with no record to resume or retry.
- **No scaling beyond one process** — all downloads/encodes/translations
  share one asyncio loop; there's no way to run concurrent workers.
- **Coupling to the polling loop** — long-running downloads share a
  process/lifecycle with Telegram message dispatch, so a bot restart
  interrupts jobs.
- **Hand-rolled retries** — the same `for i in range(3): try/except/sleep(2)`
  pattern is duplicated four times across `handlers.py`, with no
  observability into failures.

Deployment target: a single VPS running `docker compose`, low/bursty
concurrent load (per user, current volume doesn't require multi-host
scaling, but the option should exist).

## Decision

Introduce **Dramatiq** with the **existing Redis instance as broker**.
Rejected alternatives:

- **Celery + Redis + Flower** — more mature tooling and a richer dashboard,
  but a heavier config surface for this project's size. Has the identical
  "visibility timeout must exceed worst-case job duration" gotcha under a
  different name, so it doesn't sidestep the risk class this design has to
  handle anyway.
- **No queue, just DRY up retry loops** — cheapest option, but doesn't touch
  durability, scaling, or decoupling, all of which were explicitly wanted.

**Observability — amended after spec approval.** The original decision here
was `dramatiq-dashboard`, but dependency verification during plan-writing
found its latest release (0.4.0, PyPI) hard-pins `dramatiq[redis]<2.0` and
`redis<5.0` — incompatible with the `dramatiq==2.2.0` this design already
verified its `TimeLimit`/`Retries` findings against, and with this project's
existing `redis[hiredis]>=5.2.1`. The package's last release predates
dramatiq 2.x, indicating it's unmaintained. Installing it alongside dramatiq
2.x breaks `uv sync` outright.

Options considered: downgrade dramatiq and redis-py to satisfy it (rejected —
downgrades a core dependency `HeartbeatLock` relies on, for a dashboard, and
pins the project to an abandoned dramatiq major); install an unofficial
GitHub fork (rejected — unpublished, unpinned, unvetted dependency in a bot
that holds a Telegram token). **Decision: drop the web dashboard.**
Observability instead extends the existing `/stats` admin command pattern
(`bot/handlers.py:cmd_stats`, `bot/util/stats.py`) with a queue-depth /
failed-count report read directly from the broker's Redis keys — no new
dependency, no new attack surface.

## Architecture

Two services built from the **same Docker image** (same `Dockerfile`, only
the `CMD` differs):

- `bot` — unchanged aiogram polling process.
- `worker` — new; runs `dramatiq` against the actor module(s) described
  below.

Redis keeps its two current roles (video-metadata cache, `HeartbeatLock`
backing store) and gains a third: Dramatiq's message broker. Dramatiq's
Redis broker uses its own key namespace (`dramatiq:*` by default), so no
collision with the app's `dl:...` / `...:lock` keys.

Queue observability is folded into the existing `/stats` admin command
(`bot/handlers.py:cmd_stats`) rather than a separate dashboard service — see
"Observability" under Decision above.

## Components

**Actors** (new module, `bot/worker/actors.py`) replace the inline
heavy-lifting currently called from `embed_youtube_videos` /
`_process_social_url`:

- `process_youtube_link(chat_id, chat_type, reply_to_message_id, ack_message_id, link, target_lang)`
- `process_social_link(chat_id, chat_type, reply_to_message_id, ack_message_id, url)`

Only primitives are passed in — not the aiogram `Message` object, which
isn't meaningfully reconstructable across a process boundary. Each actor
replies via its **own standalone `Bot(token=...)` instance** (`aiogram.Bot`
doesn't require the `Dispatcher`/polling loop), so no IPC back to the bot
process is needed to deliver results.

**Async bridging.** Dramatiq actors are synchronous; the existing pipeline
code (`redis.asyncio`, aiogram's async `Bot`) is not. Each actor wraps its
async implementation with `asyncio.run(...)`:

```python
@with_chat_action()
async def _process_youtube_link_async(bot: Bot, chat_id: int, chat_type: str, reply_to_message_id: int, ack_message_id: int, link: str, target_lang: str):
    ...  # today's handle_youtube_video body, using HeartbeatLock as now,
    ...  # plus waiters-list fan-out on completion — see Data flow below

@dramatiq.actor(
    max_retries=2,
    min_backoff=30_000,
    max_backoff=5 * 60_000,
    time_limit=45 * 60_000,
    throws=(YouTubeError,),
)
def process_youtube_link(chat_id, chat_type, reply_to_message_id, ack_message_id, link, target_lang):
    bot = Bot(token=settings.bot_token)
    try:
        asyncio.run(_process_youtube_link_async(bot, chat_id, chat_type, reply_to_message_id, ack_message_id, link, target_lang))
    finally:
        asyncio.run(bot.session.close())
```

`process_social_link` mirrors this with `throws=(SocialDownloadError,)` and
a shorter `time_limit` (~25 min).

**`HeartbeatLock` is reused unchanged for the actual processing** — same
dedup lock built for the prior fix, guarding the download/translate/merge
pipeline exactly as it does today, just invoked from inside the actor
instead of the aiogram handler. Coordinating *concurrent duplicate
requests* is a separate concern, handled by the waiters list below — see
"Deduplication across the handler/worker split" under Data flow.

**Chat-action wrapper.** A small decorator (`with_chat_action`, e.g. in
`bot/worker/chat_action.py`) reuses the existing
`send_chat_action_periodically` helper from `bot/util/chat_action.py`,
sharing the actor's own event loop (no second event loop needed, unlike a
Dramatiq middleware approach — see Alternatives Considered below):

```python
def with_chat_action(action: ChatAction = ChatAction.UPLOAD_VIDEO):
    def decorator(func):
        @wraps(func)
        async def wrapper(bot: Bot, chat_id: int, *args, **kwargs):
            action_task = await send_chat_action_periodically(bot, chat_id, action)
            try:
                return await func(bot, chat_id, *args, **kwargs)
            finally:
                action_task.cancel()
                try:
                    await action_task
                except asyncio.CancelledError:
                    pass
        return wrapper
    return decorator
```

### Alternatives considered for the chat-action wrapper

A Dramatiq **middleware** (`before_process_message`/`after_process_message`)
was considered and rejected: it runs around the actor call but does not
share the actor's `asyncio.run()` event loop, so it would need a second,
independent event loop just to ping chat actions. It also has no clean way
to obtain `chat_id` except by convention (e.g. "always the first positional
arg"), which is fragile. The decorator shares the actor's event loop for
free and takes `chat_id` as an explicit parameter.

## Data flow

**Cache hit (common case) — unchanged.** The aiogram handler does the Redis
`GET` inline and replies immediately if there's a hit. No queue involvement.

### Deduplication across the handler/worker split

The cache key (`yt:{video_id}:{lang}` / `dl:{link_hash}`) is **global**, not
per-chat — the same link can legitimately be posted to several different
chats at once, and today's single blocking `Lock` makes every one of them
wait, then serves all of them from cache once the first finishes. Moving
the heavy work into a fire-and-forget `actor.send()` removes the natural
place that used to make later requests wait — so a fast, cross-process
mechanism is needed to (a) avoid double-processing the same link and (b)
still deliver the result to every chat that asked for it, not just the
first.

**Design: a per-cache-key "waiters" list in Redis**, keyed
`{cache_key}:waiters`, holding one JSON entry per pending request:
`{chat_id, chat_type, reply_to_message_id, ack_message_id}`.

**Cache miss:**

1. Handler computes `video.cache_key`, sends a "processing…" ack
   (`message.reply(...)`), and does `RPUSH {cache_key}:waiters <entry>`
   followed by `EXPIRE {cache_key}:waiters <ttl>` (TTL generous relative to
   the actor's worst-case retry budget — ~3h for YouTube, ~1.5h for social).
   `RPUSH` is atomic and returns the list's new length.
2. **Only if the returned length is 1** (this handler was first) does it
   also call `process_youtube_link.send(...)` / `process_social_link.send(...)`.
   Every other concurrent requester for the same key — same chat or a
   different one — still registers a waiter and gets a normal "processing…"
   ack, but does not enqueue a second job.
3. A worker picks up the job, acquires `HeartbeatLock` for the cache key
   (unchanged from the prior fix), and runs the pipeline.
4. On success: the actor writes the cache entry, then atomically reads and
   deletes `{cache_key}:waiters` (`LRANGE` + `DELETE`), and for **every**
   waiter — not just the one that triggered the job — deletes that waiter's
   ack message and sends the result to that waiter's chat, via its own
   `Bot`. It fires `on_yt_video_sent` once per waiter (`fresh=True`).
5. On an unrecoverable error (`YouTubeError` / `SocialDownloadError`,
   single attempt, no retry): same fan-out, but each waiter's ack is edited
   to a failure message instead of deleted. See Error handling.
6. On a transient error that dramatiq will retry: the waiters list is left
   untouched (a later attempt, or eventual success, still needs it) and the
   exception is simply re-raised.

**Accepted gap:** if a job exhausts all of dramatiq's retries (three
consecutive failures on the same in-flight link — rare), only the
originally-enqueuing request is guaranteed a clear failure outcome; other
waiters that piggybacked on the same in-flight job keep a stale
"processing…" ack rather than an explicit failure notice. Closing this
fully would need a companion actor wired via dramatiq's
`on_retry_exhausted` actor option to fan out the failure on final
exhaustion too — deliberately out of scope for this iteration (see Out of
scope).

## Error handling

Verified against installed `dramatiq==2.2.0` source rather than assumed —
two defaults would have reintroduced the same class of bug just fixed for
the Redis lock:

- **`TimeLimit` middleware defaults to `time_limit=600_000` ms (10 min)** —
  a static per-actor timeout that force-kills the actor thread (via async
  exception injection) once exceeded, regardless of whether the worker
  process is healthy. Left at default, this would kill the YouTube/social
  actors mid-pipeline on any video whose processing (especially with
  translation) exceeds 10 minutes. **Must be explicitly overridden per
  actor** — e.g. `time_limit=45*60_000` for YouTube, `~25*60_000` for
  social.
- **`Retries` middleware defaults to `max_retries=20`.** Since a retry here
  re-runs the *entire* pipeline (re-download, re-translate, re-upload), the
  default is dangerous for a persistently-failing video. Override to a low
  value (e.g. `max_retries=2`) with tightened backoff (`min_backoff`,
  `max_backoff`) — defaults allow up to 7 days of backoff, far beyond what's
  sane here.
- **Unrecoverable errors are excluded from retry** via
  `throws=(YouTubeError,)` / `throws=(SocialDownloadError,)` — verified in
  `Retries.after_process_message` that a `throws`-matched exception marks
  the message failed without retrying. The actor body still catches the
  exception itself first (to fire `on_yt_video_fail`/`on_social_video_fail`
  and reply to the user), then re-raises so `throws` sees it — mirroring
  today's `except SocialDownloadError: raise` pattern in
  `handle_social_video`.
- **Fine-grained retry loops are unchanged** — the existing 3x/2s-sleep
  loops around a single stream download or a single Telegram upload stay
  exactly as they are. Dramatiq's actor-level retry is the outer safety net
  for catastrophic failure only (worker crash, unhandled exception); the two
  layers shouldn't overlap in purpose.
- **`RedisBroker`'s `heartbeat_timeout` (default 60s) does not need
  tuning** — confirmed via the broker's docstring that it governs
  worker-*process* liveness (is this worker still alive), not a per-message
  visibility timeout like Celery's. A live worker running a long actor is
  unaffected by it.

## Testing

No automated test suite exists in this repo (per `CLAUDE.md`); this design
doesn't change that baseline. Manual verification before considering this
done, using the local Redis instance:

1. Cache-hit path still replies synchronously, no worker involvement.
2. Cache-miss path end-to-end: enqueue → worker picks up → `HeartbeatLock`
   held correctly → chat-action pings fire → result sent via worker's `Bot`
   → cache written.
3. Worker crash mid-job: kill the worker process partway through a job,
   confirm the broker's heartbeat-based requeue picks it up on a restarted
   worker instead of losing it silently.
4. Unrecoverable error path: force a `YouTubeError`/`SocialDownloadError`,
   confirm it fails fast (no retries burned) and the user still gets a clear
   failure message via the existing signal.
5. Long-job survival: temporarily lower `time_limit` against a sleep to
   confirm the override prevents the force-kill that the 10-minute default
   would otherwise cause, then revert. Directly re-validates the finding in
   Error Handling.
6. `/stats` admin command sanity check: confirm the queue-depth / failed-count
   report reflects the success/failure states exercised by the above steps.

## Out of scope

Explicitly deferred to their own design passes (raised mid-session, parked
per user decision to finish this design first):

- Feature-flagging the video translator off.
- A new "paste a link, get music" feature for Yandex Music / SoundCloud
  (verified via `yt-dlp --list-extractors` on the installed 2026.06.09
  build: Yandex Music and SoundCloud both have working extractors; Spotify
  has none — DRM-protected, no legitimate extraction path — and VK has only
  video extractors, no music/audio extractor).
- The `on_retry_exhausted` companion actor that would notify *every*
  waiting chat on full retry exhaustion, not just the originally-enqueuing
  one (see "Accepted gap" under Data flow) — a deliberate scope cut for a
  rare edge case, revisit if it turns out to matter in practice.
