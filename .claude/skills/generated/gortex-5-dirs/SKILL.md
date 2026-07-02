---
name: gortex-5-dirs
description: "Work in the . +5 dirs area — 35 symbols across 9 files (76% cohesion)"
---

# . +5 dirs

35 symbols | 9 files | 76% cohesion

## When to Use

Use this skill when working on files in:
- `bot\enum.py`
- `bot\events\handlers\log.py`
- `bot\handlers.py`
- `bot\util\chat_action.py`
- `bot\util\youtube\enum.py`
- `bot\util\youtube\schema.py`
- `bot\worker\chat_action.py`
- `external-call::dep:pytubefix.YouTube`
- `external-call::dep:redis.asyncio.lock.Lock`

## Key Files

| File | Symbols |
|------|---------|
| `bot\enum.py` | LinkOrigin |
| `bot\events\handlers\log.py` | log_link, origin, message |
| `bot\handlers.py` | message, embed_youtube_videos, embed_social, message, message, ... |
| `bot\util\chat_action.py` | action, chat_id, bot, action_task, send_chat_action_periodically |
| `bot\util\youtube\enum.py` | TargetLang |
| `bot\util\youtube\schema.py` | caption, reply_to, bot, YouTubeVideoData, chat_id, ... |
| `bot\worker\chat_action.py` | kwargs, chat_id, args, bot, wrapper |
| `external-call::dep:pytubefix.YouTube` | pytubefix.YouTube |
| `external-call::dep:redis.asyncio.lock.Lock` | redis.asyncio.lock.Lock |

## Entry Points

- `bot\handlers.py::embed_youtube_videos`
- `bot\util\chat_action.py::send_chat_action_periodically`
- `bot\handlers.py::embed_social`
- `bot\handlers.py::_process_social_url`
- `bot\util\youtube\schema.py::YouTubeVideoData.send_to_chat`

## Connected Communities

- **. +3 dirs** (3 cross-edges)
- **. +1 dirs · __aenter__** (1 cross-edges)
- **. +2 dirs · _social_cache_key** (1 cross-edges)

## How to Explore

```
get_communities with id: "community-3"
smart_context with task: "understand . +5 dirs", format: "gcx"
find_usages with id: "bot\handlers.py::embed_youtube_videos", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
