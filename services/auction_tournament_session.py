"""Persistent mirror for live auction-tournament creation/runtime state.

The database is authoritative. This JSON file is a human-readable active-session
mirror that is refreshed whenever tournament state changes. On process restart the
mirror is rebuilt from PostgreSQL, so a lost in-memory process never destroys an
active tournament creation session.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

SESSION_FILE = Path("runtime") / "auction_tournament_sessions.json"
ACTIVE_STATUSES = {
    "select_mode", "await_prize", "confirm_prize", "overview", "await_pool",
    "confirm_pool", "ask_group", "await_group", "confirm_group", "final_confirm",
    "created",
}


def _safe(value: Any):
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def _write(payload: dict) -> None:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SESSION_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temp.replace(SESSION_FILE)


def has_active_prompt_message(user_id: int, chat_id: int, message_id: int) -> bool:
    """Return True only when a reply points at a known active tournament prompt.

    This prevents the auction subsystem from querying PostgreSQL for every
    ordinary reply in busy groups. The mirror is advisory; the database remains
    authoritative once a prompt is actually matched.
    """
    try:
        if not SESSION_FILE.exists():
            return False
        raw = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return False
        target_user = int(user_id)
        target_chat = int(chat_id)
        target_message = int(message_id)
        for payload in raw.values():
            tournament = payload.get("tournament") if isinstance(payload, dict) else None
            if not isinstance(tournament, dict):
                continue
            if int(tournament.get("creator_id") or 0) != target_user:
                continue
            if int(tournament.get("creation_chat_id") or 0) != target_chat:
                continue
            prompt_id = int(
                tournament.get("prompt_message_id")
                or tournament.get("overview_message_id")
                or 0
            )
            if prompt_id == target_message:
                return True
        return False
    except Exception as exc:
        print(f"[auction-session] prompt lookup failed: {exc!r}")
        return False


async def sync_tournament_session(tournament_id: int) -> None:
    try:
        from database.auction_tournament_repo import get_tournament, fetch_teams, fetch_pool_summary
        tournament = await get_tournament(int(tournament_id))
        current = {}
        if SESSION_FILE.exists():
            try:
                current = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            except Exception:
                current = {}
        sessions = current if isinstance(current, dict) else {}
        key = str(int(tournament_id))
        if not tournament or str(tournament.get("status") or "") not in ACTIVE_STATUSES:
            sessions.pop(key, None)
        else:
            teams = await fetch_teams(int(tournament_id))
            pools = await fetch_pool_summary(int(tournament_id))
            sessions[key] = _safe({
                "tournament": dict(tournament),
                "teams": [dict(x) for x in teams],
                "pools": [dict(x) for x in pools],
            })
        _write(sessions)
    except Exception as exc:
        print(f"[auction-session] sync failed for {tournament_id}: {exc!r}")


async def rebuild_active_sessions() -> None:
    try:
        from database.query import fetch
        rows = await fetch(
            """
            SELECT tournament_id
            FROM auction_tournaments
            WHERE status = ANY($1::text[])
            ORDER BY tournament_id;
            """,
            list(ACTIVE_STATUSES),
        )
        sessions = {}
        for row in rows:
            from database.auction_tournament_repo import get_tournament, fetch_teams, fetch_pool_summary
            tid = int(row["tournament_id"])
            tournament = await get_tournament(tid)
            if not tournament:
                continue
            teams = await fetch_teams(tid)
            pools = await fetch_pool_summary(tid)
            sessions[str(tid)] = _safe({
                "tournament": dict(tournament),
                "teams": [dict(x) for x in teams],
                "pools": [dict(x) for x in pools],
            })
        _write(sessions)
        print(f"[auction-session] rebuilt {len(sessions)} active tournament session(s).")
    except Exception as exc:
        print(f"[auction-session] rebuild failed: {exc!r}")
