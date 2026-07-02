# Dramatiq Task Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move YouTube/social video processing off the aiogram polling process and onto Dramatiq worker(s), so jobs survive bot restarts, can scale beyond one process, and get real retry/observability instead of hand-rolled `for i in range(3)` loops.

**Architecture:** A new `bot/worker/` package holds a Dramatiq `RedisBroker` (reusing the existing Redis instance), two actors (`process_youtube_link`, `process_social_link`), and the download/upload pipeline they call. The aiogram handler still does the fast Redis cache-check inline; on a cache miss it registers itself in a per-cache-key "waiters" list and enqueues a job only if it's the first waiter, then returns immediately. The worker fans the result (or an unrecoverable failure) out to every registered waiter when the job resolves — this is what makes the same link posted to N different chats at once still work correctly without a shared blocking lock across the process boundary.

**Tech Stack:** Dramatiq 2.x (`dramatiq[redis]`), the existing `redis[hiredis]` async client for app state, aiogram 3.x, Pydantic.

## Global Constraints

- Pin `dramatiq[redis]>=2.2.0,<3.0.0`. The Error Handling section of the spec
  (`docs/superpowers/specs/2026-07-02-dramatiq-task-queue-design.md`) verified
  `TimeLimit`/`Retries` middleware defaults and the `actor_options` override
  mechanism against `dramatiq==2.2.0` source directly — re-verify those before
  ever bumping past `<3.0.0`.
- No `dramatiq-dashboard` dependency — it hard-pins `dramatiq[redis]<2.0` and
  `redis<5.0`, incompatible with this project (see spec's amended Decision
  section). Observability goes through the `/stats` admin command instead.
- Actors receive **only JSON-serializable primitives** (`int`, `str`) as
  arguments — never an aiogram `Message` or `Bot` object; those don't survive
  a process boundary. Each actor builds its own `Bot(token=...)` instance.
- Every verification script in this plan imports `bot.*` modules, which
  requires a valid local `.env` (`BOT_TOKEN`, `DUMP_CHAT_ID` at minimum, per
  `bot/config.py`) and a reachable Redis (`REDIS_URL`, default
  `redis://redis` — override to `redis://localhost:6379` for local runs
  outside `docker compose`, matching how the prior `HeartbeatLock` fix in
  this repo was verified).
- Dramatiq's default worker CLI flags (`--processes 20 --threads 8`) are
  sized for a big server — always pass `--processes 1 --threads 4` explicitly
  for this deployment. `bot/util/youtube/translate.py` keeps its Whisper
  model in a per-process global (`_whisper_model`); multiple worker
  *processes* would each load a separate copy into memory, while threads
  within one process share it safely.
- **Amended after Task 7 implementation:** actors must never import
  `bot.util.redis.redis_client` directly. That module-level singleton is
  constructed once at import time and is only safe under the aiogram
  process's single, persistent event loop. Each `@dramatiq.actor`-decorated
  function bridges to async code via a fresh `asyncio.run(...)` call per
  invocation — a new event loop every time — and redis-py's async connection
  pool does not detect or recover from being handed a connection whose
  transport belongs to an already-closed loop. Reusing the shared singleton
  there fails deterministically on a worker's *second* job with `RuntimeError:
  Event loop is closed` (or, off Windows, "attached to a different loop").
  Verified directly with a 4-line repro (`asyncio.run(ping()); asyncio.run(ping())`
  against the shared client — the second call fails). Fix: each actor's async
  work function constructs its own short-lived `redis.asyncio.Redis` client
  (`redis.asyncio.from_url(str(settings.redis_dsn), decode_responses=True)`)
  as its first statement and closes it (`await redis_client.aclose()`) in a
  `finally` block before returning — scoped entirely to that invocation's own
  event loop. This does not affect `bot/util/redis.py`, `bot/handlers.py`, or
  `bot/util/stats.py` (Task 9) — all of those run inside the aiogram process's
  one persistent event loop, where the shared singleton remains correct and
  is not changed by this amendment.
- **Amended after Task 7 verification:** the same event-loop hazard also
  hits `bot/events/handlers/stats.py`'s signal receivers for
  `on_yt_video_sent`, `on_social_video_sent`, `on_yt_video_fail`, and
  `on_social_video_fail` — these are fired both from the aiogram process
  (cache-hit path, `fresh=False`) and from Task 7's worker actors
  (cache-miss fan-out, `fresh=True`), so they inherit the worker's
  per-job `asyncio.run()` loop churn. Reproduced directly: a two-job
  sequential run against a real `Worker` failed on job 2 with the same
  `RuntimeError: ... attached to a different loop`, raised from inside
  `stats_yt_sent`'s call to the shared `redis_client` singleton — not
  from `actors.py` itself, which was already fixed. Fix: those four
  handlers each open their own short-lived `redis.asyncio.Redis` client
  (same `from_url(...)` / `aclose()` pattern as the prior amendment) instead
  of importing the shared singleton; `_incr`/`_sadd` were refactored to take
  the client as an explicit parameter. `stats_link_received` (fired only
  from the aiogram process via `on_link_received`) is unaffected and still
  uses the shared singleton. This does not change the prior amendment's
  claim about `bot/util/stats.py` (Task 9) — that module's
  `build_stats_report()` is only ever called from the aiogram process's
  `/stats` admin command, a genuinely different code path from these signal
  receivers.

---

### Task 1: Dramatiq dependency + Redis broker module

**Files:**
- Modify: `pyproject.toml`
- Create: `bot/worker/__init__.py`
- Create: `bot/worker/broker.py`

**Interfaces:**
- Produces: `bot.worker.broker.broker` — a `dramatiq.brokers.redis.RedisBroker`
  instance, already installed as the global broker via `dramatiq.set_broker()`
  at import time. Every other module in `bot/worker/` must import this module
  (directly or transitively) before declaring any `@dramatiq.actor`.

- [ ] **Step 1: Add the dependency**

Edit `pyproject.toml`'s `dependencies` list to insert, alphabetically after
`"aiosignal>=1.3.2",` and before `"faster-whisper>=1.2.0",`:

```toml
    "dramatiq[redis]>=2.2.0,<3.0.0",
```

- [ ] **Step 2: Sync and verify the import**

Run: `uv sync`

Run: `uv run python -c "import dramatiq; from dramatiq.brokers.redis import RedisBroker; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Create `bot/worker/__init__.py`**

Empty file — makes `bot.worker` a package.

- [ ] **Step 4: Create `bot/worker/broker.py`**

```python
import dramatiq
from dramatiq.brokers.redis import RedisBroker

from bot.config import settings

broker = RedisBroker(url=str(settings.redis_dsn))
dramatiq.set_broker(broker)
```

No custom `middleware=` list is passed — that would *replace* dramatiq's
default middleware stack (`AgeLimit`, `TimeLimit`, `ShutdownNotifications`,
`Callbacks`, `Pipelines`, `Retries`) wholesale instead of extending it.
`time_limit`/`max_retries`/etc. are overridden per-actor via
`@dramatiq.actor(...)` options instead (Task 7), which the default
`TimeLimit`/`Retries` middleware instances read per-message.

- [ ] **Step 5: Verify a round-trip through local Redis**

Run this script (it defines its own scratch actor, so it doesn't depend on
any later task):

```bash
uv run python -c "
import threading
from bot.worker.broker import broker
import dramatiq
from dramatiq.worker import Worker

done = threading.Event()
received = []

@dramatiq.actor(max_retries=0)
def scratch_ping(x):
    received.append(x)
    done.set()

worker = Worker(broker, worker_threads=1)
worker.start()
try:
    scratch_ping.send(42)
    ok = done.wait(timeout=5)
    print('received:', received, 'ok:', ok)
finally:
    worker.stop()
    worker.join()
"
```

Expected: `received: [42] ok: True`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock bot/worker/__init__.py bot/worker/broker.py
git commit -m "Add Dramatiq dependency and Redis broker module"
```

---

### Task 2: Chat-action decorator for actors

**Files:**
- Create: `bot/worker/chat_action.py`

**Interfaces:**
- Consumes: `bot.util.chat_action.send_chat_action_periodically(bot, chat_id, action)`
  (existing, unchanged).
- Produces: `bot.worker.chat_action.with_chat_action(action=ChatAction.UPLOAD_VIDEO)`
  — a decorator for `async def f(bot: Bot, chat_id: int, *args, **kwargs)`
  functions. Task 7's actor wrapper functions use this.

- [ ] **Step 1: Create `bot/worker/chat_action.py`**

```python
import asyncio
from functools import wraps

from aiogram import Bot
from aiogram.enums import ChatAction

from bot.util.chat_action import send_chat_action_periodically


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

- [ ] **Step 2: Verify with a stub bot**

```bash
uv run python -c "
import asyncio
from bot.worker.chat_action import with_chat_action

class StubBot:
    def __init__(self):
        self.calls = []
    async def send_chat_action(self, chat_id, action):
        self.calls.append((chat_id, action))

@with_chat_action()
async def fake_job(bot, chat_id):
    await asyncio.sleep(9)
    return 'done'

async def main():
    bot = StubBot()
    result = await fake_job(bot, 123)
    print('result:', result)
    print('pings:', len(bot.calls))

asyncio.run(main())
"
```

Expected: `result: done` and `pings:` at least `2` (pings fire every 4
seconds; a 9-second job should see pings at roughly t=0 and t=4, possibly
t=8).

- [ ] **Step 3: Commit**

```bash
git add bot/worker/chat_action.py
git commit -m "Add chat-action decorator for Dramatiq actors"
```

---

### Task 3: Waiters list (cross-process dedup + fan-out)

**Files:**
- Create: `bot/worker/waiters.py`

**Interfaces:**
- Produces: `Waiter` (Pydantic model: `chat_id: int`, `chat_type: str`,
  `reply_to_message_id: int`, `ack_message_id: int`);
  `register_waiter(redis_client, cache_key: str, waiter: Waiter, ttl: int) -> bool`
  (returns `True` iff this waiter is the first for `cache_key` — caller
  should enqueue the job only in that case);
  `pop_waiters(redis_client, cache_key: str) -> list[Waiter]` (atomically
  reads and clears all waiters for `cache_key`).
- Consumed by: Task 8 (`bot/handlers.py`, calls `register_waiter`) and
  Task 7 (`bot/worker/actors.py`, calls `pop_waiters`).

- [ ] **Step 1: Create `bot/worker/waiters.py`**

```python
from pydantic import BaseModel
from redis.asyncio import Redis


class Waiter(BaseModel):
    chat_id: int
    chat_type: str
    reply_to_message_id: int
    ack_message_id: int


def _waiters_key(cache_key: str) -> str:
    return f"{cache_key}:waiters"


async def register_waiter(redis_client: Redis, cache_key: str, waiter: Waiter, ttl: int) -> bool:
    """
    Registers a waiter for the given cache key. Returns True if this waiter
    is the first one registered (the caller should enqueue the processing
    job in that case); False if a job for this cache key is already in
    flight (the caller should not enqueue a second one).
    """
    key = _waiters_key(cache_key)
    length = await redis_client.rpush(key, waiter.model_dump_json())
    await redis_client.expire(key, ttl)
    return length == 1


async def pop_waiters(redis_client: Redis, cache_key: str) -> list[Waiter]:
    """Atomically reads and clears all waiters registered for the given cache key."""
    key = _waiters_key(cache_key)
    async with redis_client.pipeline(transaction=True) as pipe:
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        raw_entries, _ = await pipe.execute()
    return [Waiter.model_validate_json(raw) for raw in raw_entries]
```

- [ ] **Step 2: Verify first-wins semantics and atomic pop against local Redis**

```bash
uv run python -c "
import asyncio
from bot.util.redis import redis_client
from bot.worker.waiters import Waiter, register_waiter, pop_waiters

async def main():
    key = 'test:waiters:scratch'
    await redis_client.delete(f'{key}:waiters')

    w1 = Waiter(chat_id=1, chat_type='private', reply_to_message_id=10, ack_message_id=11)
    w2 = Waiter(chat_id=2, chat_type='group', reply_to_message_id=20, ack_message_id=21)

    first = await register_waiter(redis_client, key, w1, ttl=60)
    second = await register_waiter(redis_client, key, w2, ttl=60)
    print('first is_first:', first, 'second is_first:', second)

    waiters = await pop_waiters(redis_client, key)
    print('popped:', [(w.chat_id, w.ack_message_id) for w in waiters])

    empty = await pop_waiters(redis_client, key)
    print('popped again (should be empty):', empty)

asyncio.run(main())
"
```

Expected: `first is_first: True second is_first: False`, then
`popped: [(1, 11), (2, 21)]`, then `popped again (should be empty): []`

- [ ] **Step 3: Commit**

```bash
git add bot/worker/waiters.py
git commit -m "Add Redis-backed waiters list for cross-process job dedup and fan-out"
```

---

### Task 4: Decouple post-processing signals from aiogram `Message`

The `on_yt_video_sent`/`on_social_video_sent` signals currently take a full
`Message` object, which won't exist in the worker process. Their only real
uses are `message.chat.type` and `message.bot` (in `share.py`); nothing
reads any other `Message` field. The `*_fail` signals don't use `message`
at all — dropping it outright there.

**Files:**
- Modify: `bot/events/signals.py`
- Modify: `bot/events/handlers/share.py`
- Modify: `bot/events/handlers/stats.py`

**Interfaces:**
- Produces: `on_yt_video_sent.send(link, chat_id, chat_type, bot, video, fresh)`,
  `on_social_video_sent.send(link, chat_id, chat_type, bot, video, fresh)`,
  `on_yt_video_fail.send(link)`, `on_social_video_fail.send(link)`.
- Note: `bot/handlers.py` (Task 8) and `bot/worker/pipeline.py` (Task 6)
  still call these with the *old* signature until those tasks land — this
  task only changes the signal definitions and their registered handlers,
  which is independently testable by invoking the signals directly (Step 4).

- [ ] **Step 1: Update `bot/events/signals.py`**

```python
from aiosignal import Signal


def signal_handler(signal: Signal):
    def decorator(func):
        signal.append(func)
        return func

    return decorator


def freeze_signals():
    for sig in [
        on_link_received, on_link_sent,
        on_yt_video_sent, on_yt_video_fail,
        on_social_video_sent, on_social_video_fail,
    ]:
        sig.freeze()


on_yt_video_sent = Signal(
    "on_yt_video_sent(link: str, chat_id: int, chat_type: str, bot: Bot, video: YouTubeVideoData, fresh: bool)"
)
on_yt_video_fail = Signal(
    "on_yt_video_fail(link: str)"
)
on_social_video_sent = Signal(
    "on_social_video_sent(link: str, chat_id: int, chat_type: str, bot: Bot, video: SocialVideoData, fresh: bool)"
)
on_social_video_fail = Signal(
    "on_social_video_fail(link: str)"
)
on_link_sent = Signal("on_link_sent(link: str, message: Message, origin: LinkOrigin)")
on_link_received = Signal("on_link_received(message: Message, origin: LinkOrigin)")
```

- [ ] **Step 2: Update `bot/events/handlers/share.py`**

```python
from aiogram import Bot

from bot.config import settings
from bot.events.signals import signal_handler, on_yt_video_sent, on_social_video_sent
from bot.util.social.schema import SocialVideoData
from bot.util.youtube.schema import YouTubeVideoData


@signal_handler(on_social_video_sent)
async def share_social_video(link: str, chat_id: int, chat_type: str, bot: Bot, video: SocialVideoData, fresh: bool):
    """Share fresh TikTok/Instagram downloads to the feed channel."""
    if not settings.feed_channel_id or chat_type == "private" or not fresh:
        return
    await video.send_to_chat(bot, settings.feed_channel_id)


@signal_handler(on_yt_video_sent)
async def share_yt_shorts(link: str, chat_id: int, chat_type: str, bot: Bot, video: YouTubeVideoData, fresh: bool):
    """Share successfully downloaded YouTube shorts to a feed channel."""
    if settings.feed_channel_id and chat_type != "private" and fresh and 'shorts' in link:
        await video.send_to_chat(bot, settings.feed_channel_id)
```

- [ ] **Step 3: Update `bot/events/handlers/stats.py`**

```python
from bot.events.signals import (
    on_link_received,
    on_yt_video_sent,
    on_social_video_sent,
    on_yt_video_fail,
    on_social_video_fail,
    signal_handler,
)
from bot.util.redis import redis_client
from bot.config import settings

_TTL = 90 * 24 * 3600  # 90 days


def _today() -> str:
    return settings.now().strftime("%Y-%m-%d")


async def _incr(key: str) -> None:
    await redis_client.incr(key)
    await redis_client.expire(key, _TTL)


async def _sadd(key: str, value: str) -> None:
    await redis_client.sadd(key, value)
    await redis_client.expire(key, _TTL)


@signal_handler(on_link_received)
async def stats_link_received(message, origin):
    if not message.from_user:
        return
    d = _today()
    lang = (message.from_user.language_code or "unknown").lower()
    await _incr(f"stats:{d}:requests")
    await _sadd(f"stats:{d}:users", str(message.from_user.id))
    await _incr(f"stats:{d}:chat:{message.chat.type}")
    await _incr(f"stats:{d}:lang:{lang}")


@signal_handler(on_yt_video_sent)
async def stats_yt_sent(link, chat_id, chat_type, bot, video, fresh):
    await _incr(f"stats:{_today()}:success:youtube")


@signal_handler(on_social_video_sent)
async def stats_social_sent(link, chat_id, chat_type, bot, video, fresh):
    platform = (video.origin or "social").lower()
    await _incr(f"stats:{_today()}:success:{platform}")


@signal_handler(on_yt_video_fail)
async def stats_yt_fail(link):
    await _incr(f"stats:{_today()}:fail:youtube")


@signal_handler(on_social_video_fail)
async def stats_social_fail(link):
    await _incr(f"stats:{_today()}:fail:social")
```

(`on_link_received`'s handler is unaffected — that signal still fires from
the handler process with a real `Message`, unchanged by this task.)

- [ ] **Step 4: Verify by firing the signals directly**

```bash
uv run python -c "
import asyncio
from bot.util.redis import redis_client
from bot.config import settings
import bot.events  # registers all signal handlers
from bot.events import freeze_signals, on_yt_video_sent, on_yt_video_fail
from bot.util.youtube.schema import YouTubeVideoData

async def main():
    freeze_signals()
    d = settings.now().strftime('%Y-%m-%d')
    await redis_client.delete(f'stats:{d}:success:youtube', f'stats:{d}:fail:youtube')

    video = YouTubeVideoData.model_validate(dict(link='https://youtu.be/dQw4w9WgXcQ'))
    video.file_ids = ['stub_file_id']

    class StubBot:
        pass

    await on_yt_video_sent.send('https://youtu.be/dQw4w9WgXcQ', 123, 'private', StubBot(), video, True)
    await on_yt_video_fail.send('https://youtu.be/dQw4w9WgXcQ')

    sent = await redis_client.get(f'stats:{d}:success:youtube')
    failed = await redis_client.get(f'stats:{d}:fail:youtube')
    print('success:', sent, 'fail:', failed)

asyncio.run(main())
"
```

Expected: `success: 1 fail: 1`, no exceptions raised (confirms `share.py`'s
handlers tolerate the new signature even with `settings.feed_channel_id`
unset — they just no-op).

- [ ] **Step 5: Commit**

```bash
git add bot/events/signals.py bot/events/handlers/share.py bot/events/handlers/stats.py
git commit -m "Decouple post-processing signals from aiogram Message"
```

---

### Task 5: `reply_to_message_id` support + `cache_key` for social data

**Files:**
- Modify: `bot/util/youtube/schema.py`
- Modify: `bot/util/social/schema.py`

**Interfaces:**
- Produces: `YouTubeVideoData.send_to_chat(bot, chat_id, reply_to_message_id=None)`,
  `SocialVideoData.send_to_chat(bot, chat_id, reply_to_message_id=None)`,
  `SocialVideoData.cache_key` (property, mirrors `YouTubeVideoData.cache_key`).
- Consumed by: Task 7's actor fan-out (passes `reply_to_message_id`) and
  Task 8's handler (uses `SocialVideoData.cache_key`, replacing the current
  module-level `_social_cache_key` helper in `bot/handlers.py`).

- [ ] **Step 1: Update `YouTubeVideoData.send_to_chat` in `bot/util/youtube/schema.py`**

Change:

```python
    async def send_to_chat(self, bot: Bot, chat_id: int):
        if len(self.file_ids) > 1:
            await bot.send_media_group(chat_id, self.media_group)
        else:
            await bot.send_video(chat_id, **self.single_video)
```

to:

```python
    async def send_to_chat(self, bot: Bot, chat_id: int, reply_to_message_id: int | None = None):
        if len(self.file_ids) > 1:
            await bot.send_media_group(chat_id, self.media_group, reply_to_message_id=reply_to_message_id)
        else:
            await bot.send_video(chat_id, **self.single_video, reply_to_message_id=reply_to_message_id)
```

- [ ] **Step 2: Update `SocialVideoData` in `bot/util/social/schema.py`**

Add the import and the `cache_key` property, and update `send_to_chat`:

```python
import hashlib

from aiogram import types, Bot
from pydantic import BaseModel, Field

from bot.config import settings


class SocialVideoData(BaseModel):
    link: str
    origin: str = ""
    file_ids: list[str] = Field(default_factory=list)
    video_id: str | None = None
    width: int | None = None
    height: int | None = None
    title: str | None = None

    @property
    def cache_key(self) -> str:
        return f"dl:{hashlib.sha256(self.link.encode()).hexdigest()[:16]}"

    @property
    def caption(self) -> str:
        return self.link

    async def reply_to(self, message: types.Message) -> None:
        if len(self.file_ids) > 1:
            group = [
                types.InputMediaVideo(
                    media=fid,
                    width=self.width,
                    height=self.height,
                    caption=self.caption if i == 0 else None,
                )
                for i, fid in enumerate(self.file_ids)
            ]
            await message.reply_media_group(group)
        else:
            await message.reply_video(
                video=self.file_ids[0],
                width=self.width,
                height=self.height,
                caption=self.caption,
            )

    async def send_to_chat(self, bot: Bot, chat_id: int, reply_to_message_id: int | None = None) -> None:
        if len(self.file_ids) > 1:
            group = [
                types.InputMediaVideo(
                    media=fid,
                    width=self.width,
                    height=self.height,
                    caption=self.caption if i == 0 else None,
                )
                for i, fid in enumerate(self.file_ids)
            ]
            await bot.send_media_group(chat_id, group, reply_to_message_id=reply_to_message_id)
        else:
            await bot.send_video(
                chat_id,
                video=self.file_ids[0],
                width=self.width,
                height=self.height,
                caption=self.caption,
                reply_to_message_id=reply_to_message_id,
            )
```

- [ ] **Step 3: Verify**

```bash
uv run python -c "
import asyncio
from bot.util.social.schema import SocialVideoData
from bot.util.youtube.schema import YouTubeVideoData

s1 = SocialVideoData(link='https://tiktok.com/@a/video/123')
s2 = SocialVideoData(link='https://tiktok.com/@a/video/123')
s3 = SocialVideoData(link='https://tiktok.com/@a/video/456')
print('cache_key stable:', s1.cache_key == s2.cache_key)
print('cache_key differs by link:', s1.cache_key != s3.cache_key)
print('cache_key format:', s1.cache_key)

class StubBot:
    def __init__(self):
        self.calls = []
    async def send_video(self, chat_id, **kwargs):
        self.calls.append(('send_video', chat_id, kwargs))
    async def send_media_group(self, chat_id, media, **kwargs):
        self.calls.append(('send_media_group', chat_id, kwargs))

async def main():
    video = YouTubeVideoData.model_validate(dict(link='https://youtu.be/dQw4w9WgXcQ'))
    video.file_ids = ['a']
    bot = StubBot()
    await video.send_to_chat(bot, 999, reply_to_message_id=42)
    print('call:', bot.calls[0][0], 'reply_to_message_id' in bot.calls[0][2], bot.calls[0][2].get('reply_to_message_id'))

asyncio.run(main())
"
```

Expected: `cache_key stable: True`, `cache_key differs by link: True`,
`cache_key format: dl:<16 hex chars>`, and
`call: send_video True 42`.

- [ ] **Step 4: Commit**

```bash
git add bot/util/youtube/schema.py bot/util/social/schema.py
git commit -m "Add reply_to_message_id support and cache_key to video data models"
```

---

### Task 6: Worker pipeline module

Moves the download/upload logic out of `bot/handlers.py` and adapts it to
the new shape: no single `chat_id` to deliver to (that's the caller's job
now, per waiter — see Task 7), and no more "send the single-file case
directly to the user" shortcut, since with N waiters there's no longer a
single privileged recipient. Every result now goes through the dump chat
uniformly to obtain `file_ids`, which the caller then fans out.

**Files:**
- Create: `bot/worker/pipeline.py`

**Interfaces:**
- Consumes: `bot.util.youtube.video.{get_resolution, check_download_adaptive, split_video, MAX_FILE_SIZE_BYTES}`,
  `bot.util.social.download.download_social_video`,
  `bot.util.youtube.exc.YouTubeError`, `bot.util.social.exc.SocialDownloadError`,
  `bot.events.signals.{on_yt_video_fail, on_social_video_fail}` (new
  single-arg signature from Task 4).
- Produces: `handle_youtube_video(bot: Bot, video: YouTubeVideoData) -> YouTubeVideoData`
  (raises `YouTubeError` on unrecoverable failure, any other exception on
  exhausted transient retries — never returns partially-populated data on
  failure); `handle_social_video(bot: Bot, video: SocialVideoData) -> SocialVideoData`
  (same contract, raises `SocialDownloadError` for unrecoverable failures).
  Both populate `video.file_ids` (and, for YouTube, `width`/`height`) on
  success; delivery to any chat is the caller's responsibility.
- Consumed by: Task 7 (`bot/worker/actors.py`).

- [ ] **Step 1: Create `bot/worker/pipeline.py`**

```python
import asyncio
import logging
import math
import tempfile
from pathlib import Path

from aiogram import Bot, types
from aiogram.exceptions import TelegramNetworkError

from bot.config import settings
from bot.events.signals import on_yt_video_fail, on_social_video_fail
from bot.util.social.download import download_social_video
from bot.util.social.exc import SocialDownloadError
from bot.util.social.schema import SocialVideoData
from bot.util.youtube.exc import YouTubeError
from bot.util.youtube.schema import YouTubeVideoData
from bot.util.youtube.video import get_resolution, check_download_adaptive, split_video, MAX_FILE_SIZE_BYTES

log = logging.getLogger(__name__)


async def _upload_parts_to_dump_chat(bot: Bot, file_paths: list[Path], width: int, height: int) -> list[str]:
    file_ids = []
    for file_path in file_paths:
        for i in range(3):
            try:
                media_message = await bot.send_video(
                    settings.dump_chat_id,
                    types.FSInputFile(file_path),
                    width=width,
                    height=height,
                )
                break
            except TelegramNetworkError:
                if i == 2:
                    raise
                log.warning('failed to send a video file, retrying in 2 seconds')
                await asyncio.sleep(2)
        log.info("sent %s", file_path)
        file_ids.append(media_message.video.file_id)
    return file_ids


async def handle_youtube_video(bot: Bot, video: YouTubeVideoData) -> YouTubeVideoData:
    with tempfile.TemporaryDirectory() as tmp:
        exc = None
        for i in range(3):
            try:
                stream, file_paths = await asyncio.to_thread(
                    check_download_adaptive,
                    video=video,
                    output_path=tmp,
                )
                exc = None
                break
            except YouTubeError:
                # raise YouTubeError directly (it is an unrecoverable error)
                raise
            except Exception as ex:
                exc = ex
                log.error("failed to download %s on try #%d: %r", video.yt.video_id, i + 1, exc)
                await asyncio.sleep(2)

        if exc:
            log.error("finally failed to download youtube link %s: %r", video.link, exc)
            await on_yt_video_fail.send(video.link)
            raise exc

        width, height = get_resolution(stream)
        video.width = width
        video.height = height

        log.info('sending %d part(s) to dump chat to obtain file ids', len(file_paths))
        video.file_ids = await _upload_parts_to_dump_chat(bot, file_paths, width, height)
        return video


async def handle_social_video(bot: Bot, video: SocialVideoData) -> SocialVideoData:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        exc = None

        for i in range(3):
            try:
                result = await asyncio.to_thread(download_social_video, video.link, tmp_path)
                exc = None
                break
            except SocialDownloadError:
                raise  # unrecoverable — private account, removed video, geo-block
            except Exception as ex:
                exc = ex
                log.error("failed to download social %s on try #%d: %r", video.link, i + 1, exc)
                await asyncio.sleep(2)

        if exc:
            log.error("finally failed to download social link %s: %r", video.link, exc)
            await on_social_video_fail.send(video.link)
            raise exc

        video.video_id = result.video_id
        video.width = result.width
        video.height = result.height
        video.title = result.title
        video.origin = result.extractor.lower()

        file_size = result.file_path.stat().st_size
        if file_size <= MAX_FILE_SIZE_BYTES:
            file_paths = [result.file_path]
        else:
            n_parts = math.ceil(file_size / MAX_FILE_SIZE_BYTES)
            file_paths = split_video(
                duration_seconds=result.duration,
                input_path=result.file_path,
                output_dir=tmp_path,
                n_parts=n_parts,
            )
            while any(p.stat().st_size > MAX_FILE_SIZE_BYTES for p in file_paths):
                n_parts += 1
                if n_parts > 10:
                    raise SocialDownloadError("Video too large, cannot split into <= 10 parts")
                file_paths = split_video(
                    duration_seconds=result.duration,
                    input_path=result.file_path,
                    output_dir=tmp_path,
                    n_parts=n_parts,
                )

        log.info("sending %d part(s) to dump chat for %s", len(file_paths), video.link)
        video.file_ids = await _upload_parts_to_dump_chat(bot, file_paths, video.width, video.height)
        return video
```

- [ ] **Step 2: Verify import and basic call shape (no real network I/O)**

This confirms the module wires together correctly and that a `YouTubeError`
raised deep in the download loop propagates and fires the fail signal,
without needing a real video download:

```bash
uv run python -c "
import asyncio
from unittest.mock import patch
import bot.events  # registers signal handlers
from bot.events import freeze_signals
from bot.worker.pipeline import handle_youtube_video
from bot.util.youtube.exc import YouTubeError
from bot.util.youtube.schema import YouTubeVideoData

async def main():
    freeze_signals()
    video = YouTubeVideoData.model_validate(dict(link='https://youtu.be/dQw4w9WgXcQ'))

    with patch('bot.worker.pipeline.check_download_adaptive', side_effect=YouTubeError('no stream')):
        try:
            await handle_youtube_video(bot=None, video=video)
            print('ERROR: expected YouTubeError')
        except YouTubeError as e:
            print('raised as expected:', e)

asyncio.run(main())
"
```

Expected: `raised as expected: no stream`

- [ ] **Step 3: Commit**

```bash
git add bot/worker/pipeline.py
git commit -m "Add worker pipeline module (download/upload, decoupled from a single chat)"
```

---

### Task 7: Dramatiq actors

The actors tie together the lock (Task's prior `HeartbeatLock` fix, reused
unchanged for guarding the pipeline itself), the pipeline (Task 6), the
waiters list (Task 3), and the chat-action decorator (Task 2), and fan the
outcome out to every waiting chat.

**Files:**
- Create: `bot/worker/actors.py`

**Interfaces:**
- Produces: `process_youtube_link(chat_id: int, link: str, target_lang: str)`
  (Dramatiq actor — call via `.send(...)`), `process_social_link(chat_id: int, url: str)`
  (Dramatiq actor — call via `.send(...)`). `chat_id` here is only the
  *originally enqueuing* chat, used solely to drive the "uploading video…"
  chat-action indicator — delivery goes to every waiter popped from Redis,
  not just this one.
- Consumed by: Task 8 (`bot/handlers.py`).

- [ ] **Step 1: Create `bot/worker/actors.py`**

```python
import asyncio
import logging

import dramatiq
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from redis.asyncio.lock import Lock

from bot.config import settings
from bot.events.signals import on_yt_video_sent, on_social_video_sent
from bot.util.redis import redis_client
from bot.util.redis_lock import HeartbeatLock
from bot.util.social.exc import SocialDownloadError
from bot.util.social.schema import SocialVideoData
from bot.util.youtube.enum import TargetLang
from bot.util.youtube.exc import YouTubeError
from bot.util.youtube.schema import YouTubeVideoData
from bot.worker.broker import broker  # noqa: F401 -- registers the Redis broker before actors are declared
from bot.worker.chat_action import with_chat_action
from bot.worker.pipeline import handle_social_video, handle_youtube_video
from bot.worker.waiters import Waiter, pop_waiters

log = logging.getLogger(__name__)


async def _safe_edit_ack(bot: Bot, chat_id: int, message_id: int, text: str) -> None:
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest:
        log.warning("could not edit ack message %s in chat %s", message_id, chat_id)


async def _safe_delete_ack(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        log.warning("could not delete ack message %s in chat %s", message_id, chat_id)


async def _notify_waiters_success(bot: Bot, waiters: list[Waiter], video) -> None:
    for waiter in waiters:
        await _safe_delete_ack(bot, waiter.chat_id, waiter.ack_message_id)
        await video.send_to_chat(bot, waiter.chat_id, reply_to_message_id=waiter.reply_to_message_id)


async def _notify_waiters_failure(bot: Bot, waiters: list[Waiter], text: str) -> None:
    for waiter in waiters:
        await _safe_edit_ack(bot, waiter.chat_id, waiter.ack_message_id, text)


@with_chat_action()
async def _process_youtube_link_async(bot: Bot, chat_id: int, link: str, target_lang_value: str) -> None:
    target_lang = TargetLang(target_lang_value)
    video = YouTubeVideoData.model_validate(dict(link=link, target_lang=target_lang))

    lock = Lock(redis_client, f'{video.cache_key}:lock', timeout=10 * 60, blocking_timeout=11 * 60)
    async with HeartbeatLock(lock):
        try:
            video = await handle_youtube_video(bot, video)
        except YouTubeError as e:
            waiters = await pop_waiters(redis_client, video.cache_key)
            await _notify_waiters_failure(bot, waiters, f"❌ Couldn't process this video: {e}")
            raise

        await redis_client.set(video.cache_key, video.model_dump_json())
        log.info("cached %s (%d files)", video.cache_key, len(video.file_ids))

        waiters = await pop_waiters(redis_client, video.cache_key)
        await _notify_waiters_success(bot, waiters, video)
        for waiter in waiters:
            await on_yt_video_sent.send(link, waiter.chat_id, waiter.chat_type, bot, video, True)


@dramatiq.actor(
    max_retries=2,
    min_backoff=30_000,
    max_backoff=5 * 60_000,
    time_limit=45 * 60_000,
    throws=(YouTubeError,),
)
def process_youtube_link(chat_id: int, link: str, target_lang: str):
    bot = Bot(token=settings.bot_token)
    try:
        asyncio.run(_process_youtube_link_async(bot, chat_id, link, target_lang))
    finally:
        asyncio.run(bot.session.close())


@with_chat_action()
async def _process_social_link_async(bot: Bot, chat_id: int, url: str) -> None:
    video = SocialVideoData.model_validate(dict(link=url))

    lock = Lock(redis_client, f'{video.cache_key}:lock', timeout=20 * 60, blocking_timeout=21 * 60)
    async with HeartbeatLock(lock):
        try:
            video = await handle_social_video(bot, video)
        except SocialDownloadError as e:
            waiters = await pop_waiters(redis_client, video.cache_key)
            await _notify_waiters_failure(bot, waiters, f"❌ Couldn't download this video: {e}")
            raise

        await redis_client.set(video.cache_key, video.model_dump_json())
        log.info("cached %s (%s)", video.cache_key, video.origin)

        waiters = await pop_waiters(redis_client, video.cache_key)
        await _notify_waiters_success(bot, waiters, video)
        for waiter in waiters:
            await on_social_video_sent.send(url, waiter.chat_id, waiter.chat_type, bot, video, True)


@dramatiq.actor(
    max_retries=2,
    min_backoff=30_000,
    max_backoff=5 * 60_000,
    time_limit=25 * 60_000,
    throws=(SocialDownloadError,),
)
def process_social_link(chat_id: int, url: str):
    bot = Bot(token=settings.bot_token)
    try:
        asyncio.run(_process_social_link_async(bot, chat_id, url))
    finally:
        asyncio.run(bot.session.close())
```

- [ ] **Step 2: Verify orchestration with the pipeline stubbed out**

This exercises the full actor plumbing (lock, waiters fan-out to *two*
different chats, signal firing) without a real download, by monkeypatching
`handle_youtube_video` at the point `actors.py` imports it:

```bash
uv run python -c "
import asyncio
import threading
from unittest.mock import AsyncMock, patch

import bot.events
from bot.events import freeze_signals
from bot.util.redis import redis_client
from bot.worker.broker import broker
from bot.worker.waiters import Waiter, register_waiter
from bot.util.youtube.schema import YouTubeVideoData
from dramatiq.worker import Worker

freeze_signals()

class StubBot:
    def __init__(self, token=None):
        self.calls = []
    async def send_chat_action(self, chat_id, action):
        pass
    async def edit_message_text(self, text, chat_id, message_id):
        self.calls.append(('edit', chat_id, message_id, text))
    async def delete_message(self, chat_id, message_id):
        self.calls.append(('delete', chat_id, message_id))
    async def send_video(self, chat_id, **kwargs):
        self.calls.append(('send_video', chat_id, kwargs.get('reply_to_message_id')))
    async def send_media_group(self, chat_id, media, **kwargs):
        self.calls.append(('send_media_group', chat_id, kwargs.get('reply_to_message_id')))
    class session:
        @staticmethod
        async def close():
            pass

async def fake_handle_youtube_video(bot, video):
    video.file_ids = ['stub_file_id']
    video.width, video.height = 640, 360
    return video

async def setup_waiters():
    video = YouTubeVideoData.model_validate(dict(link='https://youtu.be/dQw4w9WgXcQ'))
    await redis_client.delete(video.cache_key, f'{video.cache_key}:lock')
    w1 = Waiter(chat_id=111, chat_type='private', reply_to_message_id=1, ack_message_id=2)
    w2 = Waiter(chat_id=222, chat_type='group', reply_to_message_id=3, ack_message_id=4)
    await register_waiter(redis_client, video.cache_key, w1, ttl=60)
    await register_waiter(redis_client, video.cache_key, w2, ttl=60)
    return video.cache_key

cache_key = asyncio.run(setup_waiters())

with patch('bot.worker.actors.Bot', StubBot), \
     patch('bot.worker.pipeline.handle_youtube_video', fake_handle_youtube_video):
    from bot.worker.actors import process_youtube_link

    worker = Worker(broker, worker_threads=1)
    worker.start()
    try:
        process_youtube_link.send(111, 'https://youtu.be/dQw4w9WgXcQ', 'original')

        async def wait_for_cache():
            for _ in range(50):
                if await redis_client.get(cache_key):
                    return True
                await asyncio.sleep(0.2)
            return False

        cached = asyncio.run(wait_for_cache())
        print('cached:', cached)
    finally:
        worker.stop()
        worker.join()
"
```

Expected: `cached: True`. (Delivery-call assertions on the `StubBot`
instances aren't captured here since each actor invocation creates its own
`Bot()`; Task 8's end-to-end manual test is where real delivery to two
chats gets verified against Telegram.)

- [ ] **Step 3: Commit**

```bash
git add bot/worker/actors.py
git commit -m "Add Dramatiq actors for YouTube and social video processing"
```

---

### Task 8: Rewrite `bot/handlers.py` to enqueue via the waiters list

This is where the app becomes fully coherent again — the old inline
`handle_youtube_video`/`handle_social_video`/`_social_cache_key` are
removed from `handlers.py` (they now live in `bot/worker/pipeline.py` and
`SocialVideoData.cache_key`, from Tasks 5 and 6), and the cache-miss path
switches from "process inline" to "register a waiter and maybe enqueue".

**Files:**
- Modify: `bot/handlers.py`

- [ ] **Step 1: Rewrite `bot/handlers.py`**

```python
import logging
import re

from aiogram import types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import ErrorEvent, Message

from .config import settings
from .dispatcher import router
from .enum import LinkOrigin
from .events import on_link_received, on_yt_video_sent, on_social_video_sent
from .util.stats import build_stats_report
from .util.redis import redis_client
from .util.social.schema import SocialVideoData
from .util.youtube.enum import TargetLang
from .util.youtube.schema import YouTubeVideoData
from .worker.actors import process_social_link, process_youtube_link
from .worker.waiters import Waiter, register_waiter

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://\S+")
_YOUTUBE_URL_RE = re.compile(r"https?://((www|m)\.)?youtube\.com/|https?://youtu\.be/")

_PROCESSING_TEXT = "⏳ Processing…"
_YOUTUBE_WAITERS_TTL = 3 * 60 * 60  # generous vs. worst-case retry budget (~2.5h)
_SOCIAL_WAITERS_TTL = 90 * 60  # ~1.5h


@router.error()
async def error_handler(event: ErrorEvent):
    log.critical("Critical error caused by %s", event.exception, exc_info=True)
    if settings.admin_chat_id:
        message = event.update.message
        if not message:
            return
        msg = f"""\
            Exception:
            `{event.exception!r}`
            Message text:
            `{message.text}`
            """
        await message.bot.send_message(settings.admin_chat_id, msg, parse_mode="MarkdownV2")


@router.message(CommandStart())
async def start(message: types.Message):
    await message.reply("Send a link and i will reply with a nice embedding or a video")


@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if not settings.admin_chat_id or message.chat.id != settings.admin_chat_id:
        return
    await message.reply(await build_stats_report())


@router.message(
    F.text.regexp(r"^https://(((www|m)\.)?youtube\.com/(watch|shorts/)|youtu\.be/)")
)
async def embed_youtube_videos(message: types.Message):
    await on_link_received.send(message, LinkOrigin.YOUTUBE)
    link = message.text.split()[0]  # as regex states, we expect the first element in the text to be a link

    log.info('user lang: %s', message.from_user.language_code)
    try:
        target_lang = TargetLang(message.from_user.language_code)
    except ValueError:
        target_lang = TargetLang.ORIGINAL
        log.info('no translation will be performed')

    video = YouTubeVideoData.model_validate(dict(link=link, target_lang=target_lang))

    if video_raw := await redis_client.get(video.cache_key):
        cached = YouTubeVideoData.model_validate_json(video_raw)
        log.info("cache hit for %s", video.cache_key)
        try:
            await cached.reply_to(message)
        except TelegramBadRequest:
            log.info("cached telegram file ids failed to be posted, removing from cache")
            await redis_client.delete(video.cache_key)
        else:
            await on_yt_video_sent.send(link, message.chat.id, message.chat.type, message.bot, cached, False)
            return

    log.info("cache miss for %s, registering waiter", video.cache_key)
    ack = await message.reply(_PROCESSING_TEXT)
    waiter = Waiter(
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        reply_to_message_id=message.message_id,
        ack_message_id=ack.message_id,
    )
    is_first = await register_waiter(redis_client, video.cache_key, waiter, _YOUTUBE_WAITERS_TTL)
    if is_first:
        process_youtube_link.send(message.chat.id, link, target_lang.value)


async def _process_social_url(message: Message, url: str) -> None:
    video = SocialVideoData.model_validate(dict(link=url))

    if video_raw := await redis_client.get(video.cache_key):
        cached = SocialVideoData.model_validate_json(video_raw)
        log.info("cache hit for %s", video.cache_key)
        try:
            await cached.reply_to(message)
        except TelegramBadRequest:
            log.info("cached file ids invalid, clearing cache for %s", video.cache_key)
            await redis_client.delete(video.cache_key)
        else:
            await on_social_video_sent.send(url, message.chat.id, message.chat.type, message.bot, cached, False)
            return

    log.info("cache miss for %s, registering waiter", video.cache_key)
    ack = await message.reply(_PROCESSING_TEXT)
    waiter = Waiter(
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        reply_to_message_id=message.message_id,
        ack_message_id=ack.message_id,
    )
    is_first = await register_waiter(redis_client, video.cache_key, waiter, _SOCIAL_WAITERS_TTL)
    if is_first:
        process_social_link.send(message.chat.id, url)


@router.message(F.text.regexp(r"https?://"))
async def embed_social(message: types.Message):
    urls = [u for u in _URL_RE.findall(message.text) if not _YOUTUBE_URL_RE.match(u)]
    if not urls:
        return

    await on_link_received.send(message, LinkOrigin.SOCIAL)

    for url in urls:
        await _process_social_url(message, url)
```

- [ ] **Step 2: Sanity-check the module imports cleanly**

```bash
uv run python -c "from bot import handlers; print('handlers imported ok')"
```

Expected: `handlers imported ok` (this alone confirms
`bot.worker.actors` → `bot.worker.broker` → Redis broker construction
all succeed at import time, and there are no leftover references to the
removed `handle_youtube_video`/`handle_social_video`/`_social_cache_key`
names).

- [ ] **Step 3: End-to-end manual verification (spec Testing items 1-5)**

Requires a real `.env` (`BOT_TOKEN`, `DUMP_CHAT_ID`) and local Redis. Run
in two terminals from the repo root:

```
# terminal 1
uv run python main.py

# terminal 2
uv run dramatiq bot.worker.actors --processes 1 --threads 4
```

Then, from Telegram, against the bot:

1. **Cache hit still synchronous:** paste a link you've already sent
   before (or send the same link twice in a row) — the second reply should
   arrive immediately, and terminal 2's log should show no new job for it.
2. **Cache-miss end-to-end:** paste a fresh YouTube or social link — expect
   a "⏳ Processing…" reply within ~1s, terminal 2 logs picking up the job,
   periodic "sending chat action" log lines while it runs, then the
   processing message replaced by the actual video and terminal 1 logging
   the cache write.
3. **Worker crash mid-job:** paste a fresh link, and partway through
   (terminal 2 logs show the download in progress) kill terminal 2's
   process (Ctrl+C or `kill`), then restart it
   (`uv run dramatiq bot.worker.actors --processes 1 --threads 4`).
   Confirm the job resumes and completes rather than leaving the "⏳
   Processing…" message stuck forever.
4. **Unrecoverable error path:** paste a link known to trigger a
   `YouTubeError`/`SocialDownloadError` (e.g. a private/geo-blocked/removed
   video), confirm the "⏳ Processing…" message is edited to a "❌ Couldn't
   ..." failure within one attempt (no retries burned — check terminal 2
   logs show only one attempt).
5. **Same link, two chats at once:** from two different chats the bot is
   in (e.g. your own DM and a test group), paste the *same* fresh link
   within a couple seconds of each other. Confirm terminal 2 only logs
   **one** job for that link, both chats get their own "⏳ Processing…"
   ack, and both chats receive the video once it completes.

- [ ] **Step 4: Commit**

```bash
git add bot/handlers.py
git commit -m "Enqueue video processing via Dramatiq instead of processing inline"
```

---

### Task 9: Queue-depth stats in `/stats`

Replaces the dropped `dramatiq-dashboard` with a minimal read of the
broker's own Redis keys, appended to the existing admin `/stats` report.

**Files:**
- Modify: `bot/util/stats.py`

**Interfaces:**
- Produces: an extra line in `build_stats_report()`'s output showing
  pending and dead-lettered ("failed") message counts for the default
  Dramatiq queue.

- [ ] **Step 1: Add `_queue_stats` and wire it into `build_stats_report`**

In `bot/util/stats.py`, add near the top (after the existing imports):

```python
from bot.worker.broker import broker as dramatiq_broker


async def _queue_stats() -> dict:
    namespace = dramatiq_broker.namespace
    pending = await redis_client.hlen(f"{namespace}:default.msgs")
    failed = await redis_client.zcard(f"{namespace}:default.XQ")
    return {"pending": pending, "failed": failed}
```

Then change `build_stats_report` from:

```python
async def build_stats_report() -> str:
    today = settings.now().date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    today_stats, week_stats, month_stats = await asyncio.gather(
        _period_stats([today.strftime("%Y-%m-%d")]),
        _period_stats(_date_range(week_start, today)),
        _period_stats(_date_range(month_start, today)),
    )

    today_label = f"Today ({today.strftime('%b %-d')})"
    week_label = f"This week ({week_start.strftime('%b %-d')}–{today.strftime('%b %-d')})"
    month_label = f"This month ({today.strftime('%B')})"

    return "\n".join([
        "📊 Stats",
        "",
        _fmt_section(today_label, today_stats),
        "",
        _fmt_section(week_label, week_stats),
        "",
        _fmt_section(month_label, month_stats),
    ])
```

to:

```python
async def build_stats_report() -> str:
    today = settings.now().date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    today_stats, week_stats, month_stats, queue_stats = await asyncio.gather(
        _period_stats([today.strftime("%Y-%m-%d")]),
        _period_stats(_date_range(week_start, today)),
        _period_stats(_date_range(month_start, today)),
        _queue_stats(),
    )

    today_label = f"Today ({today.strftime('%b %-d')})"
    week_label = f"This week ({week_start.strftime('%b %-d')}–{today.strftime('%b %-d')})"
    month_label = f"This month ({today.strftime('%B')})"

    return "\n".join([
        "📊 Stats",
        "",
        _fmt_section(today_label, today_stats),
        "",
        _fmt_section(week_label, week_stats),
        "",
        _fmt_section(month_label, month_stats),
        "",
        f"🔧 Queue: pending {queue_stats['pending']} | failed {queue_stats['failed']}",
    ])
```

- [ ] **Step 2: Verify against local Redis**

```bash
uv run python -c "
import asyncio
from bot.worker.broker import broker
from bot.util.redis import redis_client
from bot.util.stats import build_stats_report
import dramatiq
from dramatiq.worker import Worker

@dramatiq.actor(max_retries=0)
def scratch_for_stats(x):
    pass

async def main():
    scratch_for_stats.send(1)
    scratch_for_stats.send(2)
    report = await build_stats_report()
    print(report)
    # drain so the scratch messages don't linger
    worker = Worker(broker, worker_threads=1)
    worker.start()
    await asyncio.sleep(1)
    worker.stop()
    worker.join()

asyncio.run(main())
"
```

Expected: the printed report includes a line like
`🔧 Queue: pending 2 | failed 0` (pending count reflects the two
just-enqueued scratch messages before the worker drains them).

- [ ] **Step 3: Commit**

```bash
git add bot/util/stats.py
git commit -m "Add Dramatiq queue depth to the /stats admin command"
```

---

### Task 10: `worker` service in `compose.yml`

**Files:**
- Modify: `compose.yml`

- [ ] **Step 1: Add the `worker` service**

In `compose.yml`, add a new service under `services:`, alongside `bot`:

```yaml
services:
    bot:
        image: metheoryt/embedthat-bot:latest
        build: .
        env_file:
            - .env
        volumes:
            - .:/app
            - bot_venv:/app/.venv
        restart: unless-stopped
    worker:
        image: metheoryt/embedthat-bot:latest
        build: .
        env_file:
            - .env
        volumes:
            - .:/app
            - bot_venv:/app/.venv
        command: dramatiq bot.worker.actors --processes 1 --threads 4
        restart: unless-stopped
    redis:
        image: redis/redis-stack:latest
        ports:
            - "6379:6379"
        volumes:
            - redis_data:/data
        environment:
            REDIS_ARGS: "--save 60 1"
        restart: unless-stopped

volumes:
    redis_data:
    bot_venv:
```

`--processes 1` avoids loading a duplicate Whisper model per process (see
Global Constraints); `--threads 4` still allows real concurrency since
`faster-whisper`'s model global is shared across threads within one
process.

- [ ] **Step 2: Validate the compose file**

Run: `docker compose config`
Expected: valid YAML output showing both `bot` and `worker` services with
the `worker` service's `command` overriding the image's default `CMD`.

- [ ] **Step 3: Smoke-test the worker container builds and starts**

Run: `docker compose up --build -d worker` then `docker compose logs worker --tail=30`
Expected: logs show Dramatiq's startup banner and
`[INFO] Worker(...) is ready for action.` with no import errors (confirms
the same image used for `bot` also runs the worker command cleanly).

Then: `docker compose stop worker`

- [ ] **Step 4: Commit**

```bash
git add compose.yml
git commit -m "Add worker service to compose.yml"
```

---

## Self-Review Notes

- **Spec coverage:** Architecture (two services, shared image, Redis as
  broker) → Task 10. Components (actors, async bridging, chat-action
  decorator) → Tasks 2, 7. Data flow (cache hit inline, waiters-list
  dedup/fan-out) → Tasks 3, 8. Error handling (`TimeLimit`/`Retries`
  overrides, `throws`) → Task 7. Testing → Task 8 Step 3 mirrors the
  spec's six manual-verification items (dashboard item replaced by Task 9's
  `/stats` queue line, per the amended spec).
- **Type/name consistency checked:** `YouTubeVideoData.cache_key` /
  `SocialVideoData.cache_key` used identically in Tasks 5, 7, 8;
  `Waiter` fields (`chat_id`, `chat_type`, `reply_to_message_id`,
  `ack_message_id`) used identically in Tasks 3, 7, 8; signal signatures
  from Task 4 match every `.send(...)` call site in Tasks 6 and 8.
