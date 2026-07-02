---
name: gortex-1-dirs-mix-audio
description: "Work in the . +1 dirs · mix_audio area — 6 symbols across 2 files (93% cohesion)"
---

# . +1 dirs · mix_audio

6 symbols | 2 files | 93% cohesion

## When to Use

Use this skill when working on files in:
- `bot\util\youtube\translate.py`
- `external-call::dep:pydub.AudioSegment`

## Key Files

| File | Symbols |
|------|---------|
| `bot\util\youtube\translate.py` | output_path, translated_audio_path, mix_audio, original_volume_db, original_audio_path |
| `external-call::dep:pydub.AudioSegment` | pydub.AudioSegment |

## Entry Points

- `bot\util\youtube\translate.py::mix_audio`

## How to Explore

```
get_communities with id: "community-16"
smart_context with task: "understand . +1 dirs · mix_audio", format: "gcx"
find_usages with id: "bot\util\youtube\translate.py::mix_audio", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
