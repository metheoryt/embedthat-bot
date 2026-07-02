---
name: gortex-1-dirs-now
description: "Work in the . +1 dirs · now area — 6 symbols across 3 files (100% cohesion)"
---

# . +1 dirs · now

6 symbols | 3 files | 100% cohesion

## When to Use

Use this skill when working on files in:
- ``
- `bot\config.py`
- `external-call::dep:zoneinfo.ZoneInfo`

## Key Files

| File | Symbols |
|------|---------|
| `` | datetime.datetime, now |
| `bot\config.py` | Settings, now, timezone |
| `external-call::dep:zoneinfo.ZoneInfo` | zoneinfo.ZoneInfo |

## Entry Points

- `bot\config.py::timezone`

## How to Explore

```
get_communities with id: "community-12"
smart_context with task: "understand . +1 dirs · now", format: "gcx"
find_usages with id: "bot\config.py::timezone", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
