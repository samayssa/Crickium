"""Per-user serialization for economy / squad read-modify-write flows.

Telegram buttons can be pressed twice, and several handlers read a user's squad
(a single JSONB blob) or balance, modify it in Python, then write it back. Two
overlapping executions for the same user could double-spend or lose an update.

``user_lock(user_id)`` serializes those flows inside this process (the bot runs as
one process per deployment). The database remains the source of truth: critical
debits additionally use conditional atomic SQL so correctness never depends on
this lock alone. Lock objects are reference-counted and dropped when idle, so the
table cannot grow without bound.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

_LOCKS: dict[int, list] = {}  # user_id -> [asyncio.Lock, active_refs]


@asynccontextmanager
async def user_lock(*user_ids: int):
    """Acquire locks for one or more users (always in sorted order: deadlock-free)."""
    ids = sorted({int(u) for u in user_ids if u is not None})
    entries = []
    for uid in ids:
        entry = _LOCKS.get(uid)
        if entry is None:
            entry = _LOCKS[uid] = [asyncio.Lock(), 0]
        entry[1] += 1
        entries.append((uid, entry))
    acquired = []
    try:
        for _uid, entry in entries:
            await entry[0].acquire()
            acquired.append(entry)
        yield
    finally:
        for entry in reversed(acquired):
            entry[0].release()
        for uid, entry in entries:
            entry[1] -= 1
            if entry[1] <= 0 and _LOCKS.get(uid) is entry:
                del _LOCKS[uid]
