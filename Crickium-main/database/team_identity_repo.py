"""Persistence for user franchise names and optional team logos."""
from __future__ import annotations

import uuid

from database.query import execute, fetchrow


async def set_team_name(user_id: int, team_name: str, first_name: str | None = None) -> str:
    await execute(
        """
        INSERT INTO users (user_id, first_name, franchise_name, last_seen_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (user_id) DO UPDATE SET franchise_name = EXCLUDED.franchise_name, last_seen_at = NOW();
        """,
        int(user_id), first_name or team_name, team_name,
    )
    return team_name


async def create_logo_request(user_id: int, logo_type: str, logo_id: str, fallback: str) -> str:
    token = uuid.uuid4().hex
    await execute(
        """
        INSERT INTO team_logo_requests(token, user_id, logo_type, logo_id, logo_fallback, created_at)
        VALUES ($1, $2, $3, $4, $5, NOW());
        """,
        token, int(user_id), str(logo_type), str(logo_id), str(fallback or "🏷️"),
    )
    return token


async def get_logo_request(token: str):
    return await fetchrow(
        """
        SELECT token, user_id, logo_type, logo_id, logo_fallback
        FROM team_logo_requests WHERE token = $1;
        """,
        token,
    )


async def set_logo_from_request(token: str, user_id: int) -> bool:
    row = await get_logo_request(token)
    if not row or int(row["user_id"]) != int(user_id):
        return False

    await execute(
        """
        INSERT INTO users (user_id, first_name, team_logo_type, team_logo_id, team_logo_fallback, last_seen_at)
        VALUES ($1, $2, $3, $4, $5, NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            team_logo_type = EXCLUDED.team_logo_type,
            team_logo_id = EXCLUDED.team_logo_id,
            team_logo_fallback = EXCLUDED.team_logo_fallback,
            last_seen_at = NOW();
        """,
        int(user_id), "Player", str(row["logo_type"]), str(row["logo_id"]), str(row["logo_fallback"] or "🏷️"),
    )
    await execute("DELETE FROM team_logo_requests WHERE token = $1;", token)
    return True


async def cancel_logo_request(token: str, user_id: int) -> bool:
    row = await get_logo_request(token)
    if not row or int(row["user_id"]) != int(user_id):
        return False
    await execute("DELETE FROM team_logo_requests WHERE token = $1;", token)
    return True
