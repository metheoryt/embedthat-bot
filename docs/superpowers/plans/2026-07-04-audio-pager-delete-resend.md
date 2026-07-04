# Audio Pager Delete-and-Resend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the audio pager's reply-chained "send a new message per page tap" behavior with delete-old/send-new, anchored to the user's original link message instead of whatever pager message was tapped.

**Architecture:** `AudioRequestData.send_to_chat` becomes the sole delivery path (its near-duplicate `reply_to` is deleted) and now returns the message_ids it sent. A new orchestration function, `redeliver_page`, wraps every call to `send_to_chat`: it reads the previously-live message_ids for this pager thread from Redis, sends the new page, saves the new message_ids, then best-effort-deletes the old ones. All three delivery sites (fresh-link cache hit, page-tap cache hit, async cache-miss resolution) route through `redeliver_page`.

**Tech Stack:** Python 3.12, aiogram 3 (Telegram Bot API), redis.asyncio, Pydantic v2, dramatiq (worker queue). No test framework in this repo — verification is scratchpad scripts using stdlib `unittest.mock`, plus a final manual pass against the running bot.

## Global Constraints

- No test suite exists in this repo (per `CLAUDE.md`) — every "test" step below is a standalone script run with `uv run python <path>`, not `pytest`. Run each from the repo root (`C:\Users\methe\GitHub\embedthat-bot`) so the `bot` package resolves.
- Save scratchpad scripts under `C:\Users\methe\AppData\Local\Temp\claude\C--Users-methe-GitHub-embedthat-bot\649b910a-b0ae-44d0-a82e-a18cb2a5b30c\scratchpad` — they are throwaway, not committed.
- Redis client is created with `decode_responses=True` everywhere in this codebase (`bot/util/redis.py`, `bot/worker/actors.py`) — `redis_client.get(...)` returns `str | None`, never `bytes`.
- `callback_data` must stay within Telegram's 64-byte limit — `apg:{hash16}:{page}:{root_message_id}` (16 + up to 3 + up to 10 digits + 3 colons) is well within it; don't add further fields to it without rechecking.
- Design source of truth: `docs/superpowers/specs/2026-07-04-audio-pager-delete-resend-design.md` (and its concurrency-focused predecessor, `docs/superpowers/specs/2026-07-04-audio-pager-concurrency-design.md`, for `PAGE_SIZE`/`pager_markup` history).

---

### Task 1: `pager_markup` carries the root message id

**Files:**
- Modify: `bot/util/audio/schema.py:56-71` (the `pager_markup` method)
- Test: scratchpad script (not committed)

**Interfaces:**
- Consumes: nothing new.
- Produces: `AudioRequestData.pager_markup(self, page: int, root_message_id: int) -> types.InlineKeyboardMarkup | None`. `callback_data` format for Back/Next buttons becomes `apg:{hash16}:{page}:{root_message_id}` (4 colon-separated parts). The `apg:noop` middle button and the `total_pages <= 1 -> None` behavior are unchanged. Later tasks depend on this exact signature and callback_data shape.

- [ ] **Step 1: Write the failing script**

Save as `<scratchpad>/verify_pager_markup_root.py`:

```python
from bot.util.audio.schema import AudioRequestData, AudioTrackData


def make_track(i: int) -> AudioTrackData:
    return AudioTrackData(
        extractor="youtube", id=f"t{i}", webpage_url=f"https://example.com/{i}",
        title=f"Track {i}", uploader="u", duration=1, file_id=f"file{i}",
    )


tracks = [make_track(i) for i in range(1, 25)]  # 24 tracks -> 3 pages of 10
audio = AudioRequestData(link="https://v", tracks=tracks)

markup = audio.pager_markup(2, root_message_id=555)
back, mid, nxt = markup.inline_keyboard[0]
assert back.callback_data.split(":") == ["apg", audio.hash16, "1", "555"], back.callback_data
assert nxt.callback_data.split(":") == ["apg", audio.hash16, "3", "555"], nxt.callback_data
assert mid.callback_data == "apg:noop"

assert audio.pager_markup(1, root_message_id=555) is not None  # page 1 of 3 still has Next
single_page_audio = AudioRequestData(link="https://u", tracks=[make_track(1)])
assert single_page_audio.pager_markup(1, root_message_id=555) is None

print("all OK")
```

Run: `uv run python <scratchpad_path>/verify_pager_markup_root.py`
Expected: `TypeError: pager_markup() missing 1 required positional argument: 'root_message_id'` (current signature only takes `page`).

- [ ] **Step 2: Implement**

Replace `bot/util/audio/schema.py:56-71` with:

```python
    def pager_markup(self, page: int, root_message_id: int) -> types.InlineKeyboardMarkup | None:
        if self.total_pages <= 1:
            return None
        buttons = []
        if page > 1:
            buttons.append(
                types.InlineKeyboardButton(
                    text="◀️ Back", callback_data=f"apg:{self.hash16}:{page - 1}:{root_message_id}"
                )
            )
        buttons.append(
            types.InlineKeyboardButton(text=f"{page}/{self.total_pages}", callback_data="apg:noop")
        )
        if page < self.total_pages:
            buttons.append(
                types.InlineKeyboardButton(
                    text="Next ▶️", callback_data=f"apg:{self.hash16}:{page + 1}:{root_message_id}"
                )
            )
        return types.InlineKeyboardMarkup(inline_keyboard=[buttons])
```

- [ ] **Step 3: Run the script again**

Run: `uv run python <scratchpad_path>/verify_pager_markup_root.py`
Expected: `all OK` printed, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add bot/util/audio/schema.py
git commit -m "Thread root_message_id through audio pager callback_data"
```

---

### Task 2: `send_to_chat` returns message_ids; `reply_to` is deleted

**Files:**
- Modify: `bot/util/audio/schema.py:9` (add a placeholder-text constant next to `PAGE_SIZE`)
- Modify: `bot/util/audio/schema.py:80-129` (rewrite `send_to_chat`, delete `reply_to`)
- Test: scratchpad script (not committed)

**Interfaces:**
- Consumes: `pager_markup(page, root_message_id)` from Task 1.
- Produces: `AudioRequestData.send_to_chat(self, bot: Bot, chat_id: int, reply_to_message_id: int, page: int = 1) -> list[int]` — `reply_to_message_id` is now required (it doubles as the pager's root). Returns the message_ids of every message it sent, in send order (media message(s) first, nav message last if one was sent). `reply_to` no longer exists — Task 3 removes its last callers. `_pager_caption(skipped)` is unchanged but is now always called (no `if markup or skipped` gate) and its content (warning + source link) lands on the **first** media item's caption for multi-track pages, or the single audio's caption for one-track pages. A multi-track page only gets a trailing nav message (placeholder text + buttons, **not** a reply) when `pager_markup` returns non-`None`.

- [ ] **Step 1: Write the failing script**

Save as `<scratchpad>/verify_send_to_chat.py`:

```python
import asyncio
from unittest.mock import AsyncMock

from bot.util.audio.schema import AudioRequestData, AudioTrackData


def make_track(i: int, file_id: str | None) -> AudioTrackData:
    return AudioTrackData(
        extractor="youtube", id=f"t{i}", webpage_url=f"https://example.com/{i}",
        title=f"Track {i}", uploader="uploader", duration=100, file_id=file_id,
    )


def make_bot(start_id: int) -> AsyncMock:
    bot = AsyncMock()
    next_id = [start_id]

    def _msg(*args, **kwargs):
        m = AsyncMock()
        m.message_id = next_id[0]
        next_id[0] += 1
        return m

    async def _send_media_group(*args, **kwargs):
        media = args[1] if len(args) > 1 else kwargs["media"]
        messages = []
        for _ in media:
            m = AsyncMock()
            m.message_id = next_id[0]
            next_id[0] += 1
            messages.append(m)
        return messages

    bot.send_message.side_effect = _msg
    bot.send_audio.side_effect = _msg
    bot.send_media_group.side_effect = _send_media_group
    return bot


async def main():
    # case 1: single deliverable track, single page -> one message, caption+no-markup attached
    audio = AudioRequestData(link="https://x", tracks=[make_track(1, "file1")])
    bot = make_bot(100)
    ids = await audio.send_to_chat(bot, chat_id=1, reply_to_message_id=999, page=1)
    assert ids == [100], ids
    _, kwargs = bot.send_audio.call_args
    assert kwargs["caption"] == '🔗 <a href="https://x">Source</a>', kwargs["caption"]
    assert kwargs["reply_markup"] is None
    bot.send_message.assert_not_called()
    print("case 1 OK")

    # case 2: multi-track, single page -> media group only, first item carries caption, no nav
    tracks = [make_track(i, f"file{i}") for i in range(1, 4)]
    audio = AudioRequestData(link="https://y", tracks=tracks)
    bot = make_bot(200)
    ids = await audio.send_to_chat(bot, chat_id=1, reply_to_message_id=999, page=1)
    assert ids == [200, 201, 202], ids
    args, kwargs = bot.send_media_group.call_args
    media = kwargs.get("media", args[1] if len(args) > 1 else None)
    assert media[0].caption == '🔗 <a href="https://y">Source</a>', media[0].caption
    assert media[1].caption is None
    bot.send_message.assert_not_called()
    print("case 2 OK")

    # case 3: multi-track, multi-page -> nav message sent with markup, not a reply
    tracks = [make_track(i, f"file{i}") for i in range(1, 25)]  # 24 tracks -> 3 pages of 10
    audio = AudioRequestData(link="https://z", tracks=tracks)
    bot = make_bot(300)
    ids = await audio.send_to_chat(bot, chat_id=1, reply_to_message_id=999, page=1)
    assert len(ids) == 11, ids  # 10 media + 1 nav
    _, nav_kwargs = bot.send_message.call_args
    assert "reply_to_message_id" not in nav_kwargs, "nav message must not be a reply"
    assert nav_kwargs["reply_markup"] is not None
    print("case 3 OK")

    # case 4: no deliverable tracks -> single error message, still tracked
    audio = AudioRequestData(link="https://w", tracks=[make_track(1, None)])
    bot = make_bot(400)
    ids = await audio.send_to_chat(bot, chat_id=1, reply_to_message_id=999, page=1)
    assert ids == [400], ids
    print("case 4 OK")


asyncio.run(main())
print("all OK")
```

Run: `uv run python <scratchpad_path>/verify_send_to_chat.py`
Expected: `TypeError: send_to_chat() missing 1 required positional argument` or an `AssertionError` on `ids == [100]` (current method returns `None`).

- [ ] **Step 2: Implement**

Add this constant right after `PAGE_SIZE = 10  # Telegram's InputMediaAudio media-group cap` (`bot/util/audio/schema.py:9`):

```python
_NAV_PLACEHOLDER = "⁣"  # invisible separator -- Telegram requires non-empty message text
```

Replace `bot/util/audio/schema.py:80-129` (the current `send_to_chat` method through the end of `reply_to`) with:

```python
    async def send_to_chat(self, bot: Bot, chat_id: int, reply_to_message_id: int, page: int = 1) -> list[int]:
        page_tracks = self.page(page)
        deliverable = [t for t in page_tracks if t.file_id]
        if not deliverable:
            message = await bot.send_message(
                chat_id, "❌ No tracks on this page could be downloaded.", reply_to_message_id=reply_to_message_id
            )
            return [message.message_id]

        skipped = len(page_tracks) - len(deliverable)
        markup = self.pager_markup(page, reply_to_message_id)
        caption = self._pager_caption(skipped)

        if len(deliverable) == 1:
            t = deliverable[0]
            message = await bot.send_audio(
                chat_id, cast(str, t.file_id), performer=t.uploader, title=t.title, duration=t.duration,
                reply_to_message_id=reply_to_message_id,
                caption=caption, parse_mode="HTML", reply_markup=markup,
            )
            return [message.message_id]

        first = deliverable[0]
        media: list[types.InputMediaAudio] = [
            types.InputMediaAudio(
                media=cast(str, first.file_id), title=first.title, performer=first.uploader,
                duration=first.duration, caption=caption, parse_mode="HTML",
            ),
            *(t.as_input_media for t in deliverable[1:]),
        ]
        messages = await bot.send_media_group(chat_id, media, reply_to_message_id=reply_to_message_id)
        message_ids = [m.message_id for m in messages]
        if markup:
            nav = await bot.send_message(chat_id, _NAV_PLACEHOLDER, reply_markup=markup)
            message_ids.append(nav.message_id)
        return message_ids
```

(This deletes `reply_to` entirely — nothing after the new `send_to_chat` body remains in the file.)

- [ ] **Step 3: Run the script again**

Run: `uv run python <scratchpad_path>/verify_send_to_chat.py`
Expected: `case 1 OK` through `case 4 OK`, then `all OK`, exit code 0.

- [ ] **Step 4: Confirm no leftover references to the deleted method**

Run: `git grep -n "\.reply_to(" -- bot/` (from repo root)
Expected: no output yet from `bot/util/audio/schema.py` itself, but `bot/handlers.py` will still show two hits (`get_audio_page`, `_process_social_url`) — that's expected and fixed in Task 3. Confirm the only hits are those two known call sites, not something unexpected.

- [ ] **Step 5: Commit**

```bash
git add bot/util/audio/schema.py
git commit -m "Make send_to_chat the sole audio delivery path, return sent message_ids"
```

Note: this leaves `bot/handlers.py` calling a now-deleted `AudioRequestData.reply_to` — the tree is not runnable until Task 3 lands. This mirrors this repo's own prior multi-task plans (see `docs/superpowers/plans/2026-07-03-audio-only-platforms.md`), which land tightly-coupled cross-file changes as a short sequence of commits reviewed together, not as independently-deployable increments.

---

### Task 3: `redeliver_page` — shared send/track/delete orchestration

**Files:**
- Create: `bot/util/audio/pager.py`
- Test: scratchpad script (not committed)

**Interfaces:**
- Consumes: `AudioRequestData.send_to_chat(bot, chat_id, reply_to_message_id, page) -> list[int]` from Task 2.
- Produces: `redeliver_page(redis_client: redis.Redis, bot: Bot, chat_id: int, root_message_id: int, audio: AudioRequestData, page: int) -> None`. Internally reads/writes Redis key `da:msgs:{chat_id}:{root_message_id}` (JSON list of ints, no TTL). Tasks 4 and 5 call this function; nothing else in `pager.py` is consumed externally.

- [ ] **Step 1: Write the failing script**

Save as `<scratchpad>/verify_redeliver_page.py`:

```python
import asyncio
import json
from unittest.mock import AsyncMock, Mock

from aiogram.exceptions import TelegramBadRequest

from bot.util.audio.pager import redeliver_page
from bot.util.audio.schema import AudioRequestData, AudioTrackData


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = value


def make_track(i: int) -> AudioTrackData:
    return AudioTrackData(
        extractor="youtube", id=f"t{i}", webpage_url=f"https://example.com/{i}",
        title=f"Track {i}", uploader="u", duration=1, file_id=f"file{i}",
    )


async def main():
    redis_client = FakeRedis()
    bot = AsyncMock()
    next_id = [1]

    def _msg(*a, **kw):
        m = AsyncMock()
        m.message_id = next_id[0]
        next_id[0] += 1
        return m

    bot.send_audio.side_effect = _msg
    bot.send_message.side_effect = _msg

    audio = AudioRequestData(link="https://x", tracks=[make_track(1)])  # single track, single page

    # first delivery: nothing stored yet, nothing to delete
    await redeliver_page(redis_client, bot, chat_id=42, root_message_id=999, audio=audio, page=1)
    bot.delete_message.assert_not_called()
    assert json.loads(redis_client.store["da:msgs:42:999"]) == [1]
    print("case 1 (first delivery) OK")

    # second delivery: prior message [1] gets deleted, new one [2] stored
    await redeliver_page(redis_client, bot, chat_id=42, root_message_id=999, audio=audio, page=1)
    bot.delete_message.assert_called_once_with(42, 1)
    assert json.loads(redis_client.store["da:msgs:42:999"]) == [2]
    print("case 2 (resend deletes prior) OK")

    # third delivery: a delete failure (message already gone) must not propagate
    bot.delete_message.side_effect = TelegramBadRequest(
        method=Mock(), message="Bad Request: message to delete not found"
    )
    await redeliver_page(redis_client, bot, chat_id=42, root_message_id=999, audio=audio, page=1)
    assert json.loads(redis_client.store["da:msgs:42:999"]) == [3]
    print("case 3 (swallows delete failure) OK")


asyncio.run(main())
print("all OK")
```

Run: `uv run python <scratchpad_path>/verify_redeliver_page.py`
Expected: `ModuleNotFoundError: No module named 'bot.util.audio.pager'`.

- [ ] **Step 2: Implement**

Create `bot/util/audio/pager.py`:

```python
import json
import logging

import redis.asyncio as redis
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from bot.util.audio.schema import AudioRequestData

log = logging.getLogger(__name__)


def _messages_key(chat_id: int, root_message_id: int) -> str:
    return f"da:msgs:{chat_id}:{root_message_id}"


async def redeliver_page(
    redis_client: redis.Redis, bot: Bot, chat_id: int, root_message_id: int, audio: AudioRequestData, page: int,
) -> None:
    key = _messages_key(chat_id, root_message_id)
    old_raw = await redis_client.get(key)
    old_ids: list[int] = json.loads(old_raw) if old_raw else []

    new_ids = await audio.send_to_chat(bot, chat_id, reply_to_message_id=root_message_id, page=page)
    await redis_client.set(key, json.dumps(new_ids))

    for message_id in old_ids:
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramBadRequest:
            log.warning("could not delete old page message %s in chat %s", message_id, chat_id)
```

- [ ] **Step 3: Run the script again**

Run: `uv run python <scratchpad_path>/verify_redeliver_page.py`
Expected: `case 1 (first delivery) OK`, `case 2 (resend deletes prior) OK`, `case 3 (swallows delete failure) OK`, `all OK`, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add bot/util/audio/pager.py
git commit -m "Add redeliver_page to send-then-delete audio pager pages"
```

---

### Task 4: Wire `bot/handlers.py` to `redeliver_page`

**Files:**
- Modify: `bot/handlers.py` (import block; `get_audio_page` at `bot/handlers.py:146-183`; `_process_social_url` at `bot/handlers.py:186-216`)
- Modify: `bot/worker/waiters.py:5-9` (doc comment only, no field/behavior change)
- Test: scratchpad script (not committed) + repo-wide grep

**Interfaces:**
- Consumes: `redeliver_page(redis_client, bot, chat_id, root_message_id, audio, page)` from Task 3.
- Produces: `get_audio_page`'s `callback_data` parsing now expects 4 colon-separated parts (`_, hash16, page_str, root_str`). `Waiter.reply_to_message_id` registered from this handler on a cache-miss is now always the pager's root, not the tapped message — Task 5 depends on this.

- [ ] **Step 1: Write the failing script**

Save as `<scratchpad>/verify_callback_data_roundtrip.py` (confirms the exact string shape `pager_markup` produces is what `get_audio_page` will parse):

```python
from bot.util.audio.schema import AudioRequestData, AudioTrackData


def make_track(i: int) -> AudioTrackData:
    return AudioTrackData(
        extractor="youtube", id=f"t{i}", webpage_url=f"https://example.com/{i}",
        title=f"Track {i}", uploader="u", duration=1, file_id=f"file{i}",
    )


tracks = [make_track(i) for i in range(1, 25)]
audio = AudioRequestData(link="https://v", tracks=tracks)
markup = audio.pager_markup(2, root_message_id=987654321)
_, mid, nxt = markup.inline_keyboard[0]

_, hash16, page_str, root_str = nxt.callback_data.split(":")
assert hash16 == audio.hash16
assert int(page_str) == 3
assert int(root_str) == 987654321
print("all OK")
```

Run: `uv run python <scratchpad_path>/verify_callback_data_roundtrip.py`
Expected: `all OK` (this already passes after Task 1 — it's here to pin the contract before touching the handler that parses it).

- [ ] **Step 2: Implement**

In `bot/handlers.py`, add to the import block (near the other `.util.audio` import):

```python
from .util.audio.pager import redeliver_page
```

Replace `bot/handlers.py:146-183` (`get_audio_page`) with:

```python
@router.callback_query(F.data.startswith("apg:"))
async def get_audio_page(callback: types.CallbackQuery):
    await callback.answer()
    if not isinstance(callback.message, types.Message):
        return

    _, hash16, page_str, root_str = callback.data.split(":")
    page = int(page_str)
    root_message_id = int(root_str)
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
        await redeliver_page(
            redis_client, callback.message.bot, callback.message.chat.id, root_message_id, audio, page,
        )
        return

    log.info("cache miss for %s page %d, registering waiter", cache_key, page)
    page_key = f"{cache_key}:page:{page}"
    waiter = Waiter(
        chat_id=callback.message.chat.id,
        chat_type=callback.message.chat.type,
        reply_to_message_id=root_message_id,
    )
    is_first = await register_waiter(redis_client, page_key, waiter, _SOCIAL_WAITERS_TTL)
    if is_first:
        process_audio_page.send(callback.message.chat.id, hash16, page)
```

In `bot/handlers.py:186-216` (`_process_social_url`), replace only the audio cache-hit branch:

```python
    audio = AudioRequestData(link=url)
    if audio_raw := await redis_client.get(audio.cache_key):
        cached_audio = AudioRequestData.model_validate_json(audio_raw)
        log.info("cache hit (audio) for %s", audio.cache_key)
        await redeliver_page(redis_client, message.bot, message.chat.id, message.message_id, cached_audio, page=1)
        return
```

(The rest of `_process_social_url` — the `SocialVideoData` branch and waiter registration — is untouched.)

In `bot/worker/waiters.py:5-9`, document the new dual meaning of `reply_to_message_id` for audio waiters (no field or type change):

```python
class Waiter(BaseModel):
    chat_id: int
    chat_type: str
    reply_to_message_id: int  # for audio-pager waiters, this is the pager's root message id
    ack_message_id: int | None = None
```

- [ ] **Step 3: Run the script again, and confirm no dangling references**

Run: `uv run python <scratchpad_path>/verify_callback_data_roundtrip.py`
Expected: `all OK`.

Run: `git grep -n "\.reply_to(" -- bot/` (from repo root)
Expected: no output at all now — `reply_to` was deleted in Task 2 and its last two callers are gone.

- [ ] **Step 4: Commit**

```bash
git add bot/handlers.py bot/worker/waiters.py
git commit -m "Route audio pager delivery through redeliver_page in handlers"
```

---

### Task 5: Wire `bot/worker/actors.py` to `redeliver_page`

**Files:**
- Modify: `bot/worker/actors.py` (import block; `_notify_audio_page_waiters_success` at `bot/worker/actors.py:111-113`; its call site inside `_process_audio_page_async` at `bot/worker/actors.py:294-321`)
- Test: scratchpad script (not committed)

**Interfaces:**
- Consumes: `redeliver_page(redis_client, bot, chat_id, root_message_id, audio, page)` from Task 3; `Waiter.reply_to_message_id` as the root, per Task 4.
- Produces: `_notify_audio_page_waiters_success(redis_client: redis.Redis, bot: Bot, waiters: list[Waiter], audio: AudioRequestData, page: int) -> None` — gains a `redis_client` first parameter (it previously took none).

- [ ] **Step 1: Write the failing script**

Save as `<scratchpad>/verify_notify_audio_page_waiters.py`:

```python
import asyncio
from unittest.mock import AsyncMock, patch

import bot.worker.actors as actors_mod
from bot.worker.waiters import Waiter


async def main():
    redis_client = AsyncMock()
    bot = AsyncMock()
    audio = AsyncMock()
    waiters = [
        Waiter(chat_id=1, chat_type="private", reply_to_message_id=111),
        Waiter(chat_id=2, chat_type="group", reply_to_message_id=222),
    ]

    with patch.object(actors_mod, "redeliver_page", new=AsyncMock()) as mock_redeliver:
        await actors_mod._notify_audio_page_waiters_success(redis_client, bot, waiters, audio, page=2)

    assert mock_redeliver.call_count == 2
    assert mock_redeliver.call_args_list[0].args == (redis_client, bot, 1, 111, audio, 2)
    assert mock_redeliver.call_args_list[1].args == (redis_client, bot, 2, 222, audio, 2)
    print("all OK")


asyncio.run(main())
```

Run: `uv run python <scratchpad_path>/verify_notify_audio_page_waiters.py`
Expected: `TypeError: _notify_audio_page_waiters_success() takes 4 positional arguments but 5 were given` (current signature has no `redis_client` param).

- [ ] **Step 2: Implement**

In `bot/worker/actors.py`, add to the import block (near the other `bot.util.audio` import):

```python
from bot.util.audio.pager import redeliver_page
```

Replace `bot/worker/actors.py:111-113` with:

```python
async def _notify_audio_page_waiters_success(
    redis_client: redis.Redis, bot: Bot, waiters: list[Waiter], audio: AudioRequestData, page: int,
) -> None:
    for waiter in waiters:
        await redeliver_page(redis_client, bot, waiter.chat_id, waiter.reply_to_message_id, audio, page)
```

In `_process_audio_page_async` (`bot/worker/actors.py:294-321`), update the call site — find:

```python
            waiters = await _pop_waiters(redis_client, page_key)
            await _notify_audio_page_waiters_success(bot, waiters, audio, page)
```

and change to:

```python
            waiters = await _pop_waiters(redis_client, page_key)
            await _notify_audio_page_waiters_success(redis_client, bot, waiters, audio, page)
```

- [ ] **Step 3: Run the script again**

Run: `uv run python <scratchpad_path>/verify_notify_audio_page_waiters.py`
Expected: `all OK`, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add bot/worker/actors.py
git commit -m "Thread redis_client through audio page waiter notification"
```

---

### Task 6: Manual end-to-end verification

**Files:** none (verification only)

**Interfaces:**
- Consumes: the fully-wired feature from Tasks 1-5.
- Produces: a go/no-go signal for the branch. Nothing downstream depends on this task's code (there is none), but treat it as blocking before considering the feature done.

- [ ] **Step 1: Start the bot locally**

Run: `docker compose up -d` (brings up Redis) then `uv run main.py` (per `CLAUDE.md`), with a valid `.env` (`BOT_TOKEN`, `DUMP_CHAT_ID`).

- [ ] **Step 2: Fresh multi-page playlist link**

Send a playlist link with more than 10 audio tracks (e.g. a YouTube playlist / SoundCloud set the bot's audio pipeline recognizes) to the bot in a private chat.

Expected: page 1 arrives as a media group replying to your link message, first track's caption shows `🔗 Source` (and `⚠️ N unavailable` if any track failed), followed by a buttons-only message with no visible text and a `[1/M] [Next ▶️]` row that is **not** shown as a reply to anything.

- [ ] **Step 3: Page forward and back repeatedly**

Tap Next, then Next again, then Back twice.

Expected at every step: exactly one page's worth of messages is visible in the chat (the previous page's media + buttons message are gone, not just button-stripped); each new media message shows as a reply to your *original* link message, never to a previous page's message; the buttons row still shows no reply arrow.

- [ ] **Step 4: Cache-miss page turn**

Find or contrive a playlist where a later page hasn't been downloaded yet (a page you haven't visited before on a freshly-cached playlist). Tap into it.

Expected: the tapped page's buttons disappear immediately (no double-tap possible), the bot eventually delivers the new page as in Step 3, and the page you tapped from is deleted once the new one lands — not left behind.

- [ ] **Step 5: Forwarded page still traces to source**

Forward a delivered page (media message) to another chat (e.g. Saved Messages).

Expected: the forwarded message(s) show the source-link caption on the first track (buttons will not survive the forward — that's expected and matches Telegram's own behavior, not a regression).

- [ ] **Step 6: Single-track result**

Send a link that resolves to exactly one audio track.

Expected: one message total — the audio with its caption (source link, and `⚠️ 1 unavailable`-style warning only if applicable) and no buttons row (since `total_pages <= 1`).
