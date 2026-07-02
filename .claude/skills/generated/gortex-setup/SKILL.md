---
name: gortex-setup
description: "Work in the . · setup area — 6 symbols across 3 files (100% cohesion)"
---

# . · setup

6 symbols | 3 files | 100% cohesion

## When to Use

Use this skill when working on files in:
- ``
- `external-call::dep:dotenv.load_dotenv`
- `main.py`

## Key Files

| File | Symbols |
|------|---------|
| `` | basicConfig, logging, setLevel, getLogger |
| `external-call::dep:dotenv.load_dotenv` | dotenv.load_dotenv |
| `main.py` | setup |

## Entry Points

- `main.py::setup`

## How to Explore

```
get_communities with id: "community-18"
smart_context with task: "understand . · setup", format: "gcx"
find_usages with id: "main.py::setup", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
