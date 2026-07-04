# Audio pager: delete-and-resend instead of reply-chained messages

## Context

Today, tapping Back/Next on an audio playlist page (`bot/util/audio/schema.py`,
`bot/handlers.py::get_audio_page`, `bot/worker/actors.py`) sends a **new**
message (or two: media + a follow-up "page N/M" message) that **replies to**
the tapped message. Over several page turns this builds a visible reply-chain
of stale pager messages, all still sitting in the chat with (until the very
last one) their buttons cleared.

This spec replaces that with: each page turn sends the new page's message(s),
then deletes the previous page's message(s). Only one page's worth of
messages is ever live in the chat at a time.

## Goals

1. Replacing a page removes the previous page's messages instead of leaving
   a growing reply-chain behind.
2. The media message(s) for every page reply to the same anchor: the user's
   original link message — not to whatever pager message happened to be
   tapped.
3. The buttons message (when one exists) is a plain follow-up, not a reply to
   anything.
4. The buttons message carries no text of its own (Telegram requires
   non-empty text, so a placeholder is used) — the "N unavailable" warning
   and the source-link (added in `2026-07-04-audio-pager-concurrency-design.md`
   for forward-traceability) both move to the **caption of the first media
   item** instead. This is a strict improvement for that goal: a caption
   travels with a forwarded media message, whereas the old separate
   text-message wasn't part of the media forward at all.

## Non-goals

- No change to download concurrency (`handle_audio_page`) — covered by the
  prior spec.
- No change to Instagram/TikTok/Twitter/YouTube handling.
- No true in-place `editMessageMedia` — ruled out: it can't change the number
  of items in an album, can't turn an audio album into a single-audio message
  or back (last page is often shorter than a full page; a 1-track page uses
  a wholly different message shape than a multi-track page), and there's no
  bulk "edit this album" call in the Bot API (each item is an independent
  message under the same `media_group_id`). Delete-and-resend sidesteps all
  of this uniformly.

## Design

### 1. Root message as the stable anchor

Every pager thread is anchored to the user's original link message
(`root_message_id`). All media for every page in that thread replies to the
root, never to a previous page's message. This must be threaded through page
turns since a callback tap only gives us the *current* (about-to-be-deleted)
message, not the original one.

`pager_markup` gains a `root_message_id` param and encodes it in
`callback_data`:

```python
def pager_markup(self, page: int, root_message_id: int) -> types.InlineKeyboardMarkup | None:
    ...
    callback_data=f"apg:{self.hash16}:{page - 1}:{root_message_id}"
    ...
```

`apg:{hash16}:{page}:{root_message_id}` — 16 + up-to-3 + up-to-10 digits plus
separators, well under Telegram's 64-byte `callback_data` limit.
`get_audio_page` parses 4 colon-separated parts instead of 3 and threads
`root_message_id` onward.

### 2. Tracking "what's currently live" (Redis)

New key: `da:msgs:{chat_id}:{root_message_id}` → JSON list of message_ids
that make up the currently-displayed page for that pager thread (media
message(s), plus the nav message if one was sent). No TTL, matching the
existing `da:{hash16}` cache entry (also untimed).

This key is per-`(chat_id, root_message_id)`, not per-`(chat_id, hash16)` —
two different users posting the *same* link in the same group chat get two
different root messages and therefore two independent, non-colliding pager
threads.

### 3. `send_to_chat` consolidation, `reply_to` removed

`reply_to(message, page)` and `send_to_chat(bot, chat_id, reply_to_message_id, page)`
are near-duplicates that exist only because one call site had a `Message` to
reply to and another only had a `chat_id`. Since every call site can supply
`reply_to_message_id` (it's now mandatory — always the root), `reply_to` is
deleted; its former call sites go through `redeliver_page` (section 5)
instead, which calls `send_to_chat` internally. `send_to_chat` returns the
list of message_ids it just sent (new return type), which is what makes
`redeliver_page` possible.

### 4. Caption and nav-message placement

`_pager_caption(skipped)` keeps both pieces of info, always:

```python
def _pager_caption(self, skipped: int) -> str:
    parts = []
    if skipped:
        parts.append(f"⚠️ {skipped} unavailable")
    parts.append(f'🔗 <a href="{html.escape(self.link)}">Source</a>')
    return "\n".join(parts)
```

- **Single-track page** (`len(deliverable) == 1`): one message — the audio,
  with `caption=self._pager_caption(skipped)` and `reply_markup=markup`
  (markup is `None` when `total_pages <= 1`) attached directly. No nav
  message, same as today's single-message case.
- **Multi-track page**: `sendMediaGroup` has no `reply_markup` and Telegram
  shows only the first item's caption in the UI, so the *first* element of
  `[t.as_input_media for t in deliverable]` gets `caption`/`parse_mode="HTML"`
  set to `_pager_caption(skipped)`; the rest are unchanged. This caption is
  now unconditional (previously gated on `markup or skipped`) — every
  multi-track page shows the source link on its first track, pager or not.
  A nav message — placeholder text (e.g. a single invisible character),
  `reply_markup=markup`, **not a reply** to anything — is sent as a
  follow-up **only when `markup` is not `None`** (`total_pages > 1`).

### 5. `redeliver_page` — shared send/track/delete orchestration

New function, `bot/util/audio/pager.py::redeliver_page`, callable from both
the router layer (`bot/handlers.py`) and the worker layer
(`bot/worker/actors.py`) without a circular import (neither layer imports the
other; both already import from `bot/util/audio/`):

```python
async def redeliver_page(
    redis_client: Redis, bot: Bot, chat_id: int, root_message_id: int,
    audio: AudioRequestData, page: int,
) -> None:
    key = f"da:msgs:{chat_id}:{root_message_id}"
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

Send-then-delete, not delete-then-send: if the new send fails, the user still
has the old page rather than nothing. On the very first delivery for a
thread, `old_ids` is empty and the delete loop is a no-op — no special-casing
needed for "first send vs. page turn."

All three call sites route through this:
- `_process_social_url` (cache hit on a fresh link) — `root_message_id = message.message_id`.
- `get_audio_page` (cache hit on a page tap) — `root_message_id` parsed from `callback_data`.
- `_notify_audio_page_waiters_success` (cache-miss resolved async) —
  `root_message_id = waiter.reply_to_message_id`, which already exists on
  `Waiter` and, per goal 2, is always the original root (not the tapped
  message) once `get_audio_page` is updated to register waiters with the
  root instead of `callback.message.message_id`.

### 6. Duplicate-tap guard

`get_audio_page` clears the tapped message's `reply_markup` immediately,
before checking cache-hit/miss, same as today — this stays unconditional for
**both** branches, not scoped to cache-miss only. Reasoning: on a cache hit,
`redeliver_page` still does a network round-trip to send the new page before
it deletes the old one; a fast repeat tap in that window would run
`get_audio_page` twice, and since both invocations read-then-overwrite the
same Redis key, one of the two newly-sent pages would end up permanently
untracked and never deleted. Clearing the markup immediately (before either
branch runs) closes that race — the one wasted edit-call on the cache-hit
path (the message is deleted moments later anyway) is a cheap price for it.

## Files touched

- `bot/util/audio/schema.py` — `pager_markup` (new param), `_pager_caption`
  (unconditional, unchanged content), `send_to_chat` (returns message_ids,
  first-item caption for media groups, nav-message-only-if-markup,
  nav message no longer a reply), `reply_to` (deleted).
- `bot/util/audio/pager.py` — new file, `redeliver_page`.
- `bot/handlers.py` — `get_audio_page` (4-part callback_data, root threading,
  registers waiters with `root_message_id` instead of
  `callback.message.message_id`, duplicate-tap guard scoped to cache-miss
  only, calls `redeliver_page`), `_process_social_url` (calls
  `redeliver_page` instead of `reply_to`).
- `bot/worker/actors.py` — `_notify_audio_page_waiters_success` calls
  `redeliver_page` instead of `audio.send_to_chat` directly.
- `bot/worker/waiters.py` — no field changes; `Waiter.reply_to_message_id`
  is reused as the root, semantics documented in a comment.

## Testing

No test suite exists in this repo (per `CLAUDE.md`). Verification:

- Scratchpad script: `pager_markup(page, root_message_id)` produces the
  4-part `callback_data` and still returns `None` at `total_pages <= 1`;
  `get_audio_page`'s callback-data parsing round-trips a sample string.
- Manual, against the running bot: send a multi-page playlist link, tap
  Next/Back repeatedly — confirm only one page's messages are ever live,
  each media message replies to the original link message (not the previous
  page), the nav message shows no reply-arrow, the first track's caption
  carries the source link (and the warning when tracks are skipped), and a
  cache-miss page turn (cold cache) still resolves and deletes the stale
  "waiting" state correctly.
