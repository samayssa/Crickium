"""Helpers for storing and reading a user's squad from team_squads."""
from __future__ import annotations

import json
from typing import Any

from database.query import execute, fetch, fetchrow, transaction


async def get_team_squad(user_id: int) -> list[dict[str, Any]] | None:
    row = await fetchrow("SELECT squad FROM team_squads WHERE user_id = $1;", user_id)
    if not row:
        return None

    squad = row["squad"]
    if isinstance(squad, str):
        return json.loads(squad)
    if isinstance(squad, list):
        return squad
    return json.loads(json.dumps(squad, default=str))


async def save_team_squad(user_id: int, squad: list[dict[str, Any]]) -> None:
    squad_json = json.dumps(squad, default=str)
    await execute(
        """
        INSERT INTO team_squads (user_id, squad, updated_at)
        VALUES ($1, $2::jsonb, NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET squad = EXCLUDED.squad, updated_at = NOW();
        """,
        user_id, squad_json,
    )


async def touch_team_squad(user_id: int) -> None:
    await execute("UPDATE team_squads SET updated_at = NOW() WHERE user_id = $1;", user_id)

async def sync_player_snapshot(player_id: int, updates: dict[str, object]) -> int:
    """Propagate an edited player record into every owned squad snapshot.

    team_squads stores denormalized player dictionaries, so editing the global
    or special source row alone does not update an already-purchased snapshot.
    This helper updates matching player_id entries in-place for every squad.
    """
    import json

    clean = {k: v for k, v in (updates or {}).items() if v is not None}
    if not clean:
        return 0
    payload = json.dumps(clean, default=str)
    result = await execute(
        """
        UPDATE team_squads ts
        SET squad = COALESCE((
            SELECT jsonb_agg(
                CASE
                    WHEN elem->>'player_id' = $1::text THEN elem || $2::jsonb
                    ELSE elem
                END
                ORDER BY ord
            )
            FROM jsonb_array_elements(ts.squad) WITH ORDINALITY AS x(elem, ord)
        ), '[]'::jsonb),
            updated_at = NOW()
        WHERE EXISTS (
            SELECT 1
            FROM jsonb_array_elements(ts.squad) AS x(elem)
            WHERE elem->>'player_id' = $1::text
        );
        """,
        str(int(player_id)), payload,
    )
    try:
        return int(str(result).split()[-1])
    except Exception:
        return 0


async def refresh_all_team_squads() -> tuple[int, int]:
    """Refresh denormalized squad cards from Global, Special and Showcase sources.

    This administrative refresh intentionally uses a small, fixed number of
    indexed queries: one query for squads and one query per card family. It does
    not perform one DB query per owned player.
    """
    from services.card_identity import player_kind, showcase_card_id_from_player_id, showcase_squad_player_id

    squad_rows = await fetch("SELECT user_id, squad FROM team_squads;")
    if not squad_rows:
        return 0, 0

    squads_by_user: dict[int, list[dict[str, Any]]] = {}
    global_ids: set[int] = set()
    special_ids: set[int] = set()
    showcase_ids: set[int] = set()
    dead_special_context: list[tuple[int, int, dict[str, Any]]] = []

    for row in squad_rows:
        user_id = int(row["user_id"])
        squad = row["squad"]
        if isinstance(squad, str):
            squad = json.loads(squad)
        squad = list(squad or [])
        squads_by_user[user_id] = [dict(p) for p in squad]
        for raw in squad:
            p = dict(raw or {})
            kind = player_kind(p)
            pid = int(p.get("player_id") or 0)
            if kind == "global" and pid > 0:
                global_ids.add(pid)
            elif kind == "special" and pid < 0:
                special_ids.add(abs(pid))
            elif kind == "showcase":
                cid = int(p.get("showcase_card_id") or showcase_card_id_from_player_id(pid) or 0)
                if cid > 0:
                    showcase_ids.add(cid)

    global_map: dict[int, dict] = {}
    special_map: dict[int, dict] = {}
    showcase_map: dict[int, dict] = {}

    if global_ids:
        rows = await fetch("SELECT * FROM players WHERE player_id = ANY($1::bigint[]);", list(global_ids))
        global_map = {int(r["player_id"]): dict(r) for r in rows}
    if special_ids:
        rows = await fetch("SELECT * FROM special_edition_players WHERE special_player_id = ANY($1::bigint[]);", list(special_ids))
        special_map = {int(r["special_player_id"]): dict(r) for r in rows}
    if showcase_ids:
        rows = await fetch(
            """
            SELECT sc.*, ss.set_name AS showcase_name
            FROM showcase_cards sc
            JOIN showcase_sets ss ON ss.showcase_set_id=sc.showcase_set_id
            WHERE sc.showcase_card_id = ANY($1::bigint[]);
            """,
            list(showcase_ids),
        )
        showcase_map = {int(r["showcase_card_id"]): dict(r) for r in rows}

    # Preserve the existing special delete/recreate recovery semantics, but in
    # one batch query rather than one query per stale card.
    missing_special_context = []
    candidate_names: set[str] = set()
    for user_id, squad in squads_by_user.items():
        for p in squad:
            if player_kind(p) != "special":
                continue
            sid = abs(int(p.get("player_id") or 0))
            if sid not in special_map:
                name = str(p.get("name") or "").strip()
                if name:
                    candidate_names.add(name.casefold())
                missing_special_context.append((user_id, sid, p))
    candidate_map: dict[str, list[dict]] = {}
    if candidate_names:
        rows = await fetch(
            "SELECT * FROM special_edition_players WHERE LOWER(name) = ANY($1::text[]);",
            list(candidate_names),
        )
        for r in rows:
            candidate_map.setdefault(str(r["name"]).casefold(), []).append(dict(r))

    remaps: list[tuple[int, int, int]] = []
    removed: list[tuple[int, int]] = []
    for user_id, squad in squads_by_user.items():
        refreshed: list[dict] = []
        for raw in squad:
            p = dict(raw or {})
            kind = player_kind(p)
            pid = int(p.get("player_id") or 0)
            updated = None

            if kind == "global":
                updated = global_map.get(pid)
                if updated:
                    p.update({
                        "player_id": int(updated["player_id"]),
                        "name": updated["name"], "country": updated["country"], "role": updated["role"],
                        "bat_level": updated["bat_level"], "bowl_level": updated["bowl_level"],
                        "batting_hand": updated["batting_hand"], "bowling_hand": updated["bowling_hand"],
                        "player_kind": "global", "is_special": False, "is_showcase": False,
                        "edition": None, "special_edition_id": None,
                        "showcase_card_id": None, "showcase_set_id": None, "showcase_name": None,
                    })
                else:
                    removed.append((user_id, pid))
                    continue

            elif kind == "special":
                sid = abs(pid)
                updated = special_map.get(sid)
                if not updated:
                    name = str(p.get("name") or "").strip().casefold()
                    candidates = []
                    for candidate in candidate_map.get(name, []):
                        if (
                            int(candidate.get("bat_level") or 0) == int(p.get("bat_level") or 0)
                            and int(candidate.get("bowl_level") or 0) == int(p.get("bowl_level") or 0)
                            and str(candidate.get("country") or "").casefold() == str(p.get("country") or "").casefold()
                            and str(candidate.get("role") or "").casefold() == str(p.get("role") or "").casefold()
                            and str(candidate.get("batting_hand") or "").casefold() == str(p.get("batting_hand") or "").casefold()
                            and str(candidate.get("bowling_hand") or "").casefold() == str(p.get("bowling_hand") or "").casefold()
                        ):
                            candidates.append(candidate)
                    if len(candidates) == 1:
                        updated = candidates[0]
                        new_pid = -int(updated["special_player_id"])
                        remaps.append((user_id, pid, new_pid))
                if updated:
                    p.update({
                        "player_id": -int(updated["special_player_id"]),
                        "name": updated["name"], "country": updated["country"], "role": updated["role"],
                        "bat_level": updated["bat_level"], "bowl_level": updated["bowl_level"],
                        "batting_hand": updated["batting_hand"], "bowling_hand": updated["bowling_hand"],
                        "player_kind": "special", "is_special": True, "is_showcase": False,
                        "edition": updated["edition"], "special_edition_id": int(updated["special_player_id"]),
                        "showcase_card_id": None, "showcase_set_id": None, "showcase_name": None,
                    })
                else:
                    removed.append((user_id, pid))
                    continue

            else:
                cid = int(p.get("showcase_card_id") or showcase_card_id_from_player_id(pid) or 0)
                updated = showcase_map.get(cid)
                if updated:
                    p.update({
                        "player_id": showcase_squad_player_id(int(updated["showcase_card_id"])),
                        "name": updated["name"], "country": updated["country"], "role": updated["role"],
                        "bat_level": updated["bat_level"], "bowl_level": updated["bowl_level"],
                        "batting_hand": updated["batting_hand"], "bowling_hand": updated["bowling_hand"],
                        "player_kind": "showcase", "is_special": False, "is_showcase": True,
                        "edition": None, "special_edition_id": None,
                        "showcase_card_id": int(updated["showcase_card_id"]),
                        "showcase_set_id": int(updated["showcase_set_id"]),
                        "showcase_name": updated["showcase_name"],
                    })
                else:
                    removed.append((user_id, pid))
                    continue

            refreshed.append(p)
        await save_team_squad(user_id, refreshed)

    # Keep /plstats attached when a special card was intentionally re-created.
    for user_id, old_pid, new_pid in remaps:
        await execute(
            """
            UPDATE player_user_match_stats AS old_row
            SET player_id=$3
            WHERE old_row.user_id=$1 AND old_row.player_id=$2
              AND NOT EXISTS (
                  SELECT 1 FROM player_user_match_stats new_row
                  WHERE new_row.match_id=old_row.match_id
                    AND new_row.user_id=old_row.user_id
                    AND new_row.player_id=$3
              );
            """,
            int(user_id), int(old_pid), int(new_pid),
        )
        await execute("DELETE FROM player_user_match_stats WHERE user_id=$1 AND player_id=$2;", int(user_id), int(old_pid))
    for user_id, old_pid in removed:
        await execute("DELETE FROM player_user_match_stats WHERE user_id=$1 AND player_id=$2;", int(user_id), int(old_pid))

    rows = await fetch("SELECT user_id, jsonb_array_length(squad) AS player_count FROM team_squads;")
    return len(rows), sum(int(r["player_count"] or 0) for r in rows)

