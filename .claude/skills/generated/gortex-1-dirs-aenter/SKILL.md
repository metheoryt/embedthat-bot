---
name: gortex-1-dirs-aenter
description: "Work in the . +1 dirs · __aenter__ area — 11 symbols across 2 files (86% cohesion)"
---

# . +1 dirs · __aenter__

11 symbols | 2 files | 86% cohesion

## When to Use

Use this skill when working on files in:
- ``
- `bot\util\redis_lock.py`

## Key Files

| File | Symbols |
|------|---------|
| `` | create_task |
| `bot\util\redis_lock.py` | HeartbeatLock, __init__, interval, exc, exc_type, ... |

## Connected Communities

- **. +3 dirs** (1 cross-edges)

## How to Explore

```
get_communities with id: "community-47"
smart_context with task: "understand . +1 dirs · __aenter__", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
