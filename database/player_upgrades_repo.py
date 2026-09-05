"""Database access for player upgrades, inventory, loadouts and match snapshots."""
from __future__ import annotations

import json
from typing import Any

from database.query import execute, fetch, fetchrow, fetchval, transaction
from services.player_upgrades import UPGRADES
from utils.upgrade_prices import UPGRADE_RUBY_PRICES


async def list_catalog(category: str | None = None) -> list[dict[str, Any]]:
    if category:
        rows = await fetch(
            "SELECT * FROM upgrade_catalog WHERE active = TRUE AND category = $1 ORDER BY upgrade_id;",
            category,
        )
    else:
        rows = await fetch("SELECT * FROM upgrade_catalog WHERE active = TRUE ORDER BY upgrade_id;")
    return [dict(r) for r in rows]


async def get_upgrade(upgrade_key: str) -> dict[str, Any] | None:
    row = await fetchrow(
        "SELECT * FROM upgrade_catalog WHERE active = TRUE AND upgrade_key = $1;",
        str(upgrade_key).strip().lower(),
    )
    return dict(row) if row else None


async def get_upgrade_by_name(name: str) -> dict[str, Any] | None:
    row = await fetchrow(
        """SELECT * FROM upgrade_catalog WHERE active = TRUE AND LOWER(name) = LOWER($1) LIMIT 1;""",
        str(name).strip(),
    )
    return dict(row) if row else None


async def get_upgrade_tiers(upgrade_id: int) -> list[dict[str, Any]]:
    rows = await fetch(
        "SELECT * FROM upgrade_catalog_tiers WHERE upgrade_id = $1 ORDER BY tier;",
        int(upgrade_id),
    )
    return [dict(r) for r in rows]


async def user_owned_upgrades(user_id: int) -> list[dict[str, Any]]:
    rows = await fetch(
        """
        SELECT uui.id, uui.user_id, uui.upgrade_id, uui.tier, uui.owned_at,
               uc.upgrade_key, uc.name, uc.category, uc.description, uc.detail,
               uui.source
        FROM user_player_upgrades uui
        JOIN upgrade_catalog uc ON uc.upgrade_id = uui.upgrade_id
        WHERE uui.user_id = $1
        ORDER BY uc.upgrade_id, uui.tier DESC;
        """,
        int(user_id),
    )
    return [dict(r) for r in rows]


async def next_owned_tier(user_id: int, upgrade_id: int) -> int | None:
    highest = await fetchval(
        "SELECT MAX(tier) FROM user_player_upgrades WHERE user_id = $1 AND upgrade_id = $2;",
        int(user_id), int(upgrade_id),
    )
    highest = int(highest or 0)
    return highest + 1 if highest < 4 else None


async def purchase_upgrade(user_id: int, upgrade_id: int, tier: int, price: int) -> str:
    if int(tier) not in UPGRADE_RUBY_PRICES or int(price) != int(UPGRADE_RUBY_PRICES[int(tier)]):
        return "invalid_purchase"

    async def _tx(conn):
        # Lock the user's row so two concurrent purchases cannot both spend the same balance.
        row = await conn.fetchrow("SELECT rubies FROM users WHERE user_id = $1 FOR UPDATE;", int(user_id))
        if not row:
            return "user_missing"
        current = int(row["rubies"] or 0)
        if current < int(price):
            return "insufficient"
        highest = int(await conn.fetchval(
            "SELECT MAX(tier) FROM user_player_upgrades WHERE user_id = $1 AND upgrade_id = $2;",
            int(user_id), int(upgrade_id),
        ) or 0)
        if int(tier) != highest + 1:
            return "already_owned" if int(tier) <= highest else "invalid_purchase"
        exists = await conn.fetchval(
            "SELECT 1 FROM user_player_upgrades WHERE user_id = $1 AND upgrade_id = $2 AND tier = $3;",
            int(user_id), int(upgrade_id), int(tier),
        )
        if exists:
            return "already_owned"
        await conn.execute(
            "UPDATE users SET rubies = rubies - $1 WHERE user_id = $2;",
            int(price), int(user_id),
        )
        await conn.execute(
            """
            INSERT INTO user_player_upgrades(user_id, upgrade_id, tier, source)
            VALUES ($1,$2,$3,'shop');
            """,
            int(user_id), int(upgrade_id), int(tier),
        )
        return "success"
    return await transaction(_tx)


async def equipped_for_player(user_id: int, player_id: int, player_kind: str) -> dict[str, Any] | None:
    row = await fetchrow(
        """
        SELECT l.*, b.upgrade_key AS batting_key, b.name AS batting_name, b.category AS batting_category, b.detail AS batting_detail,
               bo.upgrade_key AS bowling_key, bo.name AS bowling_name, bo.category AS bowling_category, bo.detail AS bowling_detail
        FROM user_player_loadouts l
        LEFT JOIN upgrade_catalog b ON b.upgrade_id = l.batting_upgrade_id
        LEFT JOIN upgrade_catalog bo ON bo.upgrade_id = l.bowling_upgrade_id
        WHERE l.user_id = $1 AND l.player_id = $2 AND l.player_kind = $3;
        """,
        int(user_id), int(player_id), player_kind,
    )
    return dict(row) if row else None


async def list_equipped(user_id: int) -> list[dict[str, Any]]:
    rows = await fetch(
        """
        SELECT l.*, b.name AS batting_name, b.upgrade_key AS batting_key,
               bo.name AS bowling_name, bo.upgrade_key AS bowling_key
        FROM user_player_loadouts l
        LEFT JOIN upgrade_catalog b ON b.upgrade_id = l.batting_upgrade_id
        LEFT JOIN upgrade_catalog bo ON bo.upgrade_id = l.bowling_upgrade_id
        WHERE l.user_id = $1 AND (l.batting_upgrade_id IS NOT NULL OR l.bowling_upgrade_id IS NOT NULL)
        ORDER BY l.player_id;
        """,
        int(user_id),
    )
    return [dict(r) for r in rows]


async def equip_upgrade(user_id: int, player_id: int, player_kind: str, upgrade_id: int, tier: int, slot: str) -> str:
    if slot not in {"batting", "bowling"}:
        return "invalid_slot"

    async def _tx(conn):
        owned = await conn.fetchval(
            "SELECT 1 FROM user_player_upgrades WHERE user_id=$1 AND upgrade_id=$2 AND tier=$3;",
            int(user_id), int(upgrade_id), int(tier),
        )
        if not owned:
            return "not_owned"

        squad_row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(user_id))
        if not squad_row:
            return "player_not_owned"
        raw_squad = squad_row["squad"]
        if isinstance(raw_squad, str):
            raw_squad = json.loads(raw_squad)
        def _player_kind(player_row: dict[str, Any]) -> str:
            value = player_row.get("is_special")
            is_special = value is True or (
                isinstance(value, str) and value.strip().lower() in {"1", "true", "yes"}
            )
            return "special" if is_special or int(player_row.get("player_id") or 0) < 0 else "global"

        player = next((p for p in (raw_squad or []) if int(p.get("player_id") or 0) == int(player_id) and _player_kind(p) == player_kind), None)
        if not player:
            return "player_not_owned"

        catalog = await conn.fetchrow("SELECT category, eligible_roles, eligible_family FROM upgrade_catalog WHERE upgrade_id=$1 AND active=TRUE;", int(upgrade_id))
        if not catalog:
            return "upgrade_missing"
        from services.player_upgrades import bowler_family, role_key
        role = role_key(player.get("role"))
        eligible_roles = catalog["eligible_roles"] or []
        # asyncpg commonly returns JSON/JSONB columns as text unless a custom
        # codec is installed. Treat both decoded arrays and JSON text alike.
        if isinstance(eligible_roles, str):
            try:
                eligible_roles = json.loads(eligible_roles)
            except (TypeError, ValueError):
                eligible_roles = []
        eligible_roles = {str(item).strip().lower() for item in (eligible_roles or [])}
        if role not in eligible_roles:
            return "role_ineligible"
        family = catalog["eligible_family"]
        if family and bowler_family(player.get("role"), player.get("bowling_hand")) != family:
            return "family_ineligible"

        current = await conn.fetchrow(
            "SELECT batting_upgrade_id, bowling_upgrade_id FROM user_player_loadouts WHERE user_id=$1 AND player_id=$2 AND player_kind=$3 FOR UPDATE;",
            int(user_id), int(player_id), player_kind,
        )
        if current:
            current_id = current["batting_upgrade_id"] if slot == "batting" else current["bowling_upgrade_id"]
            if current_id is not None:
                return "slot_occupied"
        column = "batting_upgrade_id" if slot == "batting" else "bowling_upgrade_id"
        if current:
            await conn.execute(
                f"UPDATE user_player_loadouts SET {column}=$1, updated_at=NOW() WHERE user_id=$2 AND player_id=$3 AND player_kind=$4;",
                int(upgrade_id), int(user_id), int(player_id), player_kind,
            )
        else:
            await conn.execute(
                f"INSERT INTO user_player_loadouts(user_id, player_id, player_kind, {column}) VALUES ($1,$2,$3,$4);",
                int(user_id), int(player_id), player_kind, int(upgrade_id),
            )
        return "success"
    return await transaction(_tx)


async def unequip_upgrade(user_id: int, player_id: int, player_kind: str, slot: str) -> str:
    if slot not in {"batting", "bowling"}:
        return "invalid_slot"
    column = "batting_upgrade_id" if slot == "batting" else "bowling_upgrade_id"
    async def _tx(conn):
        squad_row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(user_id))
        if not squad_row:
            return "player_not_owned"
        raw_squad = squad_row["squad"]
        if isinstance(raw_squad, str):
            raw_squad = json.loads(raw_squad)
        def _player_kind(player_row: dict[str, Any]) -> str:
            value = player_row.get("is_special")
            is_special = value is True or (
                isinstance(value, str) and value.strip().lower() in {"1", "true", "yes"}
            )
            return "special" if is_special or int(player_row.get("player_id") or 0) < 0 else "global"

        player = next((p for p in (raw_squad or []) if int(p.get("player_id") or 0) == int(player_id) and _player_kind(p) == player_kind), None)
        if not player:
            return "player_not_owned"
        row = await conn.fetchrow(
            f"SELECT {column} FROM user_player_loadouts WHERE user_id=$1 AND player_id=$2 AND player_kind=$3 FOR UPDATE;",
            int(user_id), int(player_id), player_kind,
        )
        if not row or row[column] is None:
            return "not_equipped"
        await conn.execute(
            f"UPDATE user_player_loadouts SET {column}=NULL, updated_at=NOW() WHERE user_id=$1 AND player_id=$2 AND player_kind=$3;",
            int(user_id), int(player_id), player_kind,
        )
        return "success"
    return await transaction(_tx)


async def load_snapshot_players(user_ids: list[int], players_by_user: dict[int, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for uid in user_ids:
        rows = await fetch(
            """
            SELECT l.player_id, l.player_kind, l.batting_upgrade_id, l.bowling_upgrade_id,
                   b.upgrade_key AS batting_key, b.tier AS batting_tier,
                   bo.upgrade_key AS bowling_key, bo.tier AS bowling_tier
            FROM user_player_loadouts l
            LEFT JOIN LATERAL (
                SELECT uc.upgrade_key, uui.tier
                FROM user_player_upgrades uui
                JOIN upgrade_catalog uc ON uc.upgrade_id=uui.upgrade_id
                WHERE uui.user_id=l.user_id AND uui.upgrade_id=l.batting_upgrade_id
                ORDER BY uui.tier DESC LIMIT 1
            ) b ON TRUE
            LEFT JOIN LATERAL (
                SELECT uc.upgrade_key, uui.tier
                FROM user_player_upgrades uui
                JOIN upgrade_catalog uc ON uc.upgrade_id=uui.upgrade_id
                WHERE uui.user_id=l.user_id AND uui.upgrade_id=l.bowling_upgrade_id
                ORDER BY uui.tier DESC LIMIT 1
            ) bo ON TRUE
            WHERE l.user_id=$1;
            """,
            int(uid),
        )
        allowed_ids = {int(p.get("player_id") or 0) for p in players_by_user.get(int(uid), [])}
        for row in rows:
            pid = int(row["player_id"])
            if pid not in allowed_ids:
                continue
            kind = str(row["player_kind"])
            snapshot[f"{uid}:{kind}:{pid}"] = {
                "user_id": int(uid), "player_id": pid, "player_kind": kind,
                "batting": {"upgrade_key": row["batting_key"], "tier": int(row["batting_tier"] or 1)} if row["batting_key"] else None,
                "bowling": {"upgrade_key": row["bowling_key"], "tier": int(row["bowling_tier"] or 1)} if row["bowling_key"] else None,
            }
    return snapshot


async def persist_snapshot(match_id: int, snapshot: dict[str, dict[str, Any]]) -> None:
    for item in snapshot.values():
        await execute(
            """
            INSERT INTO match_player_upgrade_snapshots
                (match_id,user_id,player_id,player_kind,batting_upgrade_key,batting_tier,bowling_upgrade_key,bowling_tier,snapshot_json)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
            ON CONFLICT (match_id,user_id,player_id,player_kind)
            DO UPDATE SET snapshot_json=EXCLUDED.snapshot_json,
                          batting_upgrade_key=EXCLUDED.batting_upgrade_key,
                          batting_tier=EXCLUDED.batting_tier,
                          bowling_upgrade_key=EXCLUDED.bowling_upgrade_key,
                          bowling_tier=EXCLUDED.bowling_tier;
            """,
            int(match_id), int(item["user_id"]), int(item["player_id"]), item["player_kind"],
            (item.get("batting") or {}).get("upgrade_key"), (item.get("batting") or {}).get("tier"),
            (item.get("bowling") or {}).get("upgrade_key"), (item.get("bowling") or {}).get("tier"),
            json.dumps(item),
        )


async def restore_snapshot(match_id: int) -> dict[str, dict[str, Any]]:
    rows = await fetch("SELECT snapshot_json FROM match_player_upgrade_snapshots WHERE match_id=$1;", int(match_id))
    result = {}
    for row in rows:
        raw = row["snapshot_json"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                raw = None
        result_key = f"{raw['user_id']}:{raw['player_kind']}:{raw['player_id']}" if isinstance(raw, dict) else None
        if result_key:
            result[result_key] = dict(raw)
    return result
