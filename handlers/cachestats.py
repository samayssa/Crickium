from __future__ import annotations

print("cachestats.py loaded")

import time

from app import app
from config import ADMIN_USER_ID
from database import cache, db_stats
from handlers.registry import register


def _is_admin(user_id) -> bool:
    return int(user_id or 0) == int(ADMIN_USER_ID)


def _mb(size) -> str:
    return f"{float(size or 0) / (1024 * 1024):.2f} MB"


def _age(seconds) -> str:
    seconds = int(seconds or 0)
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds / 3600:.1f}h"


def _process_block(title: str, cstats: dict, dstats: dict) -> list[str]:
    counters = cstats.get("counters", {}) or {}
    redis = cstats.get("redis") or {}
    lines = [f"{title}"]
    rate = cstats.get("hit_rate")
    lines.append(
        f"• Redis: {cstats.get('redis_state', '?')}"
        + (f" | cmds {redis.get('commands', 0)} | errors {redis.get('errors', 0)} | avg {redis.get('avg_ms', 0)} ms" if redis else "")
    )
    lines.append(
        f"• Lookups {cstats.get('lookups', 0)}"
        + (f" | hit-rate {rate}%" if rate is not None else "")
        + f" (memory {counters.get('l1_hits', 0)}, redis {counters.get('l2_hits', 0)}, db {counters.get('misses', 0)})"
    )
    lines.append(
        f"• Memory cache: {cstats.get('l1_entries', 0)} entries, {cstats.get('l1_mb', 0)} MB"
        f" | invalidations {counters.get('invalidations', 0)} | refused {counters.get('refused', 0)}"
    )
    blob_hits = counters.get("blob_hits", 0)
    blob_sets = counters.get("blob_sets", 0)
    if blob_hits or blob_sets or counters.get("blob_misses", 0):
        lines.append(
            f"• Live-match snapshots via Redis: sent {blob_sets}, served {blob_hits}, "
            f"missed {counters.get('blob_misses', 0)}, rejected {counters.get('blob_rejected', 0)}"
        )
    fams = cstats.get("families") or {}
    if fams:
        parts = []
        for name, (l1, l2, miss, _x) in sorted(fams.items(), key=lambda kv: -(kv[1][0] + kv[1][1] + kv[1][2]))[:5]:
            total = l1 + l2 + miss
            parts.append(f"{name} {int((l1 + l2) * 100 / total) if total else 0}% of {total}")
        lines.append("• Cached groups: " + ", ".join(parts))
    if dstats:
        lines.append(
            f"• Database ({_age(dstats.get('since_s'))}): {dstats.get('calls', 0)} queries, "
            f"{dstats.get('rows', 0)} rows read, ~{_mb(dstats.get('bytes'))} downloaded, avg {dstats.get('avg_ms', 0)} ms"
        )
        for i, (sig, calls, size, avg_ms) in enumerate((dstats.get("top_bytes") or [])[:4], 1):
            lines.append(f"   {i}. {_mb(size)} · {calls}× · {str(sig)[:70]}")
    return lines


@register("cachestats")
async def cachestats_command(message):
    chat_id = message["chat"]["id"]
    user_id = (message.get("from") or {}).get("id")
    if not _is_admin(user_id):
        await app.send_message(chat_id, "🚫 This command is restricted to the bot admin only.")
        return

    status = cache.get_cache().status()
    lines = [
        "🧠 Cache status",
        f"Enabled: {status.get('enabled')} | Redis target: {status.get('redis') or 'not configured'}",
        f"Namespace: {status.get('namespace')}",
        "",
    ]
    lines += _process_block("BOT process (live)", cache.stats(), db_stats.snapshot(top=4))
    try:
        await cache.get_cache().publish_stats_now()
        web = await cache.get_cache().read_published_stats("web")
    except Exception:
        web = None
    lines.append("")
    if web:
        lines += _process_block(
            f"WEB (Mini App) process — published {_age(time.time() - float(web.get('at', 0)))} ago",
            web.get("cache") or {}, web.get("db") or {},
        )
    else:
        lines.append("WEB (Mini App) process: no stats published yet (appears ~10 minutes after Redis is connected).")
    text = "\n".join(lines)
    await app.send_message(chat_id, text[:3900])


@register("cacheflush")
async def cacheflush_command(message):
    chat_id = message["chat"]["id"]
    user_id = (message.get("from") or {}).get("id")
    if not _is_admin(user_id):
        await app.send_message(chat_id, "🚫 This command is restricted to the bot admin only.")
        return
    ok = await cache.flush_all_wait()
    await app.send_message(
        chat_id,
        "✅ Cache cleared (memory + Redis). Data is reloaded from the database on next use."
        if ok else
        "⚠️ Memory cache cleared. Redis could not confirm right now; cached copies there expire automatically.",
    )
