from datetime import datetime, timezone

from bot.events.signals import (
    on_link_received,
    on_yt_video_sent,
    on_social_video_sent,
    on_yt_video_fail,
    on_social_video_fail,
    signal_handler,
)
from bot.util.redis import redis_client

_TTL = 90 * 24 * 3600  # 90 days


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _incr(key: str) -> None:
    await redis_client.incr(key)
    await redis_client.expire(key, _TTL)


async def _sadd(key: str, value: str) -> None:
    await redis_client.sadd(key, value)
    await redis_client.expire(key, _TTL)


@signal_handler(on_link_received)
async def stats_link_received(message, origin):
    if not message.from_user:
        return
    d = _today()
    await _incr(f"stats:{d}:requests")
    await _sadd(f"stats:{d}:users", str(message.from_user.id))
    await _incr(f"stats:{d}:chat:{message.chat.type}")


@signal_handler(on_yt_video_sent)
async def stats_yt_sent(link, message, video, fresh):
    await _incr(f"stats:{_today()}:success:youtube")


@signal_handler(on_social_video_sent)
async def stats_social_sent(link, message, video, fresh):
    platform = (video.origin or "social").lower()
    await _incr(f"stats:{_today()}:success:{platform}")


@signal_handler(on_yt_video_fail)
async def stats_yt_fail(link, message):
    await _incr(f"stats:{_today()}:fail:youtube")


@signal_handler(on_social_video_fail)
async def stats_social_fail(link, message):
    await _incr(f"stats:{_today()}:fail:social")
