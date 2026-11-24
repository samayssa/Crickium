from __future__ import annotations

import json
from typing import Any

from database.query import fetch, fetchrow, transaction
from database.special_players_repo import as_special_player
from database.showcase_players_repo import as_showcase_player
from services.card_identity import card_entity_id, card_identity_key, player_kind
from services.player_card import overall_rating
from utils.price_chart import get_price
from utils.user_locks import user_lock

KIND_ORDER = {"global": 0, "special": 1, "showcase": 2}
MAX_TARGETS = 4


def card_ovr(player: dict[str, Any]) -> int:
    return int(overall_rating(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0)))


def evolve_quote(old: dict[str, Any], new: dict[str, Any]) -> dict[str, int]:
    old_ovr = card_ovr(old)
    new_ovr = card_ovr(new)
    buy_price = int(get_price(new_ovr)[0])
    sell_value = int(get_price(old_ovr)[1])
    return {
        "old_ovr": old_ovr,
        "new_ovr": new_ovr,
        "buy_price": buy_price,
        "sell_value": sell_value,
        "cost": max(0, buy_price - sell_value),
    }


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _global_card(row: Any) -> dict[str, Any]:
    card = dict(row)
    card.update({"player_kind": "global", "is_special": False, "is_showcase": False})
    return card


async def load_card(kind: str, entity_id: int, conn: Any = None) -> dict[str, Any] | None:
    reader = conn.fetchrow if conn is not None else fetchrow
    entity_id = int(entity_id)
    if kind == "global":
        row = await reader("SELECT * FROM players WHERE player_id=$1;", entity_id)
        return _global_card(row) if row else None
    if kind == "special":
        row = await reader("SELECT * FROM special_edition_players WHERE special_player_id=$1;", entity_id)
        return as_special_player(row) if row else None
    if kind == "showcase":
        row = await reader(
            """
            SELECT c.*, s.set_name AS showcase_name
            FROM showcase_cards c JOIN showcase_sets s ON s.showcase_set_id=c.showcase_set_id
            WHERE c.showcase_card_id=$1 AND s.status='active';
            """,
            entity_id,
        )
        return as_showcase_player(row) if row else None
    return None


async def _editions_by_name(names: list[str]) -> list[dict[str, Any]]:
    if not names:
        return []
    cards: list[dict[str, Any]] = []
    for row in await fetch("SELECT * FROM players WHERE LOWER(name)=ANY($1::text[]);", names):
        cards.append(_global_card(row))
    for row in await fetch("SELECT * FROM special_edition_players WHERE LOWER(name)=ANY($1::text[]);", names):
        cards.append(as_special_player(row))
    for row in await fetch(
        """
        SELECT c.*, s.set_name AS showcase_name
        FROM showcase_cards c JOIN showcase_sets s ON s.showcase_set_id=c.showcase_set_id
        WHERE LOWER(c.name)=ANY($1::text[]) AND s.status='active';
        """,
        names,
    ):
        cards.append(as_showcase_player(row))
    return cards


def _name_key(card: dict[str, Any]) -> str:
    return str(card.get("name") or "").strip().lower()


def pick_targets(squad: list[dict[str, Any]], editions: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    owned_keys = {card_identity_key(card) for card in squad}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for card in editions:
        by_name.setdefault(_name_key(card), []).append(card)
    result: dict[int, list[dict[str, Any]]] = {}
    for card in squad:
        own_ovr = card_ovr(card)
        better = [
            option for option in by_name.get(_name_key(card), [])
            if card_ovr(option) > own_ovr and card_identity_key(option) not in owned_keys
        ]
        better.sort(key=lambda option: (-card_ovr(option), KIND_ORDER.get(player_kind(option), 9), card_entity_id(option)))
        if better:
            result[int(card.get("player_id") or 0)] = better[:MAX_TARGETS]
    return result


async def targets_for_squad(squad: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    names = sorted({_name_key(card) for card in squad if _name_key(card)})
    return pick_targets(squad, await _editions_by_name(names))


async def read_squad(user_id: int) -> list[dict[str, Any]]:
    row = await fetchrow("SELECT squad FROM team_squads WHERE user_id=$1;", int(user_id))
    return _as_list(row["squad"]) if row else []


async def targets_for_user(user_id: int) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    squad = await read_squad(user_id)
    return squad, await targets_for_squad(squad)


def _loadout_slots(loadout: Any) -> list[tuple[str, int]]:
    slots = []
    if loadout is None:
        return slots
    if loadout["batting_upgrade_id"] is not None:
        slots.append(("batting", int(loadout["batting_upgrade_id"])))
    if loadout["bowling_upgrade_id"] is not None:
        slots.append(("bowling", int(loadout["bowling_upgrade_id"])))
    return slots


async def _slot_fits(conn: Any, slot: str, upgrade_id: int, card: dict[str, Any]) -> tuple[bool, str]:
    from services.player_upgrades import bowler_family, role_key
    row = await conn.fetchrow(
        "SELECT name, category, eligible_roles, eligible_family FROM upgrade_catalog WHERE upgrade_id=$1 AND active=TRUE;",
        int(upgrade_id),
    )
    if not row:
        return False, "Upgrade"
    name = str(row["name"] or "Upgrade")
    roles = row["eligible_roles"] or []
    if isinstance(roles, str):
        try:
            roles = json.loads(roles)
        except (TypeError, ValueError):
            roles = []
    if role_key(card.get("role")) not in {str(item).strip().lower() for item in roles}:
        return False, name
    family = row["eligible_family"]
    if slot == "bowling" and family and bowler_family(card.get("role"), card.get("bowling_hand")) != family:
        return False, name
    return True, name


async def _move_loadout(conn: Any, user_id: int, old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
    old_id = int(old.get("player_id") or 0)
    new_id = int(new.get("player_id") or 0)
    old_kind = player_kind(old)
    new_kind = player_kind(new)
    loadout = await conn.fetchrow(
        "SELECT batting_upgrade_id, bowling_upgrade_id FROM user_player_loadouts WHERE user_id=$1 AND player_id=$2 AND player_kind=$3 FOR UPDATE;",
        user_id, old_id, old_kind,
    )
    moved: list[str] = []
    dropped: list[str] = []
    keep: dict[str, int] = {}
    for slot, upgrade_id in _loadout_slots(loadout):
        fits, name = await _slot_fits(conn, slot, upgrade_id, new)
        if fits:
            keep[slot] = upgrade_id
            moved.append(name)
        else:
            dropped.append(name)
    await conn.execute(
        "DELETE FROM user_player_loadouts WHERE user_id=$1 AND ((player_id=$2 AND player_kind=$3) OR (player_id=$4 AND player_kind=$5));",
        user_id, old_id, old_kind, new_id, new_kind,
    )
    if keep:
        await conn.execute(
            "INSERT INTO user_player_loadouts(user_id, player_id, player_kind, batting_upgrade_id, bowling_upgrade_id) VALUES ($1,$2,$3,$4,$5);",
            user_id, new_id, new_kind, keep.get("batting"), keep.get("bowling"),
        )
    return {"moved": moved, "dropped": dropped}


async def perform_evolve(user_id: int, old_player_id: int, kind: str, entity_id: int) -> dict[str, Any]:
    user_id = int(user_id)
    old_player_id = int(old_player_id)

    async def _tx(conn):
        user = await conn.fetchrow("SELECT balance, captain_player_id FROM users WHERE user_id=$1 FOR UPDATE;", user_id)
        if not user:
            return {"status": "user_missing"}
        row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", user_id)
        squad = _as_list(row["squad"]) if row else []
        index = next((i for i, card in enumerate(squad) if int(card.get("player_id") or 0) == old_player_id), None)
        if index is None:
            return {"status": "player_not_owned"}
        old = squad[index]
        new = await load_card(kind, int(entity_id), conn)
        if new is None:
            return {"status": "target_not_found"}
        if _name_key(new) != _name_key(old):
            return {"status": "not_same_player"}
        quote = evolve_quote(old, new)
        if quote["new_ovr"] <= quote["old_ovr"]:
            return {"status": "not_an_upgrade", **quote}
        if any(card_identity_key(card) == card_identity_key(new) for card in squad):
            return {"status": "already_owned"}
        balance = int(user["balance"] or 0)
        if balance < quote["cost"]:
            return {"status": "insufficient_balance", "balance": balance, **quote}
        await conn.execute(
            "UPDATE users SET balance=COALESCE(balance,0)-$1, total_spent=COALESCE(total_spent,0)+$1, last_seen_at=NOW() WHERE user_id=$2;",
            quote["cost"], user_id,
        )
        new_id = int(new.get("player_id") or 0)
        squad[index] = new
        await conn.execute(
            "UPDATE team_squads SET squad=$2::jsonb, updated_at=NOW() WHERE user_id=$1;",
            user_id, json.dumps(squad, default=str),
        )
        lineup_row = await conn.fetchrow("SELECT player_ids FROM team_lineups WHERE user_id=$1 FOR UPDATE;", user_id)
        in_lineup = False
        if lineup_row:
            raw = lineup_row["player_ids"]
            if isinstance(raw, str):
                raw = json.loads(raw)
            ids = [int(item) for item in (raw or [])]
            if old_player_id in ids:
                in_lineup = True
                ids = [new_id if item == old_player_id else item for item in ids]
                await conn.execute(
                    "UPDATE team_lineups SET player_ids=$2::jsonb, updated_at=NOW() WHERE user_id=$1;",
                    user_id, json.dumps(ids),
                )
        was_captain = user["captain_player_id"] is not None and int(user["captain_player_id"]) == old_player_id
        if was_captain:
            await conn.execute("UPDATE users SET captain_player_id=$2 WHERE user_id=$1;", user_id, new_id)
        upgrades = await _move_loadout(conn, user_id, old, new)
        for stmt_player in (old_player_id, new_id):
            try:
                async with conn.transaction():
                    await conn.execute("DELETE FROM player_user_match_stats WHERE user_id=$1 AND player_id=$2;", user_id, stmt_player)
            except Exception:
                pass
        return {
            "status": "success",
            "old": old,
            "new": new,
            "balance": balance - quote["cost"],
            "in_playing_xi": in_lineup,
            "was_captain": was_captain,
            "upgrades_moved": upgrades["moved"],
            "upgrades_dropped": upgrades["dropped"],
            **quote,
        }

    async with user_lock(user_id):
        result = await transaction(_tx)
    return result
