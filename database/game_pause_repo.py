from __future__ import annotations

from typing import Any

from database.query import execute, fetch, fetchrow

HIDDEN_STATES = ("paused", "queued")


def hidden_clause(engine: str, table: str) -> str:
    return (
        f" AND NOT EXISTS (SELECT 1 FROM game_pauses gp WHERE gp.engine='{engine}' "
        f"AND gp.match_id={table}.match_id AND gp.state IN ('paused','queued'))"
    )


def _row(raw: Any) -> dict | None:
    return dict(raw) if raw else None


async def get(engine: str, match_id: int) -> dict | None:
    return _row(await fetchrow(
        "SELECT * FROM game_pauses WHERE engine=$1 AND match_id=$2;",
        str(engine).upper(), int(match_id),
    ))


async def open_request(engine: str, match_id: int, chat_id: int, challenger_id: int, opponent_id: int, requested_by: int) -> dict | None:
    return _row(await fetchrow(
        """
        INSERT INTO game_pauses(engine, match_id, chat_id, challenger_id, opponent_id, requested_by, state)
        VALUES ($1,$2,$3,$4,$5,$6,'requested')
        ON CONFLICT (engine, match_id) DO UPDATE
            SET requested_by=EXCLUDED.requested_by, created_at=NOW(), request_message_id=NULL
            WHERE game_pauses.state='requested'
        RETURNING *;
        """,
        str(engine).upper(), int(match_id), int(chat_id), int(challenger_id), int(opponent_id), int(requested_by),
    ))


async def set_request_message(engine: str, match_id: int, message_id: int) -> None:
    await execute(
        "UPDATE game_pauses SET request_message_id=$3 WHERE engine=$1 AND match_id=$2 AND state='requested';",
        str(engine).upper(), int(match_id), int(message_id),
    )


async def drop_request(engine: str, match_id: int) -> dict | None:
    return _row(await fetchrow(
        "DELETE FROM game_pauses WHERE engine=$1 AND match_id=$2 AND state='requested' RETURNING *;",
        str(engine).upper(), int(match_id),
    ))


async def confirm(engine: str, match_id: int) -> dict | None:
    return _row(await fetchrow(
        "UPDATE game_pauses SET state='paused', paused_at=NOW() WHERE engine=$1 AND match_id=$2 AND state='requested' RETURNING *;",
        str(engine).upper(), int(match_id),
    ))


async def add_linked(engine: str, match_id: int, chat_id: int, challenger_id: int, opponent_id: int, requested_by: int,
                     parent_engine: str, parent_match_id: int) -> None:
    await execute(
        """
        INSERT INTO game_pauses(engine, match_id, chat_id, challenger_id, opponent_id, requested_by, state,
                                linked_engine, linked_match_id, paused_at)
        VALUES ($1,$2,$3,$4,$5,$6,'paused',$7,$8,NOW())
        ON CONFLICT (engine, match_id) DO UPDATE SET state='paused', linked_engine=$7, linked_match_id=$8, paused_at=NOW();
        """,
        str(engine).upper(), int(match_id), int(chat_id), int(challenger_id), int(opponent_id), int(requested_by),
        str(parent_engine).upper(), int(parent_match_id),
    )


async def primary_for_user(user_id: int) -> dict | None:
    return _row(await fetchrow(
        """
        SELECT * FROM game_pauses
        WHERE (challenger_id=$1 OR opponent_id=$1)
          AND state IN ('paused','queued','resuming')
          AND linked_engine IS NULL
        ORDER BY paused_at DESC NULLS LAST LIMIT 1;
        """,
        int(user_id),
    ))


async def mark_queued(engine: str, match_id: int) -> dict | None:
    return _row(await fetchrow(
        "UPDATE game_pauses SET state='queued', queued_at=COALESCE(queued_at, NOW()) WHERE engine=$1 AND match_id=$2 AND state IN ('paused','queued') RETURNING *;",
        str(engine).upper(), int(match_id),
    ))


async def claim_resume(engine: str, match_id: int) -> dict | None:
    return _row(await fetchrow(
        "UPDATE game_pauses SET state='resuming' WHERE engine=$1 AND match_id=$2 AND state IN ('paused','queued') RETURNING *;",
        str(engine).upper(), int(match_id),
    ))


async def restore_paused(engine: str, match_id: int) -> None:
    await execute(
        "UPDATE game_pauses SET state='paused', queued_at=NULL WHERE engine=$1 AND match_id=$2 AND state='resuming';",
        str(engine).upper(), int(match_id),
    )


async def linked_rows(engine: str, match_id: int) -> list[dict]:
    rows = await fetch(
        "SELECT * FROM game_pauses WHERE linked_engine=$1 AND linked_match_id=$2;",
        str(engine).upper(), int(match_id),
    )
    return [dict(r) for r in rows]


async def delete_with_links(engine: str, match_id: int) -> list[dict]:
    rows = await fetch(
        """
        DELETE FROM game_pauses
        WHERE (engine=$1 AND match_id=$2) OR (linked_engine=$1 AND linked_match_id=$2)
        RETURNING *;
        """,
        str(engine).upper(), int(match_id),
    )
    return [dict(r) for r in rows]


async def queued_in_order() -> list[dict]:
    rows = await fetch(
        "SELECT * FROM game_pauses WHERE state='queued' AND linked_engine IS NULL ORDER BY queued_at ASC;",
    )
    return [dict(r) for r in rows]


async def active_rows() -> list[dict]:
    rows = await fetch("SELECT * FROM game_pauses WHERE state IN ('paused','queued','resuming');")
    return [dict(r) for r in rows]


async def reset_resuming() -> None:
    await execute("UPDATE game_pauses SET state='paused' WHERE state='resuming';")
