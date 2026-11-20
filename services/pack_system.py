from __future__ import annotations

import html
import json
import secrets
import uuid
from typing import Any

from database.query import execute, fetch, fetchrow, transaction
from services.card_identity import owned_same_card, player_kind as _card_kind
from services.player_card import overall_rating
from utils.price_chart import get_price
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
MAX_SQUAD_SIZE = 25


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


async def create_purchase_request(user_id: int, pack_key: str, client_request_id: str | None = None) -> dict[str, Any] | None:
    key = normalize_pack_key(pack_key)
    if not key:
        return None
    p = PACK_CATALOG[key]
    request_key = str(client_request_id or "").strip()[:80] or None
    request_id = uuid.uuid4()

                                                                               
                                                                             
    if request_key:
        existing = await fetchrow(
            "SELECT request_id,user_id,pack_key,price_sigils,status FROM pack_purchase_requests WHERE user_id=$1 AND client_request_id=$2;",
            int(user_id), request_key,
        )
        if existing:
            row = await fetchrow("SELECT sigils FROM users WHERE user_id=$1;", int(user_id))
            return {
                "request_id": str(existing["request_id"]), "user_id": int(user_id),
                "pack_key": str(existing["pack_key"]), "price": int(existing["price_sigils"]),
                "status": str(existing["status"]), "sigils": int((row or {}).get("sigils") or 0),
                "replayed": True,
            }

    if request_key:
        inserted = await fetchrow(
            """
            INSERT INTO pack_purchase_requests(request_id,user_id,pack_key,price_sigils,status,created_at,client_request_id)
            VALUES($1,$2,$3,$4,'pending',NOW(),$5)
            ON CONFLICT (user_id,client_request_id) WHERE client_request_id IS NOT NULL DO NOTHING
            RETURNING request_id;
            """,
            request_id, int(user_id), key, int(p["price"]), request_key,
        )
        if not inserted:
            existing = await fetchrow(
                "SELECT request_id,user_id,pack_key,price_sigils,status FROM pack_purchase_requests WHERE user_id=$1 AND client_request_id=$2;",
                int(user_id), request_key,
            )
            if not existing:
                return None
            request_id = existing["request_id"]
            key = str(existing["pack_key"])
            p = PACK_CATALOG[key]
    else:
        await execute(
            """
            INSERT INTO pack_purchase_requests(request_id,user_id,pack_key,price_sigils,status,created_at)
            VALUES($1,$2,$3,$4,'pending',NOW());
            """,
            request_id, int(user_id), key, int(p["price"]),
        )

    row = await fetchrow("SELECT sigils FROM users WHERE user_id=$1;", int(user_id))
    current = int(row["sigils"] or 0) if row else 0
    return {
        "request_id": str(request_id), "user_id": int(user_id), "pack_key": key,
        "price": int(p["price"]), "status": "pending", "sigils": current,
        "replayed": False,
    }


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
        if req["status"] == "confirmed":
            user = await conn.fetchrow("SELECT sigils FROM users WHERE user_id=$1;", int(user_id))
            return {"status": "success", "already_confirmed": True, "pack_key": str(req["pack_key"]),
                    "price": int(req["price_sigils"]), "balance": int((user or {}).get("sigils") or 0)}
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


async def cancel_purchase(user_id: int, request_id: str) -> dict[str, Any]:
    try:
        rid = uuid.UUID(str(request_id))
    except (TypeError, ValueError, AttributeError):
        return {"status": "invalid"}

    async def _tx(conn):
        req = await conn.fetchrow("SELECT * FROM pack_purchase_requests WHERE request_id=$1 FOR UPDATE;", rid)
        if not req:
            return {"status": "missing"}
        if int(req["user_id"]) != int(user_id):
            return {"status": "owner"}
        if req["status"] == "cancelled":
            return {"status": "cancelled", "already_cancelled": True}
        if req["status"] != "pending":
            return {"status": str(req["status"])}
        await conn.execute(
            "UPDATE pack_purchase_requests SET status='cancelled',processed_at=NOW() WHERE request_id=$1;", rid
        )
        return {"status": "cancelled"}

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


async def _open_pack_core(user_id: int, pack_key: str, client_request_id: str | None = None) -> dict[str, Any]:
    key = normalize_pack_key(pack_key)
    if not key:
        return {"status": "invalid"}
    p = PACK_CATALOG[key]
    request_key = str(client_request_id or "").strip()[:80] or None

    def _result_from_row(row: Any, *, replayed: bool = True) -> dict[str, Any]:
        data = dict(row)
        cards = data.get("cards")
        if isinstance(cards, str):
            try:
                cards = json.loads(cards)
            except (TypeError, ValueError):
                cards = []
        return {
            "status": "success", "opening_id": str(data.get("opening_id")),
            "pack_key": normalize_pack_key(data.get("pack_key")) or key,
            "cards": cards or [], "coins": int(data.get("coins") or 0),
            "rubies": int(data.get("rubies") or 0), "replayed": replayed,
        }

    if request_key:
        existing = await fetchrow(
            "SELECT opening_id,pack_key,cards,coins,rubies FROM pack_openings WHERE user_id=$1 AND client_request_id=$2;",
            int(user_id), request_key,
        )
        if existing:
            return _result_from_row(existing)

                                                                         
                                                                           
                                                                
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
        ovr = overall_rating(player.get("bat_level"), player.get("bowl_level"))
        cards.append({
            "kind": item["kind"], "player": player,
            "player_id": int(player.get("player_id") or 0),
            "ovr": ovr, "rarity": get_rarity(ovr),
        })

    opening_id = uuid.uuid4()

    async def _tx(conn):
                                                                                        
        if request_key:
            prior = await conn.fetchrow(
                "SELECT opening_id,pack_key,cards,coins,rubies FROM pack_openings WHERE user_id=$1 AND client_request_id=$2 FOR UPDATE;",
                int(user_id), request_key,
            )
            if prior:
                data = dict(prior)
                previous_cards = data.get("cards")
                if isinstance(previous_cards, str):
                    try: previous_cards = json.loads(previous_cards)
                    except (TypeError, ValueError): previous_cards = []
                return {"status": "success", "opening_id": str(data["opening_id"]),
                        "pack_key": normalize_pack_key(data.get("pack_key")) or key,
                        "cards": previous_cards or [], "coins": int(data.get("coins") or 0),
                        "rubies": int(data.get("rubies") or 0), "replayed": True}

        inv = await conn.fetchrow(
            "SELECT quantity FROM pack_inventory WHERE user_id=$1 AND pack_key=$2 FOR UPDATE;", int(user_id), key
        )
        quantity = int(inv["quantity"] or 0) if inv else 0
        if quantity <= 0:
            return {"status": "empty"}

                                                                           
                                                                                 
        if request_key:
            inserted = await conn.fetchrow(
                """
                INSERT INTO pack_openings(opening_id,user_id,pack_key,cards,coins,rubies,status,opened_at,client_request_id)
                VALUES($1,$2,$3,$4::jsonb,$5,$6,'opened',NOW(),$7)
                ON CONFLICT (user_id,client_request_id) WHERE client_request_id IS NOT NULL DO NOTHING
                RETURNING opening_id;
                """,
                opening_id, int(user_id), key, json.dumps(cards, default=str), int(p["coins"]), int(p["rubies"]), request_key,
            )
            if not inserted:
                prior = await conn.fetchrow(
                    "SELECT opening_id,pack_key,cards,coins,rubies FROM pack_openings WHERE user_id=$1 AND client_request_id=$2 FOR UPDATE;",
                    int(user_id), request_key,
                )
                if not prior:
                    return {"status": "failed"}
                data = dict(prior); prior_cards = data.get("cards")
                if isinstance(prior_cards, str):
                    try: prior_cards = json.loads(prior_cards)
                    except (TypeError, ValueError): prior_cards = []
                return {"status": "success", "opening_id": str(data["opening_id"]),
                        "pack_key": normalize_pack_key(data.get("pack_key")) or key,
                        "cards": prior_cards or [], "coins": int(data.get("coins") or 0),
                        "rubies": int(data.get("rubies") or 0), "replayed": True}
        else:
            await conn.execute(
                """
                INSERT INTO pack_openings(opening_id,user_id,pack_key,cards,coins,rubies,status,opened_at)
                VALUES($1,$2,$3,$4::jsonb,$5,$6,'opened',NOW());
                """,
                opening_id, int(user_id), key, json.dumps(cards, default=str), int(p["coins"]), int(p["rubies"]),
            )

        await conn.execute(
            "UPDATE pack_inventory SET quantity=quantity-1,updated_at=NOW() WHERE user_id=$1 AND pack_key=$2;",
            int(user_id), key,
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
        # The cards go straight into the player's SQUAD (team_squads). Only a card that cannot be added -
        # the player is already owned, or the squad is full - waits for the Release / Replace decision.
        await _settle_in_tx(conn, int(user_id))            # (also heals cards stranded by older versions)
        return {"status": "success", "opening_id": str(opening_id), "pack_key": key,
                "cards": cards, "coins": int(p["coins"]), "rubies": int(p["rubies"]), "replayed": False}

    return await transaction(_tx)


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
    await _attach_delivery(int(user_id), data)
    return data


# ---------------------------------------------------------------------------------------------------------------------
# Delivery of pack cards into the squad + Release & Replace
# ---------------------------------------------------------------------------------------------------------------------
def _parse_squad(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if isinstance(raw, list):
        return [dict(p) for p in raw if isinstance(p, dict)]
    return []


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    return value if isinstance(value, list) else []


def sell_value_of(player: dict[str, Any]) -> int:
    """What a player is worth when sold: the same chart /sell uses (45% of the buy price at his overall)."""
    ovr = overall_rating(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))
    return int(get_price(ovr)[1])


async def _full_snapshot(conn, player: dict[str, Any]) -> dict[str, Any]:
    """The squad snapshot of a pack player - the same shape /buy and /claim store (full database row)."""
    pid = int(player.get("player_id") or 0)
    if pid < 0:
        row = await conn.fetchrow("SELECT * FROM special_edition_players WHERE special_player_id=$1;", abs(pid))
        if row is not None:
            from database.special_players_repo import as_special_player
            return as_special_player(row)
        snap = dict(player)
        snap.update({"player_kind": "special", "is_special": True, "is_showcase": False, "special_edition_id": abs(pid)})
        return snap
    row = await conn.fetchrow("SELECT * FROM players WHERE player_id=$1;", pid)
    snap = dict(row) if row is not None else dict(player)
    snap.update({"player_kind": "global", "is_special": False, "is_showcase": False})
    return snap


def _card_player(row: Any) -> dict[str, Any] | None:
    cards = _as_list(row["cards"])
    position = int(row["position"] or 0)
    if position < 1 or position > len(cards) or not isinstance(cards[position - 1], dict):
        return None
    player = cards[position - 1].get("player")
    return dict(player) if isinstance(player, dict) else None


async def _write_squad(conn, user_id: int, squad: list[dict[str, Any]]) -> None:
    await conn.execute(
        "INSERT INTO team_squads (user_id, squad, updated_at) VALUES ($1, $2::jsonb, NOW()) "
        "ON CONFLICT (user_id) DO UPDATE SET squad = EXCLUDED.squad, updated_at = NOW();",
        int(user_id), json.dumps(squad, default=str),
    )


async def _settle_in_tx(conn, user_id: int, opening_id: Any = None) -> list[dict[str, Any]]:
    """Move pending pack cards into the squad (inside the caller's transaction).

    A card is added when the player is not owned yet and the squad has room. Anything else stays 'pending' and
    waits for the owner's Release / Replace choice. Also heals cards that older versions left in the collection
    table instead of the squad."""
    args: list[Any] = [int(user_id)]
    where = ""
    if opening_id is not None:
        args.append(opening_id if not isinstance(opening_id, str) else uuid.UUID(opening_id))
        where = " AND pci.opening_id=$2"
    rows = await conn.fetch(
        f"""
        SELECT pci.card_id, pci.opening_id, pci.position, po.cards
        FROM pack_card_inventory pci JOIN pack_openings po ON po.opening_id = pci.opening_id
        WHERE pci.user_id=$1 AND pci.status='pending'{where}
        ORDER BY po.opened_at, pci.position
        FOR UPDATE OF pci;
        """, *args)
    if not rows:
        return []
    squad_row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(user_id))
    squad = _parse_squad(squad_row["squad"]) if squad_row else []
    changed = False
    outcome: list[dict[str, Any]] = []
    for row in rows:
        player = _card_player(row)
        if player is None:
            continue
        snap = await _full_snapshot(conn, player)
        owned = any(owned_same_card(p, snap) for p in squad)
        if not owned and len(squad) < MAX_SQUAD_SIZE:
            squad.append(snap)
            changed = True
            await conn.execute("UPDATE pack_card_inventory SET status='added', resolved_at=NOW() WHERE card_id=$1;", int(row["card_id"]))
            await conn.execute("DELETE FROM player_user_match_stats WHERE user_id=$1 AND player_id=$2;", int(user_id), int(snap.get("player_id") or 0))
            outcome.append({"card_id": int(row["card_id"]), "status": "added"})
        else:
            outcome.append({"card_id": int(row["card_id"]), "status": "pending", "reason": "duplicate" if owned else "full"})
    if changed:
        await _write_squad(conn, int(user_id), squad)
    return outcome


async def _attach_delivery(user_id: int, data: dict[str, Any]) -> None:
    """Add card_id / status / reason / sell value (and the paid amount once decided) to each card of an opening."""
    try:
        oid = uuid.UUID(str(data.get("opening_id")))
        rows = await fetch(
            "SELECT card_id, position, status, resolution_coins FROM pack_card_inventory WHERE opening_id=$1 AND user_id=$2;",
            oid, int(user_id))
        squad_row = await fetchrow("SELECT squad FROM team_squads WHERE user_id=$1;", int(user_id))
    except Exception as exc:
        print(f"[packs] delivery lookup failed: {exc!r}")
        return
    squad = _parse_squad(squad_row["squad"]) if squad_row else []
    by_pos = {int(r["position"]): r for r in rows}
    for pos, card in enumerate(data.get("cards") or [], start=1):
        if not isinstance(card, dict):
            continue
        card["sell_value"] = sell_value_of(card.get("player") or {})
        row = by_pos.get(pos)
        if row is None:
            continue
        card["card_id"] = int(row["card_id"])
        card["status"] = str(row["status"] or "pending")
        card["resolution_coins"] = int(row["resolution_coins"] or 0)
        if card["status"] == "pending":
            pl = dict(card.get("player") or {})
            owned = any(owned_same_card(p, {**pl, "player_kind": _card_kind(pl)}) for p in squad)
            card["reason"] = "duplicate" if owned else "full"
        card["can_replace"] = card.get("status") == "pending" and card.get("reason") == "full"


async def open_pack(user_id: int, pack_key: str, client_request_id: str | None = None) -> dict[str, Any]:
    result = await _open_pack_core(user_id, pack_key, client_request_id)
    if result.get("status") == "success":
        await _attach_delivery(int(user_id), result)
    return result


async def pending_cards(user_id: int) -> list[dict[str, Any]]:
    """Cards still waiting for Release / Replace (settles first, so anything that fits is added to the squad)."""
    async def _tx(conn):
        await _settle_in_tx(conn, int(user_id))
        return await conn.fetch(
            """
            SELECT pci.card_id, pci.opening_id, pci.position, po.cards, po.pack_key
            FROM pack_card_inventory pci JOIN pack_openings po ON po.opening_id = pci.opening_id
            WHERE pci.user_id=$1 AND pci.status='pending' ORDER BY po.opened_at DESC, pci.position;
            """, int(user_id))
    rows = await transaction(_tx)
    squad_row = await fetchrow("SELECT squad FROM team_squads WHERE user_id=$1;", int(user_id))
    squad = _parse_squad(squad_row["squad"]) if squad_row else []
    out = []
    for row in rows:
        player = _card_player(row)
        if player is None:
            continue
        ovr = overall_rating(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))
        owned = any(owned_same_card(p, {**player, "player_kind": _card_kind(player)}) for p in squad)
        out.append({"card_id": int(row["card_id"]), "opening_id": str(row["opening_id"]), "position": int(row["position"]),
                    "pack_key": str(row["pack_key"] or ""), "player": player, "ovr": ovr, "rarity": get_rarity(ovr),
                    "sell_value": sell_value_of(player), "reason": "duplicate" if owned else "full",
                    "can_replace": not owned})
    return out


async def squad_choices(user_id: int) -> list[dict[str, Any]]:
    row = await fetchrow("SELECT squad FROM team_squads WHERE user_id=$1;", int(user_id))
    out = []
    for p in _parse_squad(row["squad"]) if row else []:
        ovr = overall_rating(int(p.get("bat_level") or 0), int(p.get("bowl_level") or 0))
        out.append({"player_id": int(p.get("player_id") or 0), "name": str(p.get("name") or "Player"),
                    "edition": p.get("edition"), "role": p.get("role"), "country": p.get("country"),
                    "bat_level": int(p.get("bat_level") or 0), "bowl_level": int(p.get("bowl_level") or 0),
                    "player_kind": _card_kind(p), "ovr": ovr, "sell_value": sell_value_of(p)})
    out.sort(key=lambda x: (x["ovr"], x["name"]))
    return out


async def _lock_pending_card(conn, user_id: int, card_id: int):
    return await conn.fetchrow(
        """
        SELECT pci.card_id, pci.opening_id, pci.position, pci.status, po.cards, po.pack_key
        FROM pack_card_inventory pci JOIN pack_openings po ON po.opening_id = pci.opening_id
        WHERE pci.card_id=$1 AND pci.user_id=$2 FOR UPDATE OF pci;
        """, int(card_id), int(user_id))


async def release_card(user_id: int, card_id: int) -> dict[str, Any]:
    """Release: the pack player is not kept; the owner is paid his sell value in coins (one transaction, once)."""
    async def _tx(conn):
        user = await conn.fetchrow("SELECT balance FROM users WHERE user_id=$1 FOR UPDATE;", int(user_id))
        if user is None:
            return {"status": "user_missing"}
        row = await _lock_pending_card(conn, user_id, card_id)
        if row is None:
            return {"status": "not_found"}
        if row["status"] != "pending":
            return {"status": "already_resolved", "resolution": str(row["status"]), "opening_id": str(row["opening_id"]),
                    "position": int(row["position"])}
        player = _card_player(row)
        if player is None:
            return {"status": "card_data_unavailable"}
        snap = await _full_snapshot(conn, player)
        coins = sell_value_of(snap)
        await conn.execute("UPDATE users SET balance=COALESCE(balance,0)+$1, last_seen_at=NOW() WHERE user_id=$2;", coins, int(user_id))
        await conn.execute("UPDATE pack_card_inventory SET status='released', resolved_at=NOW(), resolution_coins=$2 WHERE card_id=$1;",
                           int(card_id), coins)
        return {"status": "success", "action": "release", "coins": coins, "balance": int(user["balance"] or 0) + coins,
                "player": player, "opening_id": str(row["opening_id"]), "position": int(row["position"])}
    return await transaction(_tx)


async def replace_card(user_id: int, card_id: int, replace_player_id: int) -> dict[str, Any]:
    """Replace: the chosen squad player is sold (coins) and the pack player takes his place in the squad."""
    async def _tx(conn):
        user = await conn.fetchrow("SELECT balance FROM users WHERE user_id=$1 FOR UPDATE;", int(user_id))
        if user is None:
            return {"status": "user_missing"}
        row = await _lock_pending_card(conn, user_id, card_id)
        if row is None:
            return {"status": "not_found"}
        if row["status"] != "pending":
            return {"status": "already_resolved", "resolution": str(row["status"]), "opening_id": str(row["opening_id"]),
                    "position": int(row["position"])}
        player = _card_player(row)
        if player is None:
            return {"status": "card_data_unavailable"}
        squad_row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(user_id))
        squad = _parse_squad(squad_row["squad"]) if squad_row else []
        idx = next((i for i, p in enumerate(squad) if int(p.get("player_id") or 0) == int(replace_player_id)), None)
        if idx is None:
            return {"status": "target_missing"}
        snap = await _full_snapshot(conn, player)
        if any(owned_same_card(p, snap) for p in squad):
            return {"status": "duplicate_card"}          # a squad never holds the same card twice - release it instead
        old = squad[idx]
        coins = sell_value_of(old)
        squad[idx] = snap
        await _write_squad(conn, int(user_id), squad)
        await conn.execute("UPDATE users SET balance=COALESCE(balance,0)+$1, last_seen_at=NOW() WHERE user_id=$2;", coins, int(user_id))
        await conn.execute(
            "UPDATE pack_card_inventory SET status='replaced', resolved_at=NOW(), resolution_coins=$2, replaced_player_id=$3 WHERE card_id=$1;",
            int(card_id), coins, int(replace_player_id))
        for stmt in ("DELETE FROM user_player_loadouts WHERE user_id=$1 AND player_id=$2;",
                     "DELETE FROM player_user_match_stats WHERE user_id=$1 AND player_id=$2;"):
            try:
                async with conn.transaction():
                    await conn.execute(stmt, int(user_id), int(replace_player_id))
            except Exception:
                pass
        try:
            async with conn.transaction():
                await conn.execute("DELETE FROM player_user_match_stats WHERE user_id=$1 AND player_id=$2;", int(user_id), int(snap.get("player_id") or 0))
        except Exception:
            pass
        return {"status": "success", "action": "replace", "coins": coins, "balance": int(user["balance"] or 0) + coins,
                "player": player, "replaced": {"player_id": int(old.get("player_id") or 0), "name": str(old.get("name") or "Player"),
                                               "edition": old.get("edition")},
                "opening_id": str(row["opening_id"]), "position": int(row["position"])}
    return await transaction(_tx)
