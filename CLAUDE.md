# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Embed That! Bot** is a Telegram bot that converts social media links (YouTube, Instagram, TikTok, Twitter/X) into playable embeds or native Telegram videos. Live at https://t.me/embedthat_bot.

## Commands

```bash
# Install dependencies
uv sync

# Run the bot locally (requires .env with BOT_TOKEN and DUMP_CHAT_ID)
uv run main.py

# Run with Docker Compose (includes Redis)
docker compose up -d

# Build Docker image
docker build -t metheoryt/embedthat-bot:latest .
```

There is no test suite or linter configured.

## Environment Setup

Copy `.env.dist` to `.env` and populate:
- `BOT_TOKEN` — Telegram bot token (required)
- `DUMP_CHAT_ID` — Telegram chat ID for temporary video storage (required); the bot sends videos here first to obtain Telegram `file_id`s for caching
- `REDIS_URL` — Redis connection string (default: `redis://redis`)
- `FEED_CHANNEL_ID`, `ADMIN_CHAT_ID` — optional

## Architecture

### Request Flow

1. User sends a social media link to the bot
2. `bot/handlers.py` routes the message by detected `LinkOrigin`
3. **Instagram/TikTok**: domain is rewritten to a proxy embedding service and sent back as a link
4. **Twitter/X**: domain is replaced with fxtwitter.com / fixupx.com
5. **YouTube**: full download-and-upload pipeline (see below)
6. Signals in `bot/events/signals.py` trigger cross-cutting handlers (logging in `log.py`, optional feed channel sharing in `share.py`)

### YouTube Pipeline (`bot/util/youtube/`)

- `video.py` — main orchestration: selects best adaptive stream within Telegram's 50 MB limit, downloads video and audio separately, merges with FFmpeg, splits into ≤50 MB parts if needed (up to 10 parts)
- `translate.py` — detects source language via Whisper (tiny model), translates audio using the `vot-cli` Node.js tool, mixes original (quieted) + translated audio with pydub
- `schema.py` — `YouTubeVideoData` Pydantic model for cached video metadata
- Redis caches processed `file_id`s to avoid re-downloading; Redis distributed locks prevent concurrent processing of the same video

### Key Patterns

- **Async throughout**: aiogram + asyncio; all I/O is non-blocking
- **Event signals** (`aiosignal`): `on_link_received`, `on_link_sent`, `on_yt_video_sent`, `on_yt_video_fail` — used for logging and feed sharing without coupling handlers
- **Dump chat pattern**: videos are sent to `DUMP_CHAT_ID` to obtain a stable Telegram `file_id`, then forwarded to the user; cached `file_id`s allow instant resend on repeat requests
- **Config** via `pydantic-settings` in `bot/config.py`; `settings` singleton imported throughout

### System Dependencies (inside Docker)

- Python 3.12, FFmpeg, Node.js, `vot-cli` (global npm package for YouTube audio translation)
- Redis (separate container in `compose.yml`)

## Versioning

`version` in `pyproject.toml` follows semver (`major.minor.patch`), bumped by hand in the same commit/PR as the change it reflects:

- **major** — breaking changes (env var renames, incompatible cache/schema changes, dropped platform support)
- **minor** — new functionality (new link origin, new bot command/feature) that stays backward compatible
- **patch** — bug fixes, dependency bumps, refactors with no behavior change

<!-- gortex:communities:start -->
<!-- gortex:skills:start -->
## Community Skills

| Area | Description | Skill |
|------|-------------|-------|
| 1 Dirs Process Youtube Link Async | 39 symbols | `/gortex-1-dirs-process-youtube-link-async` |
| 5 Dirs | 35 symbols | `/gortex-5-dirs` |
| 3 Dirs | 31 symbols | `/gortex-3-dirs` |
| Bot Util Youtube 1 Dirs Pick Stream | 29 symbols | `/gortex-bot-util-youtube-1-dirs-pick-stream` |
| Bot Events Handlers | 24 symbols | `/gortex-bot-events-handlers` |
| Bot Events Handlers 1 Dirs | 22 symbols | `/gortex-bot-events-handlers-1-dirs` |
| 2 Dirs Build Stats Report | 13 symbols | `/gortex-2-dirs-build-stats-report` |
| 1 Dirs Process Youtube Link | 13 symbols | `/gortex-1-dirs-process-youtube-link` |
| 2 Dirs Download Social Video | 13 symbols | `/gortex-2-dirs-download-social-video` |
| 1 Dirs Aenter | 11 symbols | `/gortex-1-dirs-aenter` |
| Bot Util Redis Redis Client | 8 symbols | `/gortex-bot-util-redis-redis-client` |
| 2 Dirs Period Stats | 8 symbols | `/gortex-2-dirs-period-stats` |
| Bot Worker | 7 symbols | `/gortex-bot-worker` |
| 1 Dirs With Chat Action | 7 symbols | `/gortex-1-dirs-with-chat-action` |
| 2 Dirs Social Cache Key | 6 symbols | `/gortex-2-dirs-social-cache-key` |
| 1 Dirs Mix Audio | 6 symbols | `/gortex-1-dirs-mix-audio` |
| 1 Dirs Now | 6 symbols | `/gortex-1-dirs-now` |
| Setup | 6 symbols | `/gortex-setup` |
| Bot Util Youtube 1 Dirs Detect Source Lang | 5 symbols | `/gortex-bot-util-youtube-1-dirs-detect-source-lang` |
| Bot Config Settings | 4 symbols | `/gortex-bot-config-settings` |
<!-- gortex:skills:end -->

<!-- gortex:communities:end -->
