"""Database helpers for special-edition player records."""
from __future__ import annotations

from database.query import execute, fetch, fetchrow, fetchval
from database.players_repo import normalize_role, _parse_batting_field, _parse_bowling_field
import re

EDITION_RE = re.compile(r"^(.+?)\s*\(([^()]+)\)\s*$")


def split_player_edition(raw_name: str) -> tuple[str, str | None]:
    value = str(raw_name or "").strip()
    match = EDITION_RE.match(value)
    if not match:
        return value, None
    name, edition = match.groups()
    name = name.strip()
    edition = edition.strip()
    return name, edition or None


def display_edition(edition: str | None) -> str | None:
    if not edition:
        return None
    text = str(edition).strip()
    if text.lower().endswith("edition"):
        return text
    return f"{text} Edition"


def parse_special_player_line(line: str):
    """Parse the normal 5-field uploader line, but require an edition in the name field."""
    from database.players_repo import parse_player_line

    player, error = parse_player_line(line)
    if error:
        return None, error
    name, edition = split_player_edition(player["name"])
    if not edition:
        return None, f"special edition player must include an edition in the name field: {line!r}"
    player["name"] = name
    player["edition"] = edition
    return player, None


def as_special_player(row) -> dict:
    player = dict(row)
    player["is_special"] = True
    player["is_showcase"] = False
    player["player_kind"] = "special"
    player["edition"] = player.get("edition")
    special_id = int(player.get("special_player_id") or 0)
    player["special_edition_id"] = special_id
    # Special IDs use the negative side of the existing squad player_id namespace,
    # so a global player_id and a special-edition id can never collide in a squad.
    player["player_id"] = -special_id
    return player


async def insert_special_player(player: dict, uploaded_by: int) -> tuple[bool, dict | None]:
    row = await fetchrow(
        """
        INSERT INTO special_edition_players
            (name, edition, country, role, bat_level, bowl_level, batting_hand, bowling_hand, uploaded_by)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        ON CONFLICT (LOWER(name), LOWER(edition)) DO NOTHING
        RETURNING *;
        """,
        player["name"], player["edition"], player["country"], player["role"],
        player["bat_level"], player["bowl_level"], player["batting_hand"], player["bowling_hand"], uploaded_by,
    )
    if row:
        return True, as_special_player(row)
    existing = await fetchrow(
        """
        SELECT * FROM special_edition_players
        WHERE LOWER(name)=LOWER($1) AND LOWER(edition)=LOWER($2)
        LIMIT 1;
        """,
        player["name"], player["edition"],
    )
    return False, as_special_player(existing) if existing else None


async def get_special_player(name: str, edition: str) -> dict | None:
    row = await fetchrow(
        """
        SELECT * FROM special_edition_players
        WHERE LOWER(name)=LOWER($1) AND LOWER(edition)=LOWER($2)
        LIMIT 1;
        """,
        name, edition,
    )
    return as_special_player(row) if row else None


async def get_special_player_by_id(special_player_id: int) -> dict | None:
    row = await fetchrow("SELECT * FROM special_edition_players WHERE special_player_id=$1;", int(special_player_id))
    return as_special_player(row) if row else None


async def _variant_rows(query: str, *, exact: bool, limit: int = 100) -> list[dict]:
    q=(query or "").strip()
    if not q:
        return []
    if exact:
        where_global = "LOWER(p.name)=LOWER($1)"
        where_special = "LOWER(sp.name)=LOWER($1)"
        where_showcase = "LOWER(sc.name)=LOWER($1)"
        order_sql = "CASE player_kind WHEN 'global' THEN 0 WHEN 'special' THEN 1 ELSE 2 END, sort_id ASC"
        args=(q,)
        limit_sql=''
    else:
        like=f"%{q}%"
        prefix=f"{q}%"
        where_global = "LOWER(p.name) LIKE LOWER($1)"
        where_special = "LOWER(sp.name) LIKE LOWER($1)"
        where_showcase = "LOWER(sc.name) LIKE LOWER($1) OR LOWER(ss.set_name) LIKE LOWER($1)"
        order_sql = "CASE WHEN LOWER(name)=LOWER($2) THEN 0 ELSE 1 END, CASE WHEN LOWER(name) LIKE LOWER($3) THEN 0 ELSE 1 END, CASE player_kind WHEN 'global' THEN 0 WHEN 'special' THEN 1 ELSE 2 END, sort_id ASC"
        args=(like,q,prefix)
        limit_sql=' LIMIT $4'
    sql=f"""
        SELECT source_id, name, country, role, bat_level, bowl_level, batting_hand, bowling_hand,
               uploaded_by, created_at, sort_id, player_kind, is_special, edition,
               special_edition_id, showcase_card_id, showcase_set_id, showcase_name
        FROM (
            SELECT p.player_id::BIGINT AS source_id, p.name, p.country, p.role, p.bat_level, p.bowl_level,
                   p.batting_hand, p.bowling_hand, p.uploaded_by, p.created_at, p.player_id::BIGINT AS sort_id,
                   'global'::TEXT AS player_kind, FALSE AS is_special, NULL::TEXT AS edition,
                   NULL::BIGINT AS special_edition_id, NULL::BIGINT AS showcase_card_id,
                   NULL::BIGINT AS showcase_set_id, NULL::TEXT AS showcase_name
            FROM players p WHERE {where_global}
            UNION ALL
            SELECT sp.special_player_id::BIGINT AS source_id, sp.name, sp.country, sp.role, sp.bat_level, sp.bowl_level,
                   sp.batting_hand, sp.bowling_hand, sp.uploaded_by, sp.created_at, sp.special_player_id::BIGINT AS sort_id,
                   'special'::TEXT AS player_kind, TRUE AS is_special, sp.edition,
                   sp.special_player_id::BIGINT AS special_edition_id, NULL::BIGINT AS showcase_card_id,
                   NULL::BIGINT AS showcase_set_id, NULL::TEXT AS showcase_name
            FROM special_edition_players sp WHERE {where_special}
            UNION ALL
            SELECT sc.showcase_card_id::BIGINT AS source_id, sc.name, sc.country, sc.role, sc.bat_level, sc.bowl_level,
                   sc.batting_hand, sc.bowling_hand, sc.uploaded_by, sc.created_at, sc.showcase_card_id::BIGINT AS sort_id,
                   'showcase'::TEXT AS player_kind, FALSE AS is_special, NULL::TEXT AS edition,
                   NULL::BIGINT AS special_edition_id, sc.showcase_card_id::BIGINT AS showcase_card_id,
                   sc.showcase_set_id::BIGINT AS showcase_set_id, ss.set_name AS showcase_name
            FROM showcase_cards sc JOIN showcase_sets ss ON ss.showcase_set_id=sc.showcase_set_id
            WHERE {where_showcase}
        ) all_players
        ORDER BY {order_sql}{limit_sql};
    """
    return [dict(r) for r in await fetch(sql, *args, *(() if exact else (int(limit),)))]


async def search_player_variants(query: str, limit: int = 100) -> list[dict]:
    rows = await _variant_rows(query, exact=False, limit=limit)
    result=[]
    for row in rows:
        kind=row['player_kind']
        if kind=='special':
            row['special_player_id']=int(row['special_edition_id'])
            result.append(as_special_player(row))
        elif kind=='showcase':
            from database.showcase_players_repo import as_showcase_player
            result.append(as_showcase_player(row))
        else:
            row['player_id']=int(row['source_id']); row['is_special']=False; row['is_showcase']=False; row['edition']=None; row['special_edition_id']=None; row['player_kind']='global'
            result.append(row)
    return result[:int(limit)]


async def get_player_variants(name: str) -> list[dict]:
    rows = await _variant_rows(name, exact=True)
    result=[]
    for row in rows:
        kind=row['player_kind']
        if kind=='special':
            row['special_player_id']=int(row['special_edition_id'])
            result.append(as_special_player(row))
        elif kind=='showcase':
            from database.showcase_players_repo import as_showcase_player
            result.append(as_showcase_player(row))
        else:
            row['player_id']=int(row['source_id']); row['is_special']=False; row['is_showcase']=False; row['edition']=None; row['special_edition_id']=None; row['player_kind']='global'
            result.append(row)
    return result


async def search_special_players(query: str, limit: int = 10) -> list[dict]:
    q = (query or "").strip()
    if not q:
        rows = await fetch("SELECT * FROM special_edition_players ORDER BY special_player_id ASC LIMIT $1;", limit)
    else:
        like = f"%{q}%"
        rows = await fetch(
            """
            SELECT * FROM special_edition_players
            WHERE LOWER(name) LIKE LOWER($1)
               OR LOWER(edition) LIKE LOWER($1)
               OR LOWER(country) LIKE LOWER($1)
               OR LOWER(role) LIKE LOWER($1)
            ORDER BY special_player_id ASC
            LIMIT $2;
            """,
            like, limit,
        )
    return [as_special_player(row) for row in rows]


async def update_special_player(special_player_id: int, values: dict) -> dict | None:
    allowed = {"name", "edition", "country", "role", "bat_level", "bowl_level", "batting_hand", "bowling_hand"}
    clean = {k: v for k, v in values.items() if k in allowed}
    if not clean:
        return await get_special_player_by_id(special_player_id)
    columns = []
    args = []
    for key, value in clean.items():
        columns.append(f"{key} = ${len(args)+1}")
        args.append(value)
    args.append(int(special_player_id))
    row = await fetchrow(
        f"UPDATE special_edition_players SET {', '.join(columns)} WHERE special_player_id = ${len(args)} RETURNING *;",
        *args,
    )
    return as_special_player(row) if row else None


async def update_global_player(player_id: int, values: dict) -> dict | None:
    allowed = {"name", "country", "role", "bat_level", "bowl_level", "batting_hand", "bowling_hand"}
    clean = {k: v for k, v in values.items() if k in allowed}
    if not clean:
        row = await fetchrow("SELECT * FROM players WHERE player_id=$1;", int(player_id))
        return dict(row) if row else None
    columns = []
    args = []
    for key, value in clean.items():
        columns.append(f"{key} = ${len(args)+1}")
        args.append(value)
    args.append(int(player_id))
    row = await fetchrow(
        f"UPDATE players SET {', '.join(columns)} WHERE player_id = ${len(args)} RETURNING *;",
        *args,
    )
    return dict(row) if row else None


async def delete_special_player(special_player_id: int) -> bool:
    await execute("DELETE FROM special_player_card_images WHERE special_player_id=$1;", int(special_player_id))
    result = await execute("DELETE FROM special_edition_players WHERE special_player_id=$1;", int(special_player_id))
    return bool(result) and result.split()[-1] != "0"


async def delete_global_player(player_id: int) -> bool:
    pid = int(player_id)
    # Remove dependent rows first, because these reference players.player_id.
    await execute("DELETE FROM player_card_images WHERE player_id=$1;", pid)
    await execute("DELETE FROM player_claims WHERE player_id=$1;", pid)
    await execute("DELETE FROM player_user_match_stats WHERE player_id=$1;", pid)
    result = await execute("DELETE FROM players WHERE player_id=$1;", pid)
    return bool(result) and result.split()[-1] != "0"


async def get_delete_targets(parsed_player: dict) -> dict:
    """Resolve a delete target while preserving legacy global names.

    Before special editions existed, names containing parentheses were allowed in
    the global players table. A legacy name such as ``Virat Kohli (Test Edition)``
    must therefore be treated as a special edition only when that exact special
    record actually exists; otherwise it remains a global-player name.
    """
    raw_name = str(parsed_player.get("name") or "").strip()
    name, edition = split_player_edition(raw_name)
    if edition:
        player = await get_special_player(name, edition)
        if player:
            return {"kind": "special", "player": player, "name": name, "edition": edition}

        # Legacy fallback: this exact parenthesized value may be the real global
        # player name from before the special-edition feature existed.
        from database.players_repo import get_player
        legacy_global = await get_player(raw_name)
        if legacy_global:
            return {"kind": "global", "player": legacy_global, "name": raw_name, "edition": None}

        return {"kind": "special", "player": None, "name": name, "edition": edition}

    from database.players_repo import get_player
    player = await get_player(name)
    return {"kind": "global", "player": player, "name": name, "edition": None}


async def get_delete_target_by_player_id(player_id: int) -> dict | None:
    """Resolve a delete target from the command-facing player_id namespace.

    Global players keep their positive database player_id. Special-edition players
    are exposed through the existing negative player_id namespace returned by
    as_special_player(), so -N unambiguously identifies special_player_id N.
    """
    pid = int(player_id)
    if pid < 0:
        from services.card_identity import showcase_card_id_from_player_id
        showcase_id = showcase_card_id_from_player_id(pid)
        if showcase_id is not None:
            from database.showcase_players_repo import get_showcase_player
            player = await get_showcase_player(showcase_id)
            if not player:
                return None
            return {"kind": "showcase", "player": player, "name": str(player.get("name") or "").strip(), "edition": None, "showcase_name": player.get("showcase_name")}
        player = await get_special_player_by_id(abs(pid))
        if not player:
            return None
        return {
            "kind": "special",
            "player": player,
            "name": str(player.get("name") or "").strip(),
            "edition": str(player.get("edition") or "").strip(),
        }

    row = await fetchrow("SELECT * FROM players WHERE player_id=$1;", pid)
    if not row:
        return None
    player = dict(row)
    player["is_special"] = False
    player["edition"] = None
    player["special_edition_id"] = None
    return {
        "kind": "global",
        "player": player,
        "name": str(player.get("name") or "").strip(),
        "edition": None,
    }


async def search_delete_candidates(query: str, limit: int = 50) -> list[dict]:
    """Find Global + Special + Showcase cards with one query."""
    q=(query or "").strip()
    if not q:
        return []
    like=f"%{q}%"
    rows=await fetch(
        """
        SELECT source_id, name, country, role, bat_level, bowl_level, batting_hand, bowling_hand,
               uploaded_by, created_at, sort_id, player_kind, is_special, edition, special_edition_id,
               showcase_card_id, showcase_set_id, showcase_name
        FROM (
            SELECT p.player_id::BIGINT AS source_id, p.name, p.country, p.role, p.bat_level, p.bowl_level,
                   p.batting_hand, p.bowling_hand, p.uploaded_by, p.created_at, p.player_id::BIGINT AS sort_id,
                   'global'::TEXT AS player_kind, FALSE AS is_special, NULL::TEXT AS edition,
                   NULL::BIGINT AS special_edition_id, NULL::BIGINT AS showcase_card_id,
                   NULL::BIGINT AS showcase_set_id, NULL::TEXT AS showcase_name
            FROM players p WHERE LOWER(p.name) LIKE LOWER($1)
            UNION ALL
            SELECT sp.special_player_id::BIGINT AS source_id, sp.name, sp.country, sp.role, sp.bat_level, sp.bowl_level,
                   sp.batting_hand, sp.bowling_hand, sp.uploaded_by, sp.created_at, sp.special_player_id::BIGINT AS sort_id,
                   'special'::TEXT AS player_kind, TRUE AS is_special, sp.edition,
                   sp.special_player_id::BIGINT AS special_edition_id, NULL::BIGINT AS showcase_card_id,
                   NULL::BIGINT AS showcase_set_id, NULL::TEXT AS showcase_name
            FROM special_edition_players sp WHERE LOWER(sp.name) LIKE LOWER($1)
            UNION ALL
            SELECT sc.showcase_card_id::BIGINT AS source_id, sc.name, sc.country, sc.role, sc.bat_level, sc.bowl_level,
                   sc.batting_hand, sc.bowling_hand, sc.uploaded_by, sc.created_at, sc.showcase_card_id::BIGINT AS sort_id,
                   'showcase'::TEXT AS player_kind, FALSE AS is_special, NULL::TEXT AS edition,
                   NULL::BIGINT AS special_edition_id, sc.showcase_card_id::BIGINT AS showcase_card_id,
                   sc.showcase_set_id::BIGINT AS showcase_set_id, ss.set_name AS showcase_name
            FROM showcase_cards sc JOIN showcase_sets ss ON ss.showcase_set_id=sc.showcase_set_id
            WHERE LOWER(sc.name) LIKE LOWER($1) OR LOWER(ss.set_name) LIKE LOWER($1)
        ) all_players
        ORDER BY CASE WHEN LOWER(name)=LOWER($2) THEN 0 ELSE 1 END,
                 CASE player_kind WHEN 'global' THEN 0 WHEN 'special' THEN 1 ELSE 2 END,
                 sort_id ASC
        LIMIT $3;
        """, like, q, int(limit),
    )
    result=[]
    for row in rows:
        kind=row['player_kind']
        if kind=='special':
            row['special_player_id']=int(row['special_edition_id'])
            result.append(as_special_player(row))
        elif kind=='showcase':
            from database.showcase_players_repo import as_showcase_player
            result.append(as_showcase_player(row))
        else:
            d=dict(row); d['player_id']=int(d.pop('source_id')); d['is_special']=False; d['is_showcase']=False; d['edition']=None; d['special_edition_id']=None; d['player_kind']='global'; result.append(d)
    return result

