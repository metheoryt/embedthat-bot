---
name: gortex-bot-worker
description: "Work in the bot\worker area — 7 symbols across 1 files (85% cohesion)"
---

# bot\worker

7 symbols | 1 files | 85% cohesion

## When to Use

Use this skill when working on files in:
- `bot\worker\waiters.py`

## Key Files

| File | Symbols |
|------|---------|
| `bot\worker\waiters.py` | ttl, waiter, _waiters_key, cache_key, cache_key, ... |

## Entry Points

- `bot\worker\waiters.py::register_waiter`

## How to Explore

```
get_communities with id: "community-11"
smart_context with task: "understand bot\worker", format: "gcx"
find_usages with id: "bot\worker\waiters.py::register_waiter", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
