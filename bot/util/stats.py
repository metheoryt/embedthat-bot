import asyncio
import os
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo

from bot.util.redis import redis_client

_tz = ZoneInfo(os.environ.get("TZ", "UTC"))


async def _period_stats(dates: list[str]) -> dict:
    all_success_keys: list[str] = []
    for d in dates:
        keys = await redis_client.keys(f"stats:{d}:success:*")
        all_success_keys.extend(keys)
    platforms = sorted({k.split(":")[-1] for k in all_success_keys})

    counter_keys: list[str] = []
    for d in dates:
        counter_keys += [
            f"stats:{d}:requests",
            f"stats:{d}:fail:youtube",
            f"stats:{d}:fail:social",
            f"stats:{d}:chat:private",
            f"stats:{d}:chat:group",
            f"stats:{d}:chat:supergroup",
            f"stats:{d}:chat:channel",
        ]
        for platform in platforms:
            counter_keys.append(f"stats:{d}:success:{platform}")

    counter_map: dict[str, int] = {}
    if counter_keys:
        values = await redis_client.mget(counter_keys)
        counter_map = {k: int(v or 0) for k, v in zip(counter_keys, values)}

    user_ids: set[str] = set()
    for d in dates:
        members = await redis_client.smembers(f"stats:{d}:users")
        user_ids |= members

    def s(key_suffix: str) -> int:
        return sum(counter_map.get(f"stats:{d}:{key_suffix}", 0) for d in dates)

    platform_counts = {p: s(f"success:{p}") for p in platforms}

    return {
        "requests": s("requests"),
        "success": sum(platform_counts.values()),
        "fail": s("fail:youtube") + s("fail:social"),
        "unique_users": len(user_ids),
        "private": s("chat:private"),
        "groups": s("chat:group") + s("chat:supergroup") + s("chat:channel"),
        "platforms": platform_counts,
    }


def _date_range(start: date, end: date) -> list[str]:
    result, d = [], start
    while d <= end:
        result.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return result


def _fmt_section(title: str, stats: dict) -> str:
    lines = [
        title,
        f"  Requests:     {stats['requests']}  (✓ {stats['success']}  ✗ {stats['fail']})",
        f"  Unique users: {stats['unique_users']}",
        f"  Private: {stats['private']} | Groups: {stats['groups']}",
    ]
    if stats["platforms"]:
        lines.append("  " + " | ".join(
            f"{p.capitalize()}: {c}"
            for p, c in sorted(stats["platforms"].items(), key=lambda x: -x[1])
        ))
    return "\n".join(lines)


async def build_stats_report() -> str:
    today = datetime.now(_tz).date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    today_stats, week_stats, month_stats = await asyncio.gather(
        _period_stats([today.strftime("%Y-%m-%d")]),
        _period_stats(_date_range(week_start, today)),
        _period_stats(_date_range(month_start, today)),
    )

    today_label = f"Today ({today.strftime('%b %-d')})"
    week_label = f"This week ({week_start.strftime('%b %-d')}–{today.strftime('%b %-d')})"
    month_label = f"This month ({today.strftime('%B')})"

    return "\n".join([
        "📊 Stats",
        "",
        _fmt_section(today_label, today_stats),
        "",
        _fmt_section(week_label, week_stats),
        "",
        _fmt_section(month_label, month_stats),
    ])
