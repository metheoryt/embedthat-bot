---
name: gortex-1-dirs-process-youtube-link
description: "Work in the . +1 dirs · process_youtube_link area — 13 symbols across 7 files (91% cohesion)"
---

# . +1 dirs · process_youtube_link

13 symbols | 7 files | 91% cohesion

## When to Use

Use this skill when working on files in:
- ``
- `bot\worker\actors.py`
- `external-call::dep:aiogram.Bot`
- `external-call::dep:aiogram.client.default.DefaultBotProperties`
- `external-call::dep:bot.dispatcher.dp`
- `external-call::dep:bot.events.freeze_signals`
- `main.py`

## Key Files

| File | Symbols |
|------|---------|
| `` | run |
| `bot\worker\actors.py` | chat_id, url, target_lang, chat_id, link, ... |
| `external-call::dep:aiogram.Bot` | aiogram.Bot |
| `external-call::dep:aiogram.client.default.DefaultBotProperties` | aiogram.client.default.DefaultBotProperties |
| `external-call::dep:bot.dispatcher.dp` | bot.dispatcher.dp |
| `external-call::dep:bot.events.freeze_signals` | bot.events.freeze_signals |
| `main.py` | main |

## Entry Points

- `main.py::main`
- `bot\worker\actors.py::process_youtube_link`
- `bot\worker\actors.py::process_social_link`

## Connected Communities

- **. +1 dirs · _process_youtube_link_async** (2 cross-edges)

## How to Explore

```
get_communities with id: "community-48"
smart_context with task: "understand . +1 dirs · process_youtube_link", format: "gcx"
find_usages with id: "main.py::main", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
