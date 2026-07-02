---
name: gortex-2-dirs-download-social-video
description: "Work in the . +2 dirs · download_social_video area — 13 symbols across 5 files (93% cohesion)"
---

# . +2 dirs · download_social_video

13 symbols | 5 files | 93% cohesion

## When to Use

Use this skill when working on files in:
- `bot\util\social\download.py`
- `bot\util\social\exc.py`
- `bot\util\youtube\video.py`
- `external-call::stdlib:ffmpeg`
- `external-call::stdlib:yt_dlp`

## Key Files

| File | Symbols |
|------|---------|
| `bot\util\social\download.py` | file_path, url, file_path, download_social_video, output_dir, ... |
| `bot\util\social\exc.py` | SocialDownloadError |
| `bot\util\youtube\video.py` | get_resolution, stream |
| `external-call::stdlib:ffmpeg` | ffmpeg |
| `external-call::stdlib:yt_dlp` | yt_dlp |

## Entry Points

- `bot\util\social\download.py::download_social_video`
- `bot\util\social\download.py::_probe_duration`

## How to Explore

```
get_communities with id: "community-17"
smart_context with task: "understand . +2 dirs · download_social_video", format: "gcx"
find_usages with id: "bot\util\social\download.py::download_social_video", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
