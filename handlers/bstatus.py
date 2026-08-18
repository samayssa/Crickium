from __future__ import annotations

print("bstatus.py loaded")

import asyncio
import platform
import time
from datetime import timedelta

from handlers.registry import register
from app import app
from database.query import fetchval

_PROCESS_STARTED_AT = time.monotonic()


def _format_uptime(seconds: float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _loading(text: str) -> str:
    return f"<b>{text}</b>"


async def _status_snapshot() -> tuple[str, int, int, float]:
    started = time.perf_counter()
    await app.get_me()
    ping_ms = (time.perf_counter() - started) * 1000
    total_users = int(await fetchval("SELECT COUNT(*) FROM users;") or 0)
    total_players = int(await fetchval("SELECT COUNT(*) FROM players;") or 0)
    return platform.python_version(), total_users, total_players, ping_ms


@register("bstatus")
async def bstatus_command(message):
    chat_id = message["chat"]["id"]

    status = await app.send_message(chat_id, _loading("Getting bot status."), parse_mode="HTML")
    message_id = status.get("message_id")

    if not message_id:
        return

    for dots in (2, 3):
        await asyncio.sleep(0.8)
        await app.edit_message_text(
            chat_id,
            message_id,
            _loading(f"Getting bot status{'.' * dots}"),
            parse_mode="HTML",
        )

    await asyncio.sleep(0.8)
    try:
        python_version, total_users, total_players, ping_ms = await _status_snapshot()
        uptime = _format_uptime(time.monotonic() - _PROCESS_STARTED_AT)
        final = (
            "<b>Crickium Bot Status</b>\n"
            f"Python: {python_version}\n"
            "Database: Connected\n"
            f"Uptime: {uptime}\n"
            f"Bot Ping: {ping_ms:.0f} ms\n"
            f"Total Users: {total_users}\n"
            f"Players in Pool: {total_players}"
        )
    except Exception as exc:
        print(f"[bstatus] status snapshot failed: {exc!r}")
        final = (
            "<b>Crickium Bot Status</b>\n"
            f"Python: {platform.python_version()}\n"
            "Database: Connected\n"
            f"Uptime: {_format_uptime(time.monotonic() - _PROCESS_STARTED_AT)}\n"
            "Bot Ping: Unavailable\n"
            "Total Users: Unavailable\n"
            "Players in Pool: Unavailable"
        )

    await app.edit_message_text(chat_id, message_id, final, parse_mode="HTML")
