---
name: gortex-1-dirs-process-youtube-link-async
description: "Work in the . +1 dirs · _process_youtube_link_async area — 39 symbols across 11 files (88% cohesion)"
---

# . +1 dirs · _process_youtube_link_async

39 symbols | 11 files | 88% cohesion

## When to Use

Use this skill when working on files in:
- `bot\worker\actors.py`
- `bot\worker\waiters.py`
- `external-call::dep:bot.events.signals.on_social_video_sent`
- `external-call::dep:bot.events.signals.on_yt_video_sent`
- `external-call::dep:bot.util.redis_lock.HeartbeatLock`
- `external-call::dep:bot.util.social.schema.SocialVideoData`
- `external-call::dep:bot.util.youtube.enum.TargetLang`
- `external-call::dep:bot.util.youtube.schema.YouTubeVideoData`
- `external-call::dep:bot.worker.pipeline.handle_social_video`
- `external-call::dep:bot.worker.pipeline.handle_youtube_video`
- `external-call::dep:bot.worker.waiters.pop_waiters`

## Key Files

| File | Symbols |
|------|---------|
| `bot\worker\actors.py` | chat_id, _safe_edit_ack, bot, url, bot, ... |
| `bot\worker\waiters.py` | Waiter, pop_waiters, redis_client, cache_key |
| `external-call::dep:bot.events.signals.on_social_video_sent` | bot.events.signals.on_social_video_sent |
| `external-call::dep:bot.events.signals.on_yt_video_sent` | bot.events.signals.on_yt_video_sent |
| `external-call::dep:bot.util.redis_lock.HeartbeatLock` | bot.util.redis_lock.HeartbeatLock |
| `external-call::dep:bot.util.social.schema.SocialVideoData` | bot.util.social.schema.SocialVideoData |
| `external-call::dep:bot.util.youtube.enum.TargetLang` | bot.util.youtube.enum.TargetLang |
| `external-call::dep:bot.util.youtube.schema.YouTubeVideoData` | bot.util.youtube.schema.YouTubeVideoData |
| `external-call::dep:bot.worker.pipeline.handle_social_video` | bot.worker.pipeline.handle_social_video |
| `external-call::dep:bot.worker.pipeline.handle_youtube_video` | bot.worker.pipeline.handle_youtube_video |
| `external-call::dep:bot.worker.waiters.pop_waiters` | bot.worker.waiters.pop_waiters |

## Entry Points

- `bot\worker\waiters.py::pop_waiters`
- `bot\worker\actors.py::_process_youtube_link_async`
- `bot\worker\actors.py::_process_social_link_async`

## Connected Communities

- **. +2 dirs · _period_stats** (2 cross-edges)
- **. +5 dirs** (2 cross-edges)
- **bot\worker** (1 cross-edges)

## How to Explore

```
get_communities with id: "community-15"
smart_context with task: "understand . +1 dirs · _process_youtube_link_async", format: "gcx"
find_usages with id: "bot\worker\waiters.py::pop_waiters", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
