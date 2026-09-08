from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from database.query import execute, fetch, fetchrow, transaction

_SCHEMA_READY = False
ACTIVE = ("pending", "accepted", "pitch_selected", "toss_done", "lineup", "live", "innings_break")


def _coerce_state(value: Any) -> dict[str, Any]:
    """Return PLAYSO state as a mutable dict regardless of JSONB codec behavior."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


async def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    await execute("""
    CREATE TABLE IF NOT EXISTS playso_matches(
        match_id SERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        message_id BIGINT,
        challenger_id BIGINT NOT NULL,
        challenger_username TEXT,
        challenger_name TEXT,
        opponent_id BIGINT NOT NULL,
        opponent_username TEXT,
        opponent_name TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        pitch TEXT,
        toss_winner_id BIGINT,
        toss_call TEXT,
        toss_result TEXT,
        decision TEXT,
        stadium TEXT,
        weather TEXT,
        innings_no INTEGER NOT NULL DEFAULT 1,
        state JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMP
    );
    """)
    await execute("CREATE INDEX IF NOT EXISTS idx_playso_chat_status ON playso_matches(chat_id,status);")
    await execute("CREATE INDEX IF NOT EXISTS idx_playso_user_status ON playso_matches(challenger_id,opponent_id,status);")
    _SCHEMA_READY = True


async def create_match(chat_id: int, challenger: dict, opponent: dict):
    await ensure_schema()
    return await fetchrow(
        """INSERT INTO playso_matches
        (chat_id,challenger_id,challenger_username,challenger_name,opponent_id,opponent_username,opponent_name,status,expires_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,'pending',NOW()+INTERVAL '60 seconds') RETURNING *;""",
        chat_id, challenger["id"], challenger.get("username"), challenger.get("first_name"),
        opponent["id"], opponent.get("username"), opponent.get("first_name"),
    )


async def _normalized_row(row):
    if row is None:
        return None
    data = dict(row)
    data["state"] = _coerce_state(data.get("state"))
    return data


async def get_match(match_id: int):
    await ensure_schema()
    row = await fetchrow("SELECT * FROM playso_matches WHERE match_id=$1;", match_id)
    return await _normalized_row(row)


async def get_active_match_in_chat(chat_id: int):
    await ensure_schema()
    row = await fetchrow("SELECT * FROM playso_matches WHERE chat_id=$1 AND status = ANY($2::text[]) ORDER BY match_id DESC LIMIT 1;", chat_id, list(ACTIVE))
    return await _normalized_row(row)


async def get_active_match_for_user(user_id: int):
    await ensure_schema()
    row = await fetchrow("SELECT * FROM playso_matches WHERE (challenger_id=$1 OR opponent_id=$1) AND status = ANY($2::text[]) ORDER BY match_id DESC LIMIT 1;", user_id, list(ACTIVE))
    return await _normalized_row(row)


async def set_message_id(match_id: int, message_id: int):
    await ensure_schema()
    await execute("UPDATE playso_matches SET message_id=$1 WHERE match_id=$2;", message_id, match_id)


async def set_basic(match_id: int, **fields):
    await ensure_schema()
    if not fields:
        return
    parts, args = [], []
    for k, v in fields.items():
        args.append(v)
        parts.append(f"{k}=${len(args)}")
    args.append(match_id)
    await execute(f"UPDATE playso_matches SET {', '.join(parts)} WHERE match_id=${len(args)};", *args)


async def set_state(match_id: int, state: dict[str, Any], status: str | None = None):
    await ensure_schema()
    if status is None:
        await execute("UPDATE playso_matches SET state=$1::jsonb WHERE match_id=$2;", json.dumps(state), match_id)
    else:
        await execute("UPDATE playso_matches SET state=$1::jsonb,status=$2 WHERE match_id=$3;", json.dumps(state), status, match_id)


async def mutate_locked(match_id: int, actor_id: int, expected_statuses: set[str], mutator: Callable[[dict, dict], Any]):
    await ensure_schema()
    async def _tx(conn):
        row = await conn.fetchrow("SELECT * FROM playso_matches WHERE match_id=$1 FOR UPDATE;", match_id)
        if not row:
            return None, "missing"
        data = dict(row)
        if data["status"] not in expected_statuses:
            return None, "stale"
        state = _coerce_state(data.get("state"))
        out = await mutator(data, state) if hasattr(mutator, "__await__") else mutator(data, state)
        if isinstance(out, tuple):
            value, status = out
        else:
            value, status = out, data["status"]
        await conn.execute("UPDATE playso_matches SET state=$1::jsonb,status=$2 WHERE match_id=$3;", json.dumps(state), status, match_id)
        fresh = await conn.fetchrow("SELECT * FROM playso_matches WHERE match_id=$1;", match_id)
        return (dict(fresh) if fresh else None), value
    return await transaction(_tx)


async def update_locked(match_id: int, expected_statuses: set[str], updater: Callable[[dict, dict], tuple[Any, str]]):
    await ensure_schema()
    async def _tx(conn):
        row = await conn.fetchrow("SELECT * FROM playso_matches WHERE match_id=$1 FOR UPDATE;", match_id)
        if not row:
            return None, "missing"
        data = dict(row); state = _coerce_state(data.get("state"))
        if data["status"] not in expected_statuses:
            return None, "stale"
        result, status = updater(data, state)
        await conn.execute("UPDATE playso_matches SET state=$1::jsonb,status=$2,innings_no=$3 WHERE match_id=$4;", json.dumps(state), status, int(data.get("innings_no") or 1), match_id)
        fresh = await conn.fetchrow("SELECT * FROM playso_matches WHERE match_id=$1;", match_id)
        return (dict(fresh) if fresh else None), result
    return await transaction(_tx)


async def expire_all_active() -> int:
    await ensure_schema()
    rows = await fetch("SELECT match_id FROM playso_matches WHERE status = ANY($1::text[]);", list(ACTIVE))
    count = len(rows)
    await execute("UPDATE playso_matches SET status='expired', expires_at=NOW() WHERE status = ANY($1::text[]);", list(ACTIVE))
    return count
