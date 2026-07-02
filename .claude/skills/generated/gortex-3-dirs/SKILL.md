---
name: gortex-3-dirs
description: "Work in the . +3 dirs area — 31 symbols across 10 files (81% cohesion)"
---

# . +3 dirs

31 symbols | 10 files | 81% cohesion

## When to Use

Use this skill when working on files in:
- ``
- `bot\handlers.py`
- `bot\util\youtube\schema.py`
- `bot\worker\pipeline.py`
- `external-call::dep:aiogram.types`
- `external-call::dep:bot.events.signals.on_social_video_fail`
- `external-call::dep:bot.events.signals.on_yt_video_fail`
- `external-call::dep:bot.util.social.exc.SocialDownloadError`
- `external-call::dep:bot.util.youtube.video.get_resolution`
- `external-call::dep:bot.util.youtube.video.split_video`

## Key Files

| File | Symbols |
|------|---------|
| `` | math, sleep, asyncio, ceil, TemporaryDirectory, ... |
| `bot\handlers.py` | message, video, video, handle_youtube_video, message, ... |
| `bot\util\youtube\schema.py` | media_group |
| `bot\worker\pipeline.py` | width, height, handle_youtube_video, handle_social_video, bot, ... |
| `external-call::dep:aiogram.types` | aiogram.types |
| `external-call::dep:bot.events.signals.on_social_video_fail` | bot.events.signals.on_social_video_fail |
| `external-call::dep:bot.events.signals.on_yt_video_fail` | bot.events.signals.on_yt_video_fail |
| `external-call::dep:bot.util.social.exc.SocialDownloadError` | bot.util.social.exc.SocialDownloadError |
| `external-call::dep:bot.util.youtube.video.get_resolution` | bot.util.youtube.video.get_resolution |
| `external-call::dep:bot.util.youtube.video.split_video` | bot.util.youtube.video.split_video |

## Entry Points

- `bot\worker\pipeline.py::handle_social_video`
- `bot\worker\pipeline.py::handle_youtube_video`
- `bot\handlers.py::handle_social_video`
- `bot\handlers.py::handle_youtube_video`
- `bot\util\youtube\schema.py::media_group`

## Connected Communities

- **bot\util\youtube +1 dirs · pick_stream** (2 cross-edges)

## How to Explore

```
get_communities with id: "community-49"
smart_context with task: "understand . +3 dirs", format: "gcx"
find_usages with id: "bot\worker\pipeline.py::handle_social_video", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
