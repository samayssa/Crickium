"""Database operations for Showcase player cards and Showcase sets."""
from __future__ import annotations

import re
import hashlib
from typing import Iterable

from database.query import execute, fetch, fetchrow, transaction
from database.players_repo import parse_player_line
from services.card_identity import showcase_squad_player_id

SHOWCASE_HEADER_RE = re.compile(r"^\s*SHOWCASE\s*:\s*(.+?)\s*$", re.IGNORECASE)
SHOWCASE_IMAGE_RE = re.compile(r"^\s*(.+?)\s*\[([^\[\]]+)\]\s*$")


def parse_showcase_header(line: str) -> str | None:
    match = SHOWCASE_HEADER_RE.match(str(line or ""))
    if not match:
        return None
    name = match.group(1).strip()
    return name or None


def parse_showcase_image_target(value: str) -> tuple[str, str] | None:
    match = SHOWCASE_IMAGE_RE.match(str(value or "").strip())
    if not match:
        return None
    player_name = match.group(1).strip()
    showcase_name = match.group(2).strip()
    if not player_name or not showcase_name:
        return None
    return player_name, showcase_name


def as_showcase_player(row) -> dict:
    player = dict(row)
    card_id = int(player.get("showcase_card_id") or 0)
    player["showcase_card_id"] = card_id
    player["showcase_set_id"] = int(player.get("showcase_set_id") or 0)
    player["showcase_name"] = player.get("showcase_name")
    player["set_name"] = player.get("showcase_name")
    player["showcase_set_name"] = player.get("showcase_name")
    player["is_showcase"] = True
    player["player_kind"] = "showcase"
    player["is_special"] = False
    player["edition"] = None
    player["special_edition_id"] = None
    player["player_id"] = showcase_squad_player_id(card_id)
    return player


async def get_showcase_set(showcase_name: str) -> dict | None:
    row = await fetchrow(
        "SELECT * FROM showcase_sets WHERE LOWER(set_name)=LOWER($1) LIMIT 1;",
        str(showcase_name).strip(),
    )
    return dict(row) if row else None


async def get_showcase_set_by_id(showcase_set_id: int) -> dict | None:
    row = await fetchrow("SELECT * FROM showcase_sets WHERE showcase_set_id=$1;", int(showcase_set_id))
    return dict(row) if row else None


async def _get_or_create_set(conn, showcase_name: str, uploaded_by: int) -> dict:
    clean_name = showcase_name.strip()
    slug_base = re.sub(r"[^a-z0-9]+", "-", clean_name.lower()).strip("-")[:72] or "showcase"
    slug_suffix = hashlib.sha1(clean_name.casefold().encode("utf-8")).hexdigest()[:10]
    slug = f"{slug_base}-{slug_suffix}"[:100]
    row = await conn.fetchrow(
        """
        INSERT INTO showcase_sets(set_name, set_slug, uploaded_by)
        VALUES($1, $2, $3)
        ON CONFLICT (LOWER(set_name)) DO UPDATE SET updated_at=NOW()
        RETURNING *;
        """,
        clean_name, slug, int(uploaded_by),
    )
    if row:
        return dict(row)
    row = await conn.fetchrow(
        "SELECT * FROM showcase_sets WHERE LOWER(set_name)=LOWER($1) LIMIT 1;", showcase_name.strip()
    )
    if not row:
        raise RuntimeError(f"Showcase set disappeared during upload: {showcase_name}")
    return dict(row)


async def upload_showcase_sections(sections: dict[str, list[str]], uploaded_by: int) -> dict:
    """Atomically import several named Showcase sections.

    Each section is parsed with the same canonical player-line parser used by
    global and special uploads. Duplicate cards are ignored by the DB identity
    constraint, while malformed rows are reported without poisoning other rows.
    """
    summary = {"sets": [], "total": 0, "uploaded": 0, "already_exists": 0, "failed": 0, "failed_details": []}

    async def _tx(conn):
        for showcase_name, lines in sections.items():
            set_row = await _get_or_create_set(conn, showcase_name, uploaded_by)
            set_id = int(set_row["showcase_set_id"])
            bucket = {"name": showcase_name, "uploaded": 0, "already_exists": 0, "failed": 0, "details": []}

            for line in lines:
                summary["total"] += 1
                player, error = parse_player_line(line)
                if error:
                    bucket["failed"] += 1
                    bucket["details"].append(error)
                    continue

                row = await conn.fetchrow(
                    """
                    INSERT INTO showcase_cards(
                        showcase_set_id, name, country, role, bat_level, bowl_level,
                        batting_hand, bowling_hand, base_player_id, uploaded_by
                    )
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                    ON CONFLICT (showcase_set_id, LOWER(name)) DO NOTHING
                    RETURNING *;
                    """,
                    set_id, player["name"], player["country"], player["role"],
                    player["bat_level"], player["bowl_level"], player["batting_hand"], player["bowling_hand"],
                    None, int(uploaded_by),
                )
                if row:
                    bucket["uploaded"] += 1
                else:
                    bucket["already_exists"] += 1

            summary["uploaded"] += bucket["uploaded"]
            summary["already_exists"] += bucket["already_exists"]
            summary["failed"] += bucket["failed"]
            summary["failed_details"].extend(bucket["details"])
            summary["sets"].append(bucket)

    await transaction(_tx)
    return summary


async def get_showcase_player(showcase_card_id: int) -> dict | None:
    row = await fetchrow(
        """
        SELECT c.*, s.set_name AS showcase_name
        FROM showcase_cards c
        JOIN showcase_sets s ON s.showcase_set_id=c.showcase_set_id
        WHERE c.showcase_card_id=$1
        LIMIT 1;
        """,
        int(showcase_card_id),
    )
    return as_showcase_player(row) if row else None


async def get_showcase_player_by_identity(name: str, showcase_name: str) -> dict | None:
    row = await fetchrow(
        """
        SELECT c.*, s.set_name AS showcase_name
        FROM showcase_cards c
        JOIN showcase_sets s ON s.showcase_set_id=c.showcase_set_id
        WHERE LOWER(c.name)=LOWER($1)
          AND LOWER(s.set_name)=LOWER($2)
        LIMIT 1;
        """,
        str(name).strip(), str(showcase_name).strip(),
    )
    return as_showcase_player(row) if row else None


async def get_showcase_players_by_name(name: str) -> list[dict]:
    rows = await fetch(
        """
        SELECT c.*, s.set_name AS showcase_name
        FROM showcase_cards c
        JOIN showcase_sets s ON s.showcase_set_id=c.showcase_set_id
        WHERE LOWER(c.name)=LOWER($1)
        ORDER BY c.showcase_card_id ASC;
        """,
        str(name).strip(),
    )
    return [as_showcase_player(r) for r in rows]


async def search_showcase_players(query: str, limit: int = 100) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    prefix = f"{q}%"
    rows = await fetch(
        """
        SELECT c.*, s.set_name AS showcase_name
        FROM showcase_cards c
        JOIN showcase_sets s ON s.showcase_set_id=c.showcase_set_id
        WHERE LOWER(c.name) LIKE LOWER($1)
           OR LOWER(s.set_name) LIKE LOWER($1)
        ORDER BY
            CASE WHEN LOWER(c.name)=LOWER($2) THEN 0 ELSE 1 END,
            CASE WHEN LOWER(c.name) LIKE LOWER($3) THEN 0 ELSE 1 END,
            c.showcase_card_id ASC
        LIMIT $4;
        """,
        like, q, prefix, int(limit),
    )
    return [as_showcase_player(r) for r in rows]


async def update_showcase_player(showcase_card_id: int, values: dict) -> dict | None:
    allowed = {"name", "country", "role", "bat_level", "bowl_level", "batting_hand", "bowling_hand"}
    clean = {k: v for k, v in (values or {}).items() if k in allowed}
    if clean:
        columns = []
        args = []
        for key, value in clean.items():
            columns.append(f"{key}=${len(args)+1}")
            args.append(value)
        args.append(int(showcase_card_id))
        row = await fetchrow(
            f"UPDATE showcase_cards SET {', '.join(columns)}, updated_at=NOW() WHERE showcase_card_id=${len(args)} RETURNING *;",
            *args,
        )
        if row:
            return await get_showcase_player(int(row["showcase_card_id"]))
    return await get_showcase_player(int(showcase_card_id))


async def delete_showcase_player(showcase_card_id: int) -> bool:
    card_id = int(showcase_card_id)
    squad_player_id = showcase_squad_player_id(card_id)
    await execute("DELETE FROM player_user_match_stats WHERE player_id=$1;", squad_player_id)
    await execute("DELETE FROM user_player_loadouts WHERE player_id=$1 AND player_kind='showcase';", squad_player_id)
    await execute("DELETE FROM user_player_upgrades WHERE player_id=$1 AND player_kind='showcase';", squad_player_id)
    result = await execute("DELETE FROM showcase_cards WHERE showcase_card_id=$1;", card_id)
    return bool(result) and result.split()[-1] != "0"


async def list_showcase_sets(limit: int = 100) -> list[dict]:
    rows = await fetch(
        "SELECT * FROM showcase_sets ORDER BY showcase_set_id DESC LIMIT $1;", int(limit)
    )
    return [dict(r) for r in rows]
