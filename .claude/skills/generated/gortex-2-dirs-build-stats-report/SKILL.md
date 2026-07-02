---
name: gortex-2-dirs-build-stats-report
description: "Work in the . +2 dirs · build_stats_report area — 13 symbols across 4 files (86% cohesion)"
---

# . +2 dirs · build_stats_report

13 symbols | 4 files | 86% cohesion

## When to Use

Use this skill when working on files in:
- ``
- `bot\handlers.py`
- `bot\util\stats.py`
- `external-call::dep:bot.config.settings`

## Key Files

| File | Symbols |
|------|---------|
| `` | gather, timedelta, datetime.timedelta |
| `bot\handlers.py` | cmd_stats, message |
| `bot\util\stats.py` | build_stats_report, stats, end, _fmt_section, _date_range, ... |
| `external-call::dep:bot.config.settings` | bot.config.settings |

## Entry Points

- `bot\util\stats.py::build_stats_report`
- `bot\handlers.py::cmd_stats`

## Connected Communities

- **. +2 dirs · _period_stats** (3 cross-edges)

## How to Explore

```
get_communities with id: "community-8"
smart_context with task: "understand . +2 dirs · build_stats_report", format: "gcx"
find_usages with id: "bot\util\stats.py::build_stats_report", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
