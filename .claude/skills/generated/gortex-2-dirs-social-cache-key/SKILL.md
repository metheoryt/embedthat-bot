---
name: gortex-2-dirs-social-cache-key
description: "Work in the . +2 dirs · _social_cache_key area — 6 symbols across 3 files (92% cohesion)"
---

# . +2 dirs · _social_cache_key

6 symbols | 3 files | 92% cohesion

## When to Use

Use this skill when working on files in:
- ``
- `bot\handlers.py`
- `bot\util\social\schema.py`

## Key Files

| File | Symbols |
|------|---------|
| `` | hashlib, sha256, hexdigest |
| `bot\handlers.py` | link, _social_cache_key |
| `bot\util\social\schema.py` | cache_key |

## Entry Points

- `bot\util\social\schema.py::cache_key`

## How to Explore

```
get_communities with id: "community-51"
smart_context with task: "understand . +2 dirs · _social_cache_key", format: "gcx"
find_usages with id: "bot\util\social\schema.py::cache_key", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
