---
name: gortex-1-dirs-with-chat-action
description: "Work in the . +1 dirs · with_chat_action area — 7 symbols across 3 files (95% cohesion)"
---

# . +1 dirs · with_chat_action

7 symbols | 3 files | 95% cohesion

## When to Use

Use this skill when working on files in:
- ``
- `bot\worker\chat_action.py`
- `external-call::dep:bot.util.chat_action.send_chat_action_periodically`

## Key Files

| File | Symbols |
|------|---------|
| `` | wraps, functools.wraps |
| `bot\worker\chat_action.py` | with_chat_action, action, func, decorator |
| `external-call::dep:bot.util.chat_action.send_chat_action_periodically` | bot.util.chat_action.send_chat_action_periodically |

## Entry Points

- `bot\worker\chat_action.py::with_chat_action`

## How to Explore

```
get_communities with id: "community-14"
smart_context with task: "understand . +1 dirs · with_chat_action", format: "gcx"
find_usages with id: "bot\worker\chat_action.py::with_chat_action", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
