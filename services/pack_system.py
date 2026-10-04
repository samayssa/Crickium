from __future__ import annotations

import html
import json
import secrets
import uuid
from typing import Any

from database.query import execute, fetch, fetchrow, transaction
from services.player_card import overall_rating
from utils.PremiumEmoji import pack_emoji_html
from utils.rarity import get_rarity

PACK_CATALOG: dict[str, dict[str, Any]] = {
    "bronze": {"name": "Bronze Pack", "price": 200, "cards": 2, "core": (80, 85), "bonus": (66, 74), "coins": 20_000, "rubies": 50, "description": "Your first serious pull, guaranteed to contain an 80-85 OVR core card."},
    "silver": {"name": "Silver Pack", "price": 450, "cards": 2, "core": (83, 87), "bonus": (70, 79), "coins": 50_000, "rubies": 75, "description": "A stronger climb with an 83-87 OVR core card and a better bonus pull."},
    "gold": {"name": "Gold Pack", "price": 850, "cards": 3, "core": (85, 90), "bonus": (75, 84), "coins": 100_000, "rubies": 125, "description": "A high-tier pull with an 85-90 OVR core card and two strong bonus cards."},
    "diamond": {"name": "Diamond Pack", "price": 1_500, "cards": 3, "core": (88, 94), "bonus": (80, 89), "coins": 200_000, "rubies": 200, "description": "Elite territory with an 88-94 OVR core card and premium bonus pulls."},
    "platinum": {"name": "Platinum Pack", "price": 2_500, "cards": 4, "core": (92, 99), "bonus": (85, 94), "coins": 400_000, "rubies": 300, "description": "The ultimate chase, guaranteeing a 92-99 OVR core card and three elite bonuses."},
}
PACK_KEYS = tuple(PACK_CATALOG)


def normalize_pack_key(value: str | None) -> str | None:
    key = str(value or "").strip().lower()
    aliases = {"bronze": "bronze", "silver": "silver", "gold": "gold", "diamond": "diamond", "platinum": "platinum"}
    return aliases.get(key)


def pack_shop_text() -> str:
    lines = ["<b>╭━━━〔 📦 CRICKIUM PACKS 〕━━━╮</b>", ""]
    for key, p in PACK_CATALOG.items():
        lines.extend([
            f"{pack_emoji_html(key)} <b>{p['name']}</b>",
            f"✨ <b>{p['price']:,} SG</b> • 🎴 <b>{p['cards']} Cards</b>",
            f"├ Core : <b>{p['core'][0]}-{p['core'][1]} OVR</b>",
            f"├ Bonus : <b>{p['bonus'][0]}-{p['bonus'][1]} OVR</b>",
            f"└ 🎁 <b>{p['coins']:,} Coins</b> • 💎 <b>{p['rubies']:,} Rubies</b>",
            f"<blockquote><i>{html.escape(p['description'])}</i></blockquote>",
            "",
        ])
    lines.append("<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>")
    return "\n".join(lines)


async def create_purchase_request(user_id: int, pack_key: str) -> dict[str, Any] | None:
    key = normalize_pack_key(pack_key)
    if not key:
        return None
    p = PACK_CATALOG[key]
    request_id = uuid.uuid4()
    await execute(
        """
        INSERT INTO pack_purchase_requests(request_id,user_id,pack_key,price_sigils,status,created_at)
        VALUES($1,$2,$3,$4,'pending',NOW());
        """,
        request_id, int(user_id), key, int(p["price"]),
    )
    row = await fetchrow("SELECT sigils FROM users WHERE user_id=$1;", int(user_id))
    current = int(row["sigils"] or 0) if row else 0
    return {"request_id": str(request_id), "user_id": int(user_id), "pack_key": key, "sigils": current}


async def confirm_purchase(user_id: int, request_id: str) -> dict[str, Any]:
    rid = uuid.UUID(str(request_id))

    async def _tx(conn):
        req = await conn.fetchrow(
            "SELECT * FROM pack_purchase_requests WHERE request_id=$1 FOR UPDATE;", rid
        )
        if not req:
            return {"status": "missing"}
        if int(req["user_id"]) != int(user_id):
            return {"status": "owner"}
        if req["status"] != "pending":
            return {"status": str(req["status"])}
        key = normalize_pack_key(req["pack_key"])
        if not key:
            await conn.execute("UPDATE pack_purchase_requests SET status='failed',processed_at=NOW() WHERE request_id=$1;", rid)
            return {"status": "failed"}
        price = int(req["price_sigils"])
        user = await conn.fetchrow("SELECT sigils FROM users WHERE user_id=$1 FOR UPDATE;", int(user_id))
        balance = int(user["sigils"] or 0) if user else 0
        if balance < price:
            return {"status": "insufficient", "balance": balance, "price": price}
        await conn.execute(
            "UPDATE users SET sigils=sigils-$1,total_sigils_spent=COALESCE(total_sigils_spent,0)+$1,last_seen_at=NOW() WHERE user_id=$2;",
            price, int(user_id),
        )
        await conn.execute(
            """
            INSERT INTO pack_inventory(user_id,pack_key,quantity,updated_at)
            VALUES($1,$2,1,NOW())
            ON CONFLICT(user_id,pack_key)
            DO UPDATE SET quantity=pack_inventory.quantity+1,updated_at=NOW();
            """,
            int(user_id), key,
        )
        await conn.execute(
            "UPDATE pack_purchase_requests SET status='confirmed',processed_at=NOW() WHERE request_id=$1;", rid
        )
        return {"status": "success", "pack_key": key, "price": price, "balance": balance - price}

    return await transaction(_tx)


async def inventory(user_id: int) -> dict[str, int]:
    rows = await fetch("SELECT pack_key,quantity FROM pack_inventory WHERE user_id=$1;", int(user_id))
    result = {key: 0 for key in PACK_KEYS}
    for row in rows:
        key = normalize_pack_key(row["pack_key"])
        if key:
            result[key] = max(0, int(row["quantity"] or 0))
    return result


async def _candidate_players(low: int, high: int) -> list[dict[str, Any]]:
    # Include both normal and special-edition player cards in pack pools. The
    # existing card renderer already understands both shapes.
    rows = await fetch(
        """
        SELECT player_id::bigint AS player_id, name, country, role, bat_level, bowl_level,
               batting_hand, bowling_hand, FALSE AS is_special, NULL::bigint AS special_edition_id,
               NULL::text AS edition
        FROM players
        WHERE GREATEST(COALESCE(bat_level,0),COALESCE(bowl_level,0)) BETWEEN $1 AND $2
        UNION ALL
        SELECT (-special_player_id)::bigint AS player_id, name, country, role, bat_level, bowl_level,
               batting_hand, bowling_hand, TRUE AS is_special, special_player_id,
               edition
        FROM special_edition_players
        WHERE GREATEST(COALESCE(bat_level,0),COALESCE(bowl_level,0)) BETWEEN $1 AND $2
        """,
        int(low), int(high),
    )
    return [dict(r) for r in rows]


def _sample(pool: list[dict[str, Any]], count: int, *, exclude_ids: set[int] | None = None) -> list[dict[str, Any]]:
    exclude_ids = exclude_ids or set()
    available = [p for p in pool if int(p.get("player_id") or 0) not in exclude_ids]
    if not available:
        available = list(pool)
    if len(available) <= count:
        if not available:
            return []
        chosen = list(available)
        while len(chosen) < count:
            chosen.append(secrets.choice(available))
        return chosen
    return secrets.SystemRandom().sample(available, count)


async def open_pack(user_id: int, pack_key: str) -> dict[str, Any]:
    key = normalize_pack_key(pack_key)
    if not key:
        return {"status": "invalid"}
    p = PACK_CATALOG[key]
    opening_id = uuid.uuid4()

    core_pool = await _candidate_players(*p["core"])
    bonus_pool = await _candidate_players(*p["bonus"])
    if not core_pool or not bonus_pool:
        return {"status": "no_player"}

    core = _sample(core_pool, 1)[0]
    bonus = _sample(bonus_pool, int(p["cards"]) - 1, exclude_ids={int(core.get("player_id") or 0)})
    if len(bonus) < int(p["cards"]) - 1:
        return {"status": "no_player"}
    raw_cards = [{"kind": "core", "player": dict(core)}]
    raw_cards.extend({"kind": "bonus", "player": dict(item)} for item in bonus)
    cards = []
    for item in raw_cards:
        player = dict(item["player"])
        cards.append({
            "kind": item["kind"],
            "player": player,
            "player_id": int(player.get("player_id") or 0),
            "ovr": overall_rating(player.get("bat_level"), player.get("bowl_level")),
            "rarity": get_rarity(overall_rating(player.get("bat_level"), player.get("bowl_level"))),
        })

    async def _tx(conn):
        inv = await conn.fetchrow(
            "SELECT quantity FROM pack_inventory WHERE user_id=$1 AND pack_key=$2 FOR UPDATE;", int(user_id), key
        )
        quantity = int(inv["quantity"] or 0) if inv else 0
        if quantity <= 0:
            return {"status": "empty"}
        await conn.execute(
            "UPDATE pack_inventory SET quantity=quantity-1,updated_at=NOW() WHERE user_id=$1 AND pack_key=$2;",
            int(user_id), key,
        )
        await conn.execute(
            """
            INSERT INTO pack_openings(opening_id,user_id,pack_key,cards,coins,rubies,status,opened_at)
            VALUES($1,$2,$3,$4::jsonb,$5,$6,'opened',NOW());
            """,
            opening_id, int(user_id), key, json.dumps(cards, default=str), int(p["coins"]), int(p["rubies"]),
        )
        for pos, card in enumerate(cards, start=1):
            await conn.execute(
                """
                INSERT INTO pack_card_inventory(opening_id,user_id,player_id,card_kind,position)
                VALUES($1,$2,$3,$4,$5);
                """,
                opening_id, int(user_id), int(card["player_id"]), str(card["kind"]), pos,
            )
        await conn.execute(
            """
            UPDATE users SET balance=COALESCE(balance,0)+$1,
                             rubies=COALESCE(rubies,0)+$2,
                             total_rubies_earned=COALESCE(total_rubies_earned,0)+$2,
                             last_seen_at=NOW()
            WHERE user_id=$3;
            """,
            int(p["coins"]), int(p["rubies"]), int(user_id),
        )
        return {"status": "success"}

    result = await transaction(_tx)
    if result.get("status") != "success":
        return result
    return {"status": "success", "opening_id": str(opening_id), "pack_key": key, "cards": cards, "coins": int(p["coins"]), "rubies": int(p["rubies"])}


async def get_opening(user_id: int, opening_id: str) -> dict[str, Any] | None:
    try:
        oid = uuid.UUID(str(opening_id))
    except Exception:
        return None
    row = await fetchrow("SELECT * FROM pack_openings WHERE opening_id=$1 AND user_id=$2;", oid, int(user_id))
    if not row:
        return None
    data = dict(row)
    cards = data.get("cards")
    if isinstance(cards, str):
        cards = json.loads(cards)
    data["cards"] = cards or []
    data["pack_key"] = normalize_pack_key(data.get("pack_key"))
    return data
