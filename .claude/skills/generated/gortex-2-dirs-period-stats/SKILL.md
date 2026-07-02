---
name: gortex-2-dirs-period-stats
description: "Work in the . +2 dirs · _period_stats area — 8 symbols across 3 files (77% cohesion)"
---

# . +2 dirs · _period_stats

8 symbols | 3 files | 77% cohesion

## When to Use

Use this skill when working on files in:
- `bot\events\handlers\stats.py`
- `bot\util\stats.py`
- `external-call::dep:bot.util.redis.redis_client`

## Key Files

| File | Symbols |
|------|---------|
| `bot\events\handlers\stats.py` | _sadd, value, key |
| `bot\util\stats.py` | dates, key_suffix, _period_stats, s |
| `external-call::dep:bot.util.redis.redis_client` | bot.util.redis.redis_client |

## Entry Points

- `bot\util\stats.py::_period_stats`

## How to Explore

```
get_communities with id: "community-9"
smart_context with task: "understand . +2 dirs · _period_stats", format: "gcx"
find_usages with id: "bot\util\stats.py::_period_stats", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
