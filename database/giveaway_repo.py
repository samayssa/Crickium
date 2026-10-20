"""Giveaway storage.

Tables
------
giveaways            one row per giveaway (draft -> scheduled/active -> ended/cancelled)
giveaway_entries     one row per participating user
giveaway_deliveries  one row per chat the announcement was sent to (so a
                     restart can resume a broadcast without double-sending)

Every state change is a single conditional UPDATE ... RETURNING, so two
workers / a double tap can never move a giveaway through the same step twice.
"""

from __future__ import annotations

import json
from typing import Any

from database.query import execute, executemany, fetch, fetchrow, fetchval

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS giveaways(
        giveaway_id BIGSERIAL PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'draft',
        created_by BIGINT NOT NULL,
        player JSONB NOT NULL,
        card_key TEXT NOT NULL,
        main_chat_id BIGINT NOT NULL,
        setup_chat_id BIGINT,
        setup_message_id BIGINT,
        setup_photo BOOLEAN NOT NULL DEFAULT TRUE,
        setup_step TEXT NOT NULL DEFAULT 'confirm',
        start_delay_s INTEGER NOT NULL DEFAULT 0,
        start_at TIMESTAMPTZ,
        end_at TIMESTAMPTZ,
        broadcast_state TEXT NOT NULL DEFAULT 'pending',
        broadcast_total INTEGER NOT NULL DEFAULT 0,
        broadcast_sent INTEGER NOT NULL DEFAULT 0,
        broadcast_failed INTEGER NOT NULL DEFAULT 0,
        winner_id BIGINT,
        winner_username TEXT,
        winner_name TEXT,
        winner_score DOUBLE PRECISION,
        entries_final INTEGER,
        result_note TEXT,
        announced BOOLEAN NOT NULL DEFAULT FALSE,
        closed BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        ended_at TIMESTAMPTZ
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_giveaways_status ON giveaways(status, end_at);",
    """
    CREATE TABLE IF NOT EXISTS giveaway_entries(
        giveaway_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        username TEXT,
        first_name TEXT,
        joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (giveaway_id, user_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS giveaway_deliveries(
        giveaway_id BIGINT NOT NULL,
        chat_id BIGINT NOT NULL,
        message_id BIGINT,
        kind TEXT,
        ok BOOLEAN NOT NULL DEFAULT TRUE,
        error TEXT,
        PRIMARY KEY (giveaway_id, chat_id)
    );
    """,
]

_ready = False

# Statuses in which a giveaway is still "live" for admins / the Mini App.
LIVE_STATUSES = ("scheduled", "active")


async def ensure_schema() -> None:
    global _ready
    if _ready:
        return
    for statement in SCHEMA_STATEMENTS:
        await execute(statement)
    _ready = True


def _row(raw: Any) -> dict | None:
    if raw is None:
        return None
    row = dict(raw)
    player = row.get("player")
    if isinstance(player, (str, bytes)):
        try:
            player = json.loads(player)
        except (TypeError, ValueError):
            player = {}
    row["player"] = player if isinstance(player, dict) else {}
    return row


def _rows(raws) -> list[dict]:
    return [r for r in (_row(x) for x in raws) if r is not None]


# ---------------------------------------------------------------------------
# giveaways
# ---------------------------------------------------------------------------
async def create_draft(created_by: int, player: dict, card_key: str, main_chat_id: int,
                       setup_chat_id: int | None) -> dict:
    row = await fetchrow(
        """
        INSERT INTO giveaways(created_by, player, card_key, main_chat_id, setup_chat_id)
        VALUES($1, $2::jsonb, $3, $4, $5)
        RETURNING *;
        """,
        int(created_by), json.dumps(player, default=str), str(card_key), int(main_chat_id), setup_chat_id,
    )
    return _row(row) or {}


async def get(giveaway_id: int) -> dict | None:
    return _row(await fetchrow("SELECT * FROM giveaways WHERE giveaway_id=$1;", int(giveaway_id)))


async def set_setup_message(giveaway_id: int, chat_id: int, message_id: int, is_photo: bool) -> None:
    await execute(
        "UPDATE giveaways SET setup_chat_id=$2, setup_message_id=$3, setup_photo=$4 WHERE giveaway_id=$1;",
        int(giveaway_id), int(chat_id), int(message_id), bool(is_photo),
    )


async def set_step(giveaway_id: int, step: str, start_delay_s: int | None = None) -> dict | None:
    """Move a DRAFT to another setup step. Returns the row, or None if it is no longer a draft."""
    if start_delay_s is None:
        row = await fetchrow(
            "UPDATE giveaways SET setup_step=$2 WHERE giveaway_id=$1 AND status='draft' RETURNING *;",
            int(giveaway_id), step,
        )
    else:
        row = await fetchrow(
            "UPDATE giveaways SET setup_step=$2, start_delay_s=$3 WHERE giveaway_id=$1 AND status='draft' RETURNING *;",
            int(giveaway_id), step, int(start_delay_s),
        )
    return _row(row)


async def find_draft_awaiting_reply(user_id: int, chat_id: int, message_id: int) -> dict | None:
    """The draft whose custom-time prompt is exactly this message (admin typed a reply to it)."""
    return _row(await fetchrow(
        """
        SELECT * FROM giveaways
        WHERE status='draft' AND created_by=$1 AND setup_chat_id=$2 AND setup_message_id=$3
          AND setup_step IN ('custom_start','custom_duration')
        ORDER BY giveaway_id DESC LIMIT 1;
        """,
        int(user_id), int(chat_id), int(message_id),
    ))


async def cancel_draft(giveaway_id: int) -> bool:
    row = await fetchrow(
        "UPDATE giveaways SET status='cancelled', ended_at=NOW(), closed=TRUE, announced=TRUE "
        "WHERE giveaway_id=$1 AND status='draft' RETURNING giveaway_id;",
        int(giveaway_id),
    )
    return row is not None


async def launch(giveaway_id: int, start_delay_s: int, duration_s: int) -> dict | None:
    """draft -> active (starts now) or scheduled (starts later). Exactly-once."""
    delay = max(0, int(start_delay_s))
    row = await fetchrow(
        """
        UPDATE giveaways
        SET status = CASE WHEN $2::int <= 0 THEN 'active' ELSE 'scheduled' END,
            start_at = NOW() + ($2::int * INTERVAL '1 second'),
            end_at   = NOW() + (($2::int + $3::int) * INTERVAL '1 second'),
            setup_step = 'done'
        WHERE giveaway_id=$1 AND status='draft'
        RETURNING *;
        """,
        int(giveaway_id), delay, int(duration_s),
    )
    return _row(row)


async def stale_drafts_cleanup(hours: int = 24) -> int:
    rows = await fetch(
        "UPDATE giveaways SET status='cancelled', ended_at=NOW(), closed=TRUE, announced=TRUE "
        "WHERE status='draft' AND created_at < NOW() - ($1::int * INTERVAL '1 hour') RETURNING giveaway_id;",
        int(hours),
    )
    return len(rows)


async def claim_due_start() -> list[dict]:
    """scheduled -> active for every giveaway whose start time has come."""
    return _rows(await fetch(
        "UPDATE giveaways SET status='active' WHERE status='scheduled' AND start_at <= NOW() RETURNING *;"
    ))


async def claim_pending_broadcasts() -> list[dict]:
    """Flip broadcast_state pending -> running (exactly one worker gets each giveaway)."""
    return _rows(await fetch(
        "UPDATE giveaways SET broadcast_state='running' "
        "WHERE status='active' AND broadcast_state='pending' RETURNING *;"
    ))


async def set_broadcast_total(giveaway_id: int, total: int) -> None:
    await execute("UPDATE giveaways SET broadcast_total=$2 WHERE giveaway_id=$1;", int(giveaway_id), int(total))


async def finish_broadcast(giveaway_id: int, sent: int, failed: int) -> None:
    await execute(
        "UPDATE giveaways SET broadcast_state='done', broadcast_sent=$2, broadcast_failed=$3 WHERE giveaway_id=$1;",
        int(giveaway_id), int(sent), int(failed),
    )


async def claim_due_end() -> list[dict]:
    """active -> ending for every giveaway whose end time has come."""
    return _rows(await fetch(
        "UPDATE giveaways SET status='ending' WHERE status='active' AND end_at <= NOW() RETURNING *;"
    ))


async def reset_after_restart() -> None:
    """A restarted process owns no running tasks: re-open what was in flight."""
    await execute("UPDATE giveaways SET broadcast_state='pending' WHERE broadcast_state='running' AND status IN ('active','scheduled');")
    await execute("UPDATE giveaways SET status='active' WHERE status='ending';")


async def reopen_ending(giveaway_id: int) -> None:
    """A draw that crashed: let the worker try again on its next tick."""
    await execute("UPDATE giveaways SET status='active' WHERE giveaway_id=$1 AND status='ending';", int(giveaway_id))


async def end_now(giveaway_id: int) -> bool:
    row = await fetchrow(
        "UPDATE giveaways SET end_at=NOW() WHERE giveaway_id=$1 AND status='active' RETURNING giveaway_id;",
        int(giveaway_id),
    )
    return row is not None


async def cancel_live(giveaway_id: int) -> dict | None:
    return _row(await fetchrow(
        "UPDATE giveaways SET status='cancelled', ended_at=NOW(), result_note='Cancelled by admin', announced=TRUE "
        "WHERE giveaway_id=$1 AND status IN ('scheduled','active') RETURNING *;",
        int(giveaway_id),
    ))


async def mark_ended_without_winner(giveaway_id: int, note: str, entries: int) -> dict | None:
    return _row(await fetchrow(
        """
        UPDATE giveaways SET status='ended', ended_at=NOW(), result_note=$2, entries_final=$3
        WHERE giveaway_id=$1 AND status='ending' RETURNING *;
        """,
        int(giveaway_id), str(note)[:300], int(entries),
    ))


async def mark_announced(giveaway_id: int) -> None:
    await execute("UPDATE giveaways SET announced=TRUE WHERE giveaway_id=$1;", int(giveaway_id))


async def mark_closed(giveaway_id: int) -> None:
    await execute("UPDATE giveaways SET closed=TRUE WHERE giveaway_id=$1;", int(giveaway_id))


async def unannounced() -> list[dict]:
    return _rows(await fetch(
        "SELECT * FROM giveaways WHERE status IN ('ended','cancelled') AND announced=FALSE ORDER BY giveaway_id;"
    ))


async def unclosed() -> list[dict]:
    """Finished giveaways whose broadcast messages still show a live Participate button."""
    return _rows(await fetch(
        "SELECT * FROM giveaways WHERE status IN ('ended','cancelled') AND closed=FALSE AND announced=TRUE "
        "AND broadcast_state IN ('running','done') ORDER BY giveaway_id;"
    ))


async def list_live() -> list[dict]:
    return _rows(await fetch(
        "SELECT * FROM giveaways WHERE status IN ('scheduled','active') ORDER BY end_at ASC, giveaway_id ASC;"
    ))


async def list_recent_finished(limit: int = 10, days: int = 60) -> list[dict]:
    return _rows(await fetch(
        """
        SELECT * FROM giveaways
        WHERE status='ended' AND ended_at > NOW() - ($2::int * INTERVAL '1 day')
        ORDER BY ended_at DESC, giveaway_id DESC LIMIT $1;
        """,
        int(limit), int(days),
    ))


# ---------------------------------------------------------------------------
# entries
# ---------------------------------------------------------------------------
async def add_entry(giveaway_id: int, user_id: int, username: str | None, first_name: str | None) -> bool:
    """True if the user was newly added, False if they were already in."""
    row = await fetchrow(
        """
        INSERT INTO giveaway_entries(giveaway_id, user_id, username, first_name)
        VALUES($1,$2,$3,$4) ON CONFLICT (giveaway_id, user_id) DO NOTHING RETURNING user_id;
        """,
        int(giveaway_id), int(user_id), username, first_name,
    )
    return row is not None


async def remove_entry(giveaway_id: int, user_id: int) -> None:
    await execute("DELETE FROM giveaway_entries WHERE giveaway_id=$1 AND user_id=$2;", int(giveaway_id), int(user_id))


async def entries(giveaway_id: int) -> list[dict]:
    rows = await fetch(
        "SELECT user_id, username, first_name, joined_at FROM giveaway_entries WHERE giveaway_id=$1 ORDER BY joined_at;",
        int(giveaway_id),
    )
    return [dict(r) for r in rows]


async def entry_count(giveaway_id: int) -> int:
    return int(await fetchval("SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id=$1;", int(giveaway_id)) or 0)


async def entry_counts(giveaway_ids: list[int]) -> dict[int, int]:
    if not giveaway_ids:
        return {}
    rows = await fetch(
        "SELECT giveaway_id, COUNT(*) AS n FROM giveaway_entries WHERE giveaway_id = ANY($1::bigint[]) GROUP BY giveaway_id;",
        [int(i) for i in giveaway_ids],
    )
    return {int(r["giveaway_id"]): int(r["n"]) for r in rows}


async def user_entries(user_id: int, giveaway_ids: list[int]) -> set[int]:
    if not giveaway_ids:
        return set()
    rows = await fetch(
        "SELECT giveaway_id FROM giveaway_entries WHERE user_id=$1 AND giveaway_id = ANY($2::bigint[]);",
        int(user_id), [int(i) for i in giveaway_ids],
    )
    return {int(r["giveaway_id"]) for r in rows}


async def has_entry(giveaway_id: int, user_id: int) -> bool:
    return bool(await fetchval(
        "SELECT 1 FROM giveaway_entries WHERE giveaway_id=$1 AND user_id=$2;", int(giveaway_id), int(user_id)
    ))


# ---------------------------------------------------------------------------
# deliveries
# ---------------------------------------------------------------------------
async def delivered_chat_ids(giveaway_id: int) -> set[int]:
    rows = await fetch("SELECT chat_id FROM giveaway_deliveries WHERE giveaway_id=$1;", int(giveaway_id))
    return {int(r["chat_id"]) for r in rows}


async def save_deliveries(giveaway_id: int, rows: list[tuple[int, int | None, str, bool, str | None]]) -> None:
    """rows: (chat_id, message_id, kind, ok, error)."""
    if not rows:
        return
    await executemany(
        """
        INSERT INTO giveaway_deliveries(giveaway_id, chat_id, message_id, kind, ok, error)
        VALUES($1,$2,$3,$4,$5,$6)
        ON CONFLICT (giveaway_id, chat_id) DO UPDATE SET message_id=EXCLUDED.message_id,
            kind=EXCLUDED.kind, ok=EXCLUDED.ok, error=EXCLUDED.error;
        """,
        [(int(giveaway_id), int(c), (int(m) if m else None), k, bool(ok), (str(e)[:200] if e else None))
         for c, m, k, ok, e in rows],
    )


async def sent_deliveries(giveaway_id: int) -> list[dict]:
    rows = await fetch(
        "SELECT chat_id, message_id, kind FROM giveaway_deliveries WHERE giveaway_id=$1 AND ok=TRUE AND message_id IS NOT NULL;",
        int(giveaway_id),
    )
    return [dict(r) for r in rows]


async def delivery_counts(giveaway_id: int) -> tuple[int, int]:
    rows = await fetch(
        "SELECT ok, COUNT(*) AS n FROM giveaway_deliveries WHERE giveaway_id=$1 GROUP BY ok;", int(giveaway_id)
    )
    sent = failed = 0
    for r in rows:
        if r["ok"]:
            sent = int(r["n"])
        else:
            failed = int(r["n"])
    return sent, failed


# ---------------------------------------------------------------------------
# activity metrics (each source is independent, so one missing table never
# breaks the draw)
# ---------------------------------------------------------------------------
async def _safe_rows(query: str, ids: list[int]) -> list[Any]:
    try:
        return list(await fetch(query, ids))
    except Exception as exc:
        print(f"[giveaway_repo] activity source skipped: {type(exc).__name__}: {exc}")
        return []


async def activity_metrics(user_ids: list[int]) -> dict[int, dict[str, float]]:
    """Raw activity counters for many users with one query per source."""
    ids = sorted({int(i) for i in user_ids})
    out: dict[int, dict[str, float]] = {i: {} for i in ids}
    if not ids:
        return out

    def put(rows, key_map):
        for r in rows:
            uid = int(r["user_id"])
            if uid not in out:
                continue
            for src, dst in key_map.items():
                try:
                    out[uid][dst] = float(r[src] or 0)
                except (KeyError, TypeError, ValueError):
                    pass

    put(await _safe_rows(
        "SELECT user_id, matches, wins FROM player_stats WHERE user_id = ANY($1::bigint[]);", ids),
        {"matches": "matches", "wins": "wins"})
    put(await _safe_rows(
        "SELECT user_id, COUNT(*) AS n FROM player_claims WHERE user_id = ANY($1::bigint[]) GROUP BY user_id;", ids),
        {"n": "claims"})
    put(await _safe_rows(
        "SELECT user_id, total_claimed FROM daily_rewards WHERE user_id = ANY($1::bigint[]);", ids),
        {"total_claimed": "daily"})
    put(await _safe_rows(
        "SELECT user_id, COUNT(*) AS n FROM quest_completions WHERE user_id = ANY($1::bigint[]) GROUP BY user_id;", ids),
        {"n": "quests"})
    put(await _safe_rows(
        "SELECT user_id, COUNT(*) AS n FROM pack_openings WHERE user_id = ANY($1::bigint[]) GROUP BY user_id;", ids),
        {"n": "packs"})
    put(await _safe_rows(
        "SELECT user_id, COUNT(*) AS games, COUNT(*) FILTER (WHERE outcome='win') AS wins "
        "FROM miniapp_game_results WHERE user_id = ANY($1::bigint[]) GROUP BY user_id;", ids),
        {"games": "g_games", "wins": "g_wins"})
    put(await _safe_rows(
        "SELECT user_id, COUNT(*) AS n FROM quest_events "
        "WHERE user_id = ANY($1::bigint[]) AND occurred_at > NOW() - INTERVAL '14 days' GROUP BY user_id;", ids),
        {"n": "recent"})
    return out


# ---------------------------------------------------------------------------
# tiny key/value helper (re-uses the bot_runtime_state table)
# ---------------------------------------------------------------------------
async def kv_get(key: str) -> str | None:
    try:
        return await fetchval("SELECT state_value FROM bot_runtime_state WHERE state_key=$1 LIMIT 1;", key)
    except Exception as exc:
        print(f"[giveaway_repo] kv_get failed: {type(exc).__name__}")
        return None


async def kv_set(key: str, value: str) -> None:
    try:
        await execute(
            """
            INSERT INTO bot_runtime_state(state_key, state_value, updated_at) VALUES($1,$2,NOW())
            ON CONFLICT (state_key) DO UPDATE SET state_value=EXCLUDED.state_value, updated_at=NOW();
            """,
            key, value,
        )
    except Exception as exc:
        print(f"[giveaway_repo] kv_set failed: {type(exc).__name__}")
