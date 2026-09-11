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


async def _status_snapshot() -> tuple[str, int, int, int, float, str]:
    started = time.perf_counter()
    await app.get_me()
    ping_ms = (time.perf_counter() - started) * 1000
    total_users = int(await fetchval(
        "SELECT COUNT(*) FROM (SELECT user_id FROM users UNION SELECT user_id FROM team_squads) AS known_users;"
    ) or 0)
    total_players = int(await fetchval("SELECT COUNT(*) FROM players;") or 0)
    live_matches = int(await fetchval(
        """
        SELECT
            (SELECT COUNT(*) FROM play_matches WHERE status IN ('pending','accepted','pitch_selected','toss_done','lineup','live'))
          + (SELECT COUNT(*) FROM playint_matches WHERE status IN ('pending','accepted','team_selection','pitch_selected','toss_done','lineup','live'))
          + (SELECT COUNT(*) FROM playipl_matches WHERE status IN ('pending','accepted','team_selection','pitch_selected','toss_done','lineup','live'))
          + (SELECT COUNT(*) FROM playso_matches WHERE status IN ('pending','accepted','pitch_selected','toss_done','lineup','live','innings_break'));
        """
    ) or 0)
    health = "🟢 Healthy" if ping_ms < 700 else ("🟡 Degraded" if ping_ms < 1500 else "🔴 Slow")
    return platform.python_version(), total_users, total_players, live_matches, ping_ms, health


@register("btstatus")
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
        python_version, total_users, total_players, live_matches, ping_ms, health = await _status_snapshot()
        uptime = _format_uptime(time.monotonic() - _PROCESS_STARTED_AT)
        final = (
            "<b>╭━━〔 🤖 CRICKIUM BOT STATUS 〕━━╮</b>\n\n"
            f"<b>🐍 Python:</b> {python_version}\n"
            "<b>🗄️ Database:</b> 🟢 Connected\n"
            f"<b>⏱️ Uptime:</b> {uptime}\n"
            f"<b>📡 Bot Ping:</b> {ping_ms:.0f} ms\n"
            f"<b>👥 Total Users:</b> {total_users}\n"
            f"<b>🏏 Live Matches:</b> {live_matches}\n"
            f"<b>🃏 Players in Pool:</b> {total_players}\n"
            f"<b>❤️ Bot Health:</b> {health}\n\n"
            "<b>╰━━━━━━━━━━━━━━━━━━━━━━╯</b>"
        )
    except Exception as exc:
        print(f"[bstatus] status snapshot failed: {exc!r}")
        final = (
            "<b>╭━━〔 🤖 CRICKIUM BOT STATUS 〕━━╮</b>\n\n"
            f"<b>🐍 Python:</b> {platform.python_version()}\n"
            "<b>🗄️ Database:</b> 🟡 Connected / Snapshot Unavailable\n"
            f"<b>⏱️ Uptime:</b> {_format_uptime(time.monotonic() - _PROCESS_STARTED_AT)}\n"
            "<b>📡 Bot Ping:</b> Unavailable\n"
            "<b>👥 Total Users:</b> Unavailable\n"
            "<b>🏏 Live Matches:</b> Unavailable\n"
            "<b>🃏 Players in Pool:</b> Unavailable\n"
            "<b>❤️ Bot Health:</b> 🟡 Degraded\n\n"
            "<b>╰━━━━━━━━━━━━━━━━━━━━━━╯</b>"
        )

    await app.edit_message_text(chat_id, message_id, final, parse_mode="HTML")
