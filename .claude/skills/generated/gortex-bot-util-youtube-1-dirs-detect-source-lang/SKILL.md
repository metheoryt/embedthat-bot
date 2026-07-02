---
name: gortex-bot-util-youtube-1-dirs-detect-source-lang
description: "Work in the bot\util\youtube +1 dirs · detect_source_lang area — 5 symbols across 3 files (84% cohesion)"
---

# bot\util\youtube +1 dirs · detect_source_lang

5 symbols | 3 files | 84% cohesion

## When to Use

Use this skill when working on files in:
- `bot\util\youtube\enum.py`
- `bot\util\youtube\translate.py`
- `external-call::dep:faster_whisper.WhisperModel`

## Key Files

| File | Symbols |
|------|---------|
| `bot\util\youtube\enum.py` | SourceLang |
| `bot\util\youtube\translate.py` | audio_path, detect_source_lang, _get_whisper_model |
| `external-call::dep:faster_whisper.WhisperModel` | faster_whisper.WhisperModel |

## Entry Points

- `bot\util\youtube\translate.py::detect_source_lang`

## How to Explore

```
get_communities with id: "community-10"
smart_context with task: "understand bot\util\youtube +1 dirs · detect_source_lang", format: "gcx"
find_usages with id: "bot\util\youtube\translate.py::detect_source_lang", format: "gcx"
```

_`format: "gcx"` returns the [GCX1 compact wire format](../../docs/wire-format.md) — round-trippable, ~27% fewer tokens than JSON. Drop it for JSON output; agents using `@gortex/wire` or the Go `github.com/gortexhq/gcx-go` package decode either._
