
"""
Database operations for the 'players' table:
- bulk_upload_players(): used by /upload_pl (admin command)
- get_random_players_by_role(): used by /debut to build an auto Playing 11
"""

from __future__ import annotations

import re

from database.query import execute, fetch, fetchrow, fetchval

VALID_ROLES = {"Batsman", "Bowler", "AllRounder"}

ROLE_ALIASES = {
    "batsman": "Batsman",
    "bat": "Batsman",
    "batter": "Batsman",
    "bowler": "Bowler",
    "bowl": "Bowler",
    "allrounder": "AllRounder",
    "all-rounder": "AllRounder",
    "all rounder": "AllRounder",
    "ar": "AllRounder",
}

BATTING_FIELD_RE = re.compile(r"^(RH|LH)\s*-\s*BAT\s+(\d{1,3})$", re.IGNORECASE)
BOWLING_STYLE_RE = re.compile(r"^(RAF|LAF|RAM|LAM|RAO|LAO|RAL|LAL)\s+(\d{1,3})$", re.IGNORECASE)

BOWLING_STYLE_LABELS = {
    "RAF": "Right Arm Fast",
    "LAF": "Left Arm Fast",
    "RAM": "Right Arm Medium",
    "LAM": "Left Arm Medium",
    "RAO": "Right Arm Off Break",
    "LAO": "Left Arm Off Break",
    "RAL": "Right Arm Leg Spin",
    "LAL": "Left Arm Leg Spin",
}


def normalize_role(raw_role: str) -> str | None:
    key = raw_role.strip().lower()
    return ROLE_ALIASES.get(key)


def _parse_batting_field(field: str, line: str):
    match = BATTING_FIELD_RE.match(field.strip())
    if not match:
        return None, None, (
            f"expected a batting field like 'RH-BAT <LEVEL>' or 'LH-BAT <LEVEL>', got {field!r} in line: {line!r}"
        )

    hand, level_str = match.groups()
    level = int(level_str)
    if not (0 <= level <= 100):
        return None, None, f"bat level must be 0-100: {line!r}"
    return hand.upper(), level, None


def _parse_bowling_field(field: str, line: str):
    match = BOWLING_STYLE_RE.match(field.strip())
    if not match:
        return None, None, (
            "expected a bowling style field like 'RAF <LEVEL>' / 'LAF <LEVEL>' / 'RAM <LEVEL>' / 'LAM <LEVEL>' / "
            "'RAO <LEVEL>' / 'LAO <LEVEL>' / 'RAL <LEVEL>' / 'LAL <LEVEL>', got "
            f"{field!r} in line: {line!r}"
        )

    style, level_str = match.groups()
    style = style.upper()
    level = int(level_str)
    if not (0 <= level <= 100):
        return None, None, f"bowl level must be 0-100: {line!r}"
    return style, level, None


def parse_player_line(line: str):
    """
    Parses one line in the bracketed format:
    '[Player Name][Country][Role][RH/LH-BAT <LEVEL>][RAF/LAF/RAM/LAM/RAO/LAO/RAL/LAL <LEVEL>]'
    Returns (player_dict, error_message). Exactly one will be None.
    """
    line = line.strip()
    if not line:
        return None, "empty line"

    fields = re.findall(r"\[([^\[\]]*)\]", line)
    if len(fields) != 5:
        return None, (
            f"expected 5 bracketed fields '[Name][Country][Role][RH/LH-BAT <LEVEL>][RAF/LAF/RAM/LAM/RAO/LAO/RAL/LAL <LEVEL>]', "
            f"got {len(fields)}: {line!r}"
        )

    name, country, raw_role, raw_bat, raw_bowl = [f.strip() for f in fields]

    if not name:
        return None, f"missing player name: {line!r}"

    role = normalize_role(raw_role)
    if role is None:
        return None, f"unknown role {raw_role!r} in line: {line!r}"

    batting_hand, bat_level, bat_error = _parse_batting_field(raw_bat, line)
    if bat_error:
        return None, bat_error

    bowling_hand, bowl_level, bowl_error = _parse_bowling_field(raw_bowl, line)
    if bowl_error:
        return None, bowl_error

    return {
        "name": name,
        "country": country or None,
        "role": role,
        "bat_level": bat_level,
        "bowl_level": bowl_level,
        "batting_hand": batting_hand,
        "bowling_hand": bowling_hand,
    }, None


async def bulk_upload_players(raw_text: str, uploaded_by: int) -> dict:
    """
    Parses multi-line player text and inserts each into the DB.
    Returns a summary dict: {uploaded, already_exists, failed, failed_details}
    """
    print(f"[players_repo] bulk_upload_players() called by uploaded_by={uploaded_by}")

    lines = [l for l in raw_text.splitlines() if l.strip()]
    print(f"[players_repo] {len(lines)} non-empty line(s) to process.")

    uploaded = 0
    already_exists = 0
    failed = 0
    failed_details = []

    for line in lines:
        player, error = parse_player_line(line)
        if error:
            print(f"[players_repo] PARSE FAILED: {error}")
            failed += 1
            failed_details.append(error)
            continue

        try:
            result = await execute(
                """
                INSERT INTO players (name, country, role, bat_level, bowl_level, batting_hand, bowling_hand, uploaded_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (name) DO NOTHING;
                """,
                player["name"], player["country"], player["role"],
                player["bat_level"], player["bowl_level"],
                player["batting_hand"], player["bowling_hand"], uploaded_by,
            )
            if result.endswith(" 0"):
                print(f"[players_repo] ALREADY EXISTS: {player['name']}")
                already_exists += 1
            else:
                print(f"[players_repo] UPLOADED: {player['name']}")
                uploaded += 1
        except Exception as e:
            print(f"[players_repo] !! DB INSERT FAILED for {player['name']}: {e!r}")
            failed += 1
            failed_details.append(f"{player['name']}: {e}")

    summary = {
        "total_lines": len(lines),
        "uploaded": uploaded,
        "already_exists": already_exists,
        "failed": failed,
        "failed_details": failed_details,
    }
    print(f"[players_repo] bulk_upload_players() summary: {summary}")
    return summary


async def get_player(name: str) -> dict | None:
    row = await fetchrow(
        """
        SELECT * FROM players
        WHERE LOWER(name) = LOWER($1)
        LIMIT 1;
        """,
        name,
    )
    return dict(row) if row else None


async def search_players(query: str, limit: int = 10) -> list[dict]:
    q = (query or "").strip()
    if not q:
        rows = await fetch(
            """
            SELECT * FROM players
            ORDER BY player_id ASC
            LIMIT $1;
            """,
            limit,
        )
        return [dict(r) for r in rows]

    like = f"%{q}%"
    rows = await fetch(
        """
        SELECT *
        FROM players
        WHERE LOWER(name) LIKE LOWER($1)
           OR LOWER(country) LIKE LOWER($1)
           OR LOWER(role) LIKE LOWER($1)
        ORDER BY
            CASE WHEN LOWER(name) = LOWER($2) THEN 0 ELSE 1 END,
            CASE WHEN LOWER(name) LIKE LOWER($3) THEN 0 ELSE 1 END,
            player_id ASC
        LIMIT $4;
        """,
        like,
        q,
        f"{q}%",
        limit,
    )
    return [dict(r) for r in rows]


async def get_players_by_role(role: str) -> list[dict]:
    role_name = normalize_role(role) or role
    rows = await fetch(
        """
        SELECT * FROM players
        WHERE role = $1
        ORDER BY player_id ASC;
        """,
        role_name,
    )
    return [dict(r) for r in rows]


async def count_players_by_role(role: str) -> int:
    return await fetchval("SELECT COUNT(*) FROM players WHERE role = $1;", role)


async def get_random_players_by_role(role: str, count: int, min_level: int = 0):
    """
    Fetch up to `count` random players of a given role. Prefers players with
    bat_level/bowl_level (whichever is relevant) above min_level, but falls
    back to any player of that role if not enough meet the threshold.
    """
    level_column = "bat_level" if role == "Batsman" else "bowl_level"
    if role == "AllRounder":
        rows = await fetch(
            f"""
            SELECT * FROM players
            WHERE role = $1 AND (bat_level >= $2 OR bowl_level >= $2)
            ORDER BY random()
            LIMIT $3;
            """,
            role, min_level, count,
        )
    else:
        rows = await fetch(
            f"""
            SELECT * FROM players
            WHERE role = $1 AND {level_column} >= $2
            ORDER BY random()
            LIMIT $3;
            """,
            role, min_level, count,
        )

    if len(rows) < count:
        print(f"[players_repo] Only {len(rows)}/{count} '{role}' players met min_level={min_level}, falling back to any level.")
        rows = await fetch(
            "SELECT * FROM players WHERE role = $1 ORDER BY random() LIMIT $2;",
            role, count,
        )

    return [dict(r) for r in rows]
