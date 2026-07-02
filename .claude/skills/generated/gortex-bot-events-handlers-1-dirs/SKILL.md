---
name: gortex-bot-events-handlers-1-dirs
description: "Work in the bot\events\handlers +1 dirs area — 22 symbols across 2 files (82% cohesion)"
---

# bot\events\handlers +1 dirs

22 symbols | 2 files | 82% cohesion

## When to Use

Use this skill when working on files in:
- `bot\events\handlers\share.py`
- `bot\util\social\schema.py`

## Key Files

| File | Symbols |
|------|---------|
| `bot\events\handlers\share.py` | chat_type, fresh, link, chat_id, share_yt_shorts, ... |
| `bot\util\social\schema.py` | caption, reply_to_message_id, chat_id, message, bot, ... |

## Entry Points

- `bot\util\social\schema.py::SocialVideoData.send_to_chat`
- `bot\util\social\schema.py::SocialVideoData.reply_to`

## Connected Communities

- **. +3 dirs** (2 cross-edges)

## How to Explore

```
get_communities with id: "community-7"
smart_context with task: "understand bot\events\handlers +1 dirs", format: "gcx"
find_usages with id: "bot\util\social\schema.py::SocialVideoData.send_to_chat", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
