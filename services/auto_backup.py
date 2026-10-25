"""Automatic full backup every 30 minutes, posted to the player-card channel.

The countdown survives restarts: the due time (epoch seconds) is stored in
bot_runtime_state. After a restart only the *remaining* time is waited (or the
backup is sent at once if it became due while the bot was off).

Compute: the loop touches the database only twice per cycle (one write of the
next due time, then the backup read itself). Waiting is a single asyncio.sleep,
so the Neon database can sleep between backups.
"""
from __future__ import annotations

import asyncio
import time
import traceback
from datetime import datetime, timezone

from app import app
from config import AUTO_BACKUP_CHANNEL_ID, AUTO_BACKUP_ENABLED, AUTO_BACKUP_INTERVAL_SECONDS
from database.query import execute, fetchval
from database.backup_repo import export_tables, BACKUP_FORMAT_VERSION

STATE_KEY = "auto_backup_next_at"
_task: asyncio.Task | None = None


async def _load_next() -> float | None:
    try:
        v = await fetchval("SELECT state_value FROM bot_runtime_state WHERE state_key=$1 LIMIT 1;", STATE_KEY)
        return float(v) if v else None
    except Exception:
        return None


async def _save_next(ts: float) -> None:
    await execute(
        "INSERT INTO bot_runtime_state(state_key,state_value,updated_at) VALUES($1,$2,NOW()) "
        "ON CONFLICT (state_key) DO UPDATE SET state_value=EXCLUDED.state_value, updated_at=NOW();",
        STATE_KEY, str(ts),
    )


async def remaining_seconds() -> int | None:
    nxt = await _load_next()
    return None if nxt is None else max(0, int(nxt - time.time()))


async def send_backup(chat_id: int | None = None, reason: str = "auto") -> int:
    """Build the full backup (same as /sync) and post it. Returns byte size."""
    data = await export_tables(None, backup_type="sync")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    await app.send_document(
        chat_id or AUTO_BACKUP_CHANNEL_ID,
        data,
        filename=f"cricklum_full_backup_v{BACKUP_FORMAT_VERSION}_{stamp}.json.gz",
        caption=(
            f"🗂 Complete database backup ({reason}) · {stamp} UTC\n"
            "Includes all tables: users, cards, matches, tournaments, auctions, giveaways.\n"
            "Reply to this file with /recover to restore."
        ),
    )
    return len(data)


async def _loop() -> None:
    interval = max(60, int(AUTO_BACKUP_INTERVAL_SECONDS))
    nxt = await _load_next()
    if nxt is None:
        nxt = time.time() + interval
        try:
            await _save_next(nxt)
        except Exception:
            traceback.print_exc()
    while True:
        wait = nxt - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        # Persist the next due time BEFORE the (slow) export so a crash loop can never spam the channel.
        nxt = time.time() + interval
        try:
            await _save_next(nxt)
        except Exception:
            traceback.print_exc()
        try:
            size = await send_backup()
            print(f"[auto_backup] sent {size} bytes")
        except Exception as exc:
            print(f"[auto_backup] failed: {exc!r}")
            traceback.print_exc()


def start_worker() -> None:
    global _task
    if not AUTO_BACKUP_ENABLED or (_task and not _task.done()):
        return
    _task = asyncio.create_task(_loop())
