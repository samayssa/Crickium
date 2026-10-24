from __future__ import annotations
import time

from database.query import execute, fetch
from utils.bounded_dict import BoundedDict

# chat_id -> (target_type, title, monotonic time of the last successful DB write).
# upsert_chat() used to run one INSERT..ON CONFLICT for every message the bot received.
# An unchanged chat is now written at most once per refresh window per process.
_UPSERT_REFRESH_SECONDS = 6 * 3600
_RECENT_UPSERTS: BoundedDict = BoundedDict(20000)

async def upsert_chat(chat: dict) -> None:
    chat_id = chat.get("id"); chat_type = str(chat.get("type") or "")
    if not chat_id or chat_type not in {"private","group","supergroup","channel"}: return
    if chat_type == "private": kind="user"; title=chat.get("first_name") or chat.get("username")
    elif chat_type == "channel": kind="channel"; title=chat.get("title") or chat.get("username")
    else: kind="group"; title=chat.get("title")
    cached = _RECENT_UPSERTS.get(int(chat_id))
    now = time.monotonic()
    if cached and cached[0] == kind and cached[1] == title and (now - cached[2]) < _UPSERT_REFRESH_SECONDS:
        return
    await execute("""INSERT INTO broadcast_targets(chat_id,target_type,title,updated_at) VALUES($1,$2,$3,NOW()) ON CONFLICT(chat_id) DO UPDATE SET target_type=EXCLUDED.target_type,title=EXCLUDED.title,updated_at=NOW();""", int(chat_id),kind,title)
    _RECENT_UPSERTS[int(chat_id)] = (kind, title, now)

async def get_broadcast_targets() -> list[dict]:
    rows = await fetch("""
        SELECT u.user_id AS chat_id, 'user' AS target_type, COALESCE(u.first_name,u.username) AS title
        FROM users u
        UNION
        SELECT b.chat_id, b.target_type, b.title
        FROM broadcast_targets b
        WHERE b.target_type IN ('group','channel')
        ORDER BY target_type, chat_id;
    """)
    return [dict(r) for r in rows]
