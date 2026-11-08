"""Server-side distribution and claiming for Crickium Mini App gifts.

A gift campaign is assigned once to every row in ``users``. Per-user random
cards are persisted before the campaign becomes visible, and claiming is one
transaction so wallet, pack inventory and squad changes cannot be duplicated.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any, Awaitable, Callable

from database import gifts_repo
from database.query import execute, fetch, fetchrow, transaction
from services.card_identity import owned_same_card

MAX_SQUAD_SIZE = 25


def json_value(value: Any, fallback: Any):
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return fallback
    return fallback


def card_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a global or special card row into the canonical squad shape."""
    player = dict(raw)
    player["overall"] = max(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))
    kind = str(player.get("player_kind") or ("special" if player.get("special_edition_id") else "global"))
    if kind == "special":
        sid = int(player.get("special_edition_id") or abs(int(player.get("player_id") or 0)))
        player.update({
            "player_id": -sid, "special_edition_id": sid, "player_kind": "special",
            "is_special": True, "is_showcase": False,
            "showcase_card_id": None, "showcase_set_id": None, "showcase_name": None,
        })
    else:
        player.update({
            "player_id": int(player.get("player_id") or 0), "player_kind": "global",
            "is_special": False, "is_showcase": False, "edition": None,
            "special_edition_id": None, "showcase_card_id": None,
            "showcase_set_id": None, "showcase_name": None,
        })
    return player


def _squad(raw: Any) -> list[dict[str, Any]]:
    value = json_value(raw, [])
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _candidate_for_user(candidates: list[dict[str, Any]], squad: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick a card randomly without allowing a large OVR bucket to dominate.

    First choose one of the OVR values that actually has an eligible card in
    this user's pool, then choose a card at that OVR. This makes the rating
    itself random across the selected band (for example 91, 92, 93, 94 or 95),
    rather than implicitly weighting 95 more heavily just because more 95 OVR
    cards happen to exist in the database. Already-owned cards remain excluded.
    """
    available = [
        candidate for candidate in candidates
        if not any(owned_same_card(item, candidate) for item in squad)
    ]
    if not available:
        return None

    by_overall: dict[int, list[dict[str, Any]]] = {}
    for candidate in available:
        overall = int(candidate.get("overall") or max(
            int(candidate.get("bat_level") or 0), int(candidate.get("bowl_level") or 0)
        ))
        by_overall.setdefault(overall, []).append(candidate)

    # secrets.choice is backed by the OS CSPRNG and is unbiased over the
    # available OVR buckets and over cards inside the chosen bucket.
    chosen_overall = secrets.choice(tuple(by_overall))
    return dict(secrets.choice(by_overall[chosen_overall]))


async def _player_pool(player_range: list[int] | tuple[int, int]) -> list[dict[str, Any]]:
    low, high = int(player_range[0]), int(player_range[1])
    global_rows = await fetch(
        """
        SELECT player_id,name,country,role,bat_level,bowl_level,batting_hand,bowling_hand,
               GREATEST(COALESCE(bat_level,0),COALESCE(bowl_level,0))::int AS overall,
               'global'::text AS player_kind,FALSE AS is_special
        FROM players
        WHERE GREATEST(COALESCE(bat_level,0),COALESCE(bowl_level,0)) BETWEEN $1 AND $2;
        """, low, high,
    )
    special_rows = await fetch(
        """
        SELECT (-special_player_id)::bigint AS player_id,special_player_id AS special_edition_id,
               name,country,role,bat_level,bowl_level,batting_hand,bowling_hand,
               GREATEST(COALESCE(bat_level,0),COALESCE(bowl_level,0))::int AS overall,
               edition,'special'::text AS player_kind,TRUE AS is_special
        FROM special_edition_players
        WHERE GREATEST(COALESCE(bat_level,0),COALESCE(bowl_level,0)) BETWEEN $1 AND $2;
        """, low, high,
    )
    return [card_snapshot(dict(row)) for row in [*global_rows, *special_rows]]


async def _mark_distributing(gift_id: int) -> dict[str, Any]:
    async def _tx(conn):
        row = await conn.fetchrow(
            "SELECT * FROM miniapp_gift_campaigns WHERE gift_id=$1 FOR UPDATE;", int(gift_id)
        )
        if not row:
            return {"status": "missing"}
        status = str(row["status"])
        if status == "distributed":
            return {"status": "distributed", "recipients": int(row["recipient_count"] or 0), "replayed": True}
        if status == "cancelled" or status == "draft":
            return {"status": "not_ready"}
        if status == "scheduled" and row["scheduled_at"]:
            scheduled = row["scheduled_at"]
            if scheduled.tzinfo is None:
                from datetime import timezone
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            from datetime import datetime, timezone
            if scheduled > datetime.now(timezone.utc):
                return {"status": "not_due"}
        await conn.execute(
            "UPDATE miniapp_gift_campaigns SET status='distributing' WHERE gift_id=$1;", int(gift_id)
        )
        return {"status": "distributing", "campaign": dict(row)}
    return await transaction(_tx)


_ACTIVE_DISTRIBUTIONS: set[int] = set()


async def distribute_campaign(gift_id: int, progress: Callable[[int, int], Awaitable[None]] | None = None) -> dict[str, Any]:
    """Assign and persist one reward per known bot user without messaging users.

    The local guard prevents a direct owner click and the scheduled worker from
    running the same long campaign concurrently in one bot process. A process
    restart clears the guard, allowing the worker to resume a campaign left in
    the durable ``distributing`` state.
    """
    gift_id = int(gift_id)
    if gift_id in _ACTIVE_DISTRIBUTIONS:
        return {"status": "in_progress"}
    _ACTIVE_DISTRIBUTIONS.add(gift_id)
    try:
        return await _distribute_campaign_locked(gift_id, progress)
    finally:
        _ACTIVE_DISTRIBUTIONS.discard(gift_id)


async def _distribute_campaign_locked(gift_id: int, progress: Callable[[int, int], Awaitable[None]] | None = None) -> dict[str, Any]:
    state = await _mark_distributing(int(gift_id))
    if state["status"] != "distributing":
        return state
    campaign = state["campaign"]
    config = json_value(campaign.get("reward_config"), {})
    card_range = config.get("player_range")
    fixed_player = config.get("player") if isinstance(config.get("player"), dict) else None
    candidates: list[dict[str, Any]] = []
    if fixed_player:
        candidates = [card_snapshot(fixed_player)]
    elif isinstance(card_range, (list, tuple)) and len(card_range) == 2:
        candidates = await _player_pool(card_range)
        if not candidates:
            await execute(
                "UPDATE miniapp_gift_campaigns SET status='ready',scheduled_at=NULL WHERE gift_id=$1 AND status='distributing';",
                int(gift_id),
            )
            return {"status": "player_pool_empty"}

    users = await fetch(
        """
        SELECT u.user_id,ts.squad
        FROM users u LEFT JOIN team_squads ts ON ts.user_id=u.user_id
        ORDER BY u.user_id;
        """
    )
    total = len(users)
    batch: list[tuple[int, int, str]] = []
    done = 0
    for row in users:
        squad = _squad(row.get("squad"))
        player = _candidate_for_user(candidates, squad) if candidates else None
        reward = {
            "coins": max(0, int(config.get("coins") or 0)),
            "rubies": max(0, int(config.get("rubies") or 0)),
            "pack_key": str(config.get("pack_key") or "").strip().lower() or None,
            "pack_quantity": 1 if config.get("pack_key") else 0,
            "player": player,
        }
        batch.append((int(campaign["gift_id"]), int(row["user_id"]), json.dumps(reward, ensure_ascii=False, default=str)))
        if len(batch) >= 250:
            await _save_batch(batch)
            done += len(batch)
            batch.clear()
            if progress:
                await progress(done, total)
    if batch:
        await _save_batch(batch)
        done += len(batch)
        if progress:
            await progress(done, total)

    count_row = await fetchrow(
        "SELECT COUNT(*) AS n FROM miniapp_gift_user_rewards WHERE gift_id=$1;", int(gift_id)
    )
    recipient_count = int((count_row["n"] if count_row else 0) or 0)
    await execute(
        "UPDATE miniapp_gift_campaigns SET status='distributed',step='distributed',recipient_count=$2,distributed_at=NOW() WHERE gift_id=$1 AND status='distributing';",
        int(gift_id), recipient_count,
    )
    return {"status": "distributed", "recipients": recipient_count, "replayed": False}


async def _save_batch(batch: list[tuple[int, int, str]]) -> None:
    from database.query import executemany
    await executemany(
        """
        INSERT INTO miniapp_gift_user_rewards(gift_id,user_id,reward,status)
        VALUES($1,$2,$3::jsonb,'pending')
        ON CONFLICT(gift_id,user_id) DO NOTHING;
        """, batch,
    )


async def open_gift(user_id: int, gift_id: int) -> dict[str, Any]:
    """Mark the user's assigned reward opened; retries return the same reward."""
    async def _tx(conn):
        row = await conn.fetchrow(
            """
            SELECT r.reward,r.status,c.gift_id,c.title,c.theme,c.image_file_id,c.status AS campaign_status
            FROM miniapp_gift_user_rewards r
            JOIN miniapp_gift_campaigns c ON c.gift_id=r.gift_id
            WHERE r.gift_id=$1 AND r.user_id=$2
            FOR UPDATE;
            """, int(gift_id), int(user_id),
        )
        if not row:
            return {"status": "not_found"}
        if row["campaign_status"] != "distributed":
            return {"status": "not_available"}
        if row["status"] == "claimed":
            return {"status": "claimed"}
        if row["status"] == "pending":
            await conn.execute(
                "UPDATE miniapp_gift_user_rewards SET status='opened',opened_at=NOW() WHERE gift_id=$1 AND user_id=$2;",
                int(gift_id), int(user_id),
            )
        return {
            "status": "success", "gift_id": int(row["gift_id"]),
            "title": str(row["title"] or "Your Gift"), "theme": str(row["theme"] or "normal"),
            "has_image": bool(row["image_file_id"]),
            "reward": json_value(row["reward"], {}),
        }
    return await transaction(_tx)


async def claim_gift(user_id: int, gift_id: int) -> dict[str, Any]:
    """Credit the saved reward once, using the existing wallet/squad/pack stores."""
    async def _tx(conn):
        row = await conn.fetchrow(
            """
            SELECT r.reward,r.status,c.theme,c.status AS campaign_status
            FROM miniapp_gift_user_rewards r
            JOIN miniapp_gift_campaigns c ON c.gift_id=r.gift_id
            WHERE r.gift_id=$1 AND r.user_id=$2
            FOR UPDATE;
            """, int(gift_id), int(user_id),
        )
        if not row:
            return {"status": "not_found"}
        if row["status"] == "claimed":
            wallet = await conn.fetchrow("SELECT balance,rubies FROM users WHERE user_id=$1;", int(user_id))
            return {"status": "success", "replayed": True,
                    "coins": int((wallet or {}).get("balance") or 0),
                    "rubies": int((wallet or {}).get("rubies") or 0)}
        if row.get("campaign_status") != "distributed":
            return {"status": "not_available"}
        if row["status"] != "opened":
            return {"status": "not_opened"}

        reward = json_value(row["reward"], {})
        coins = max(0, int(reward.get("coins") or 0))
        rubies = max(0, int(reward.get("rubies") or 0))
        pack_key = str(reward.get("pack_key") or "").strip().lower()
        player = reward.get("player") if isinstance(reward.get("player"), dict) else None

        user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id=$1 FOR UPDATE;", int(user_id))
        if not user:
            return {"status": "user_missing"}
        if player:
            squad_row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(user_id))
            squad = _squad(squad_row["squad"] if squad_row else None)
            if any(owned_same_card(item, player) for item in squad):
                return {"status": "already_owned"}
            if len(squad) >= MAX_SQUAD_SIZE:
                return {"status": "squad_full"}
            squad.append(card_snapshot(player))
            await conn.execute(
                """
                INSERT INTO team_squads(user_id,squad,updated_at)
                VALUES($1,$2::jsonb,NOW())
                ON CONFLICT(user_id) DO UPDATE SET squad=EXCLUDED.squad,updated_at=NOW();
                """, int(user_id), json.dumps(squad, ensure_ascii=False, default=str),
            )
            player_id = int(card_snapshot(player).get("player_id") or 0)
            await conn.execute(
                "DELETE FROM player_user_match_stats WHERE user_id=$1 AND player_id=$2;",
                int(user_id), player_id,
            )

        await conn.execute(
            "UPDATE users SET balance=COALESCE(balance,0)+$2,rubies=COALESCE(rubies,0)+$3,last_seen_at=NOW() WHERE user_id=$1;",
            int(user_id), coins, rubies,
        )
        if pack_key:
            from services.pack_system import normalize_pack_key
            key = normalize_pack_key(pack_key)
            if key:
                await conn.execute(
                    """
                    INSERT INTO pack_inventory(user_id,pack_key,quantity,updated_at)
                    VALUES($1,$2,1,NOW())
                    ON CONFLICT(user_id,pack_key)
                    DO UPDATE SET quantity=pack_inventory.quantity+1,updated_at=NOW();
                    """, int(user_id), key,
                )
        await conn.execute(
            "UPDATE miniapp_gift_user_rewards SET status='claimed',claimed_at=NOW() WHERE gift_id=$1 AND user_id=$2;",
            int(gift_id), int(user_id),
        )
        wallet = await conn.fetchrow("SELECT balance,rubies FROM users WHERE user_id=$1;", int(user_id))
        return {
            "status": "success", "replayed": False,
            "coins": int((wallet or {}).get("balance") or 0),
            "rubies": int((wallet or {}).get("rubies") or 0),
            "coins_added": coins, "rubies_added": rubies,
            "pack_key": pack_key or None, "player_added": bool(player),
        }
    return await transaction(_tx)


_WORKER: asyncio.Task | None = None
_WORKER_WAKE = asyncio.Event()


async def _worker_loop() -> None:
    while True:
        try:
            due = await gifts_repo.due_campaigns()
            for item in due:
                try:
                    result = await distribute_campaign(int(item["gift_id"]))
                    if result.get("status") not in {"distributed", "not_due"}:
                        print(f"[gifts] campaign {item['gift_id']} distribution status={result.get('status')}")
                except Exception as exc:
                    print(f"[gifts] campaign {item['gift_id']} distribution failed: {type(exc).__name__}")
        except Exception as exc:
            print(f"[gifts] scheduled worker query failed: {type(exc).__name__}")
        _WORKER_WAKE.clear()
        try:
            await asyncio.wait_for(_WORKER_WAKE.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass


def start_worker() -> None:
    global _WORKER
    if _WORKER is None or _WORKER.done():
        _WORKER = asyncio.create_task(_worker_loop(), name="crickium-gift-distribution-worker")


def wake_worker() -> None:
    _WORKER_WAKE.set()
