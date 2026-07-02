---
name: gortex-bot-util-youtube-1-dirs-pick-stream
description: "Work in the bot\util\youtube +1 dirs · pick_stream area — 29 symbols across 4 files (87% cohesion)"
---

# bot\util\youtube +1 dirs · pick_stream

29 symbols | 4 files | 87% cohesion

## When to Use

Use this skill when working on files in:
- ``
- `bot\util\youtube\exc.py`
- `bot\util\youtube\translate.py`
- `bot\util\youtube\video.py`

## Key Files

| File | Symbols |
|------|---------|
| `` | Path, subprocess, pathlib.Path, run |
| `bot\util\youtube\exc.py` | YouTubeError |
| `bot\util\youtube\translate.py` | yt, source_audio_path, output_dir, maybe_translate_audio, target_lang, ... |
| `bot\util\youtube\video.py` | output_path, pick_stream, video, output_dir, min_res, ... |

## Entry Points

- `bot\util\youtube\video.py::pick_stream`
- `bot\util\youtube\video.py::check_download_adaptive`
- `bot\util\youtube\video.py::split_video`
- `bot\util\youtube\translate.py::maybe_translate_audio`
- `bot\util\youtube\translate.py::translate_audio`

## Connected Communities

- **bot\util\youtube +1 dirs · detect_source_lang** (1 cross-edges)
- **. +1 dirs · mix_audio** (1 cross-edges)
- **. +2 dirs · download_social_video** (1 cross-edges)
- **. +3 dirs** (1 cross-edges)

## How to Explore

```
get_communities with id: "community-13"
smart_context with task: "understand bot\util\youtube +1 dirs · pick_stream", format: "gcx"
find_usages with id: "bot\util\youtube\video.py::pick_stream", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
