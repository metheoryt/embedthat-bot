---
name: gortex-bot-events-handlers
description: "Work in the bot\events\handlers area — 24 symbols across 1 files (92% cohesion)"
---

# bot\events\handlers

24 symbols | 1 files | 92% cohesion

## When to Use

Use this skill when working on files in:
- `bot\events\handlers\stats.py`

## Key Files

| File | Symbols |
|------|---------|
| `bot\events\handlers\stats.py` | chat_type, bot, message, chat_id, stats_yt_fail, ... |

## Entry Points

- `bot\events\handlers\stats.py::stats_link_received`
- `bot\events\handlers\stats.py::stats_social_sent`
- `bot\events\handlers\stats.py::stats_yt_sent`
- `bot\events\handlers\stats.py::stats_social_fail`
- `bot\events\handlers\stats.py::stats_yt_fail`

## Connected Communities

- **. +2 dirs · _period_stats** (3 cross-edges)
- **. +2 dirs · build_stats_report** (1 cross-edges)

## How to Explore

```
get_communities with id: "community-0"
smart_context with task: "understand bot\events\handlers", format: "gcx"
find_usages with id: "bot\events\handlers\stats.py::stats_link_received", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
