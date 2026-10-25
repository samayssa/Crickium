"""Auction storage + the atomic operations the live auction is built from.

The auction is a *database driven* state machine: the lot row holds the current
bid, the leading team and the deadline; ``place_bid`` and ``settle`` are single
transactions that lock the lot row. That is what lets the Telegram bot (button /
``/bid``), the Mini App (bid button) and the background worker all act on the
same lot without ever double-selling a player or accepting a bid after the hammer.

The deadline always comes from the database clock (``NOW()``), never from the
Python clock of whichever process happens to handle the request.
"""
from __future__ import annotations

import json
from typing import Any

from database.query import execute, fetch, fetchrow, fetchval, transaction
from database.tournament_repo import TourError, _row, _rows  # noqa: F401  (re-exported helpers)
from services import tournament_core as tc


# ---------------------------------------------------------------------------
# auction row
# ---------------------------------------------------------------------------
async def get_auction(tid: int) -> dict | None:
    return _row(await fetchrow("SELECT * FROM tour_auction WHERE tid=$1;", int(tid)))


async def init_auction(tid: int, lots: list[dict[str, Any]]) -> None:
    """Create the auction row and every lot of round 1 (replaces a previous, never-started setup)."""

    async def _tx(conn):
        await conn.execute("DELETE FROM tour_lots WHERE tid=$1;", int(tid))
        await conn.execute("DELETE FROM tour_bids WHERE tid=$1;", int(tid))
        await conn.execute("DELETE FROM tour_squad WHERE tid=$1;", int(tid))
        for lot in lots:
            await conn.execute(
                """
                INSERT INTO tour_lots(tid, ord, round, set_idx, set_code, set_name, pool_name, set_pos, set_size,
                    pkey, name, role, country, bat_level, bowl_level, batting_hand, bowling_hand, overseas, card, base_lakh)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19::jsonb,$20);
                """,
                int(tid), int(lot["ord"]), int(lot.get("round", 1)), int(lot["set_idx"]), lot["set_code"], lot["set_name"],
                lot["pool_name"], int(lot["set_pos"]), int(lot["set_size"]), lot["pkey"], lot["name"], lot["role"],
                lot.get("country"), int(lot.get("bat_level") or 0), int(lot.get("bowl_level") or 0),
                lot.get("batting_hand"), lot.get("bowling_hand"), bool(lot.get("overseas")),
                json.dumps(lot.get("card") or {}, default=str), int(lot["base_lakh"]),
            )
        await conn.execute(
            """
            INSERT INTO tour_auction(tid, status, total_lots, round, version) VALUES($1,'idle',$2,1,1)
            ON CONFLICT (tid) DO UPDATE SET status='idle', total_lots=EXCLUDED.total_lots, round=1, cur_ord=NULL,
                version=tour_auction.version+1, accel_done=FALSE, pinned_msg_id=NULL, next_lot_at=NULL,
                paused_left_ms=NULL, started_at=NULL, ended_at=NULL;
            """,
            int(tid), len(lots))

    await transaction(_tx)


async def start_auction(tid: int, first_delay_s: int = 60) -> dict | None:
    return _row(await fetchrow(
        """
        UPDATE tour_auction SET status='running', started_at=NOW(), next_lot_at=NOW() + ($2::int * INTERVAL '1 second'),
               version=version+1
        WHERE tid=$1 AND status='idle' RETURNING *;
        """,
        int(tid), int(first_delay_s)))


async def finish_auction(tid: int) -> dict | None:
    return _row(await fetchrow(
        "UPDATE tour_auction SET status='done', ended_at=NOW(), cur_ord=NULL, next_lot_at=NULL, version=version+1 "
        "WHERE tid=$1 AND status<>'done' RETURNING *;", int(tid)))


async def running_auctions() -> list[dict]:
    return _rows(await fetch("SELECT * FROM tour_auction WHERE status IN ('running','paused') ORDER BY tid;"))


async def pause(tid: int) -> dict | None:
    async def _tx(conn):
        auction = await conn.fetchrow("SELECT * FROM tour_auction WHERE tid=$1 FOR UPDATE;", int(tid))
        if auction is None or auction["status"] != "running":
            return None
        left = 0
        if auction["cur_ord"] is not None:
            ms = await conn.fetchval(
                "SELECT (EXTRACT(EPOCH FROM (deadline_at - NOW())) * 1000)::bigint FROM tour_lots WHERE tid=$1 AND ord=$2 AND status='live';",
                int(tid), int(auction["cur_ord"]))
            left = max(0, int(ms or 0))
        row = await conn.fetchrow(
            "UPDATE tour_auction SET status='paused', paused_left_ms=$2, version=version+1 WHERE tid=$1 RETURNING *;",
            int(tid), left)
        return row

    return _row(await transaction(_tx))


async def resume(tid: int, min_left_ms: int = 8000) -> dict | None:
    async def _tx(conn):
        auction = await conn.fetchrow("SELECT * FROM tour_auction WHERE tid=$1 FOR UPDATE;", int(tid))
        if auction is None or auction["status"] != "paused":
            return None
        left = max(int(auction["paused_left_ms"] or 0), int(min_left_ms))
        if auction["cur_ord"] is not None:
            await conn.execute(
                "UPDATE tour_lots SET deadline_at = NOW() + ($3::bigint * INTERVAL '1 millisecond') WHERE tid=$1 AND ord=$2 AND status='live';",
                int(tid), int(auction["cur_ord"]), left)
        row = await conn.fetchrow(
            """
            UPDATE tour_auction SET status='running', paused_left_ms=NULL, version=version+1,
                   next_lot_at = CASE WHEN cur_ord IS NULL THEN NOW() + INTERVAL '3 second' ELSE next_lot_at END
            WHERE tid=$1 RETURNING *;
            """,
            int(tid))
        return row

    return _row(await transaction(_tx))


async def request_end(tid: int) -> dict | None:
    """Host / owner pressed End: bidding stops at once (status 'ending'); the auctioneer sells or
    closes the live lot, closes the rest, tops up the squads and finishes."""
    return _row(await fetchrow(
        "UPDATE tour_auction SET status='ending', paused_left_ms=NULL, version=version+1 "
        "WHERE tid=$1 AND status IN ('running','paused') RETURNING *;", int(tid)))


async def set_pinned(tid: int, message_id: int | None, failed: bool | None = None) -> None:
    if failed is None:
        await execute("UPDATE tour_auction SET pinned_msg_id=$2 WHERE tid=$1;", int(tid), message_id)
    else:
        await execute("UPDATE tour_auction SET pinned_msg_id=$2, pin_failed=$3 WHERE tid=$1;", int(tid), message_id, bool(failed))


async def bump_version(tid: int) -> None:
    await execute("UPDATE tour_auction SET version=version+1 WHERE tid=$1;", int(tid))


# ---------------------------------------------------------------------------
# lots
# ---------------------------------------------------------------------------
async def lot(tid: int, ord_: int) -> dict | None:
    return _row(await fetchrow("SELECT * FROM tour_lots WHERE tid=$1 AND ord=$2;", int(tid), int(ord_)))


async def live_lot(tid: int) -> dict | None:
    return _row(await fetchrow("SELECT * FROM tour_lots WHERE tid=$1 AND status='live' ORDER BY ord LIMIT 1;", int(tid)))


async def next_pending(tid: int, round_no: int | None = None) -> dict | None:
    if round_no is None:
        return _row(await fetchrow("SELECT * FROM tour_lots WHERE tid=$1 AND status='pending' ORDER BY ord LIMIT 1;", int(tid)))
    return _row(await fetchrow(
        "SELECT * FROM tour_lots WHERE tid=$1 AND status='pending' AND round=$2 ORDER BY ord LIMIT 1;", int(tid), int(round_no)))


async def begin_lot(tid: int, ord_: int, bid_seconds: int | None = None) -> dict | None:
    bid_seconds = int(bid_seconds or tc.BID_SECONDS)

    async def _tx(conn):
        row = await conn.fetchrow(
            """
            UPDATE tour_lots SET status='live', started_at=NOW(), deadline_at = NOW() + ($3::int * INTERVAL '1 second'),
                   cur_bid_lakh=0, bid_count=0, leader_team_no=NULL, said_once=FALSE, said_twice=FALSE
            WHERE tid=$1 AND ord=$2 AND status='pending' RETURNING *;
            """,
            int(tid), int(ord_), int(bid_seconds))
        if row is None:
            return None
        await conn.execute("UPDATE tour_auction SET cur_ord=$2, next_lot_at=NULL, version=version+1 WHERE tid=$1;", int(tid), int(ord_))
        return row

    return _row(await transaction(_tx))


async def close_unsold(tid: int, ord_: int, between_s: int | None = None) -> dict | None:
    """Mark a pending/live lot unsold without a sale (nobody could bid / skipped by the host)."""
    between_s = tc.BETWEEN_LOTS_SECONDS if between_s is None else int(between_s)

    async def _tx(conn):
        row = await conn.fetchrow(
            "UPDATE tour_lots SET status='unsold', ended_at=NOW() WHERE tid=$1 AND ord=$2 AND status IN ('pending','live') RETURNING *;",
            int(tid), int(ord_))
        if row is None:
            return None
        await conn.execute(
            "UPDATE tour_auction SET cur_ord=NULL, next_lot_at=NOW() + ($2::int * INTERVAL '1 second'), version=version+1 WHERE tid=$1;",
            int(tid), int(between_s))
        return row

    return _row(await transaction(_tx))


async def set_lot_messages(tid: int, ord_: int, a_msg_id: int | None = None, b_msg_id: int | None = None) -> None:
    if a_msg_id is not None:
        await execute("UPDATE tour_lots SET a_msg_id=$3 WHERE tid=$1 AND ord=$2;", int(tid), int(ord_), int(a_msg_id))
    if b_msg_id is not None:
        await execute("UPDATE tour_lots SET b_msg_id=$3 WHERE tid=$1 AND ord=$2;", int(tid), int(ord_), int(b_msg_id))


async def mark_said(tid: int, ord_: int, which: str) -> bool:
    col = "said_once" if which == "once" else "said_twice"
    row = await fetchrow(
        f"UPDATE tour_lots SET {col}=TRUE WHERE tid=$1 AND ord=$2 AND {col}=FALSE AND status='live' RETURNING ord;",
        int(tid), int(ord_))
    return row is not None


async def counts(tid: int) -> dict[str, int]:
    rows = await fetch("SELECT status, COUNT(*) AS n FROM tour_lots WHERE tid=$1 GROUP BY status;", int(tid))
    out = {"pending": 0, "live": 0, "sold": 0, "unsold": 0}
    for r in rows:
        out[str(r["status"])] = int(r["n"])
    return out


async def upcoming(tid: int, limit: int = 8) -> list[dict]:
    return _rows(await fetch(
        "SELECT ord, set_code, set_name, pool_name, name, role, base_lakh, overseas FROM tour_lots "
        "WHERE tid=$1 AND status='pending' ORDER BY ord LIMIT $2;", int(tid), int(limit)))


async def recent_sales(tid: int, limit: int = 12) -> list[dict]:
    return _rows(await fetch(
        """
        SELECT l.ord, l.name, l.role, l.status, l.base_lakh, l.sold_lakh, l.sold_team_no, l.ended_at, t.name AS team_name, t.base_code
        FROM tour_lots l LEFT JOIN tour_teams t ON t.tid = l.tid AND t.team_no = l.sold_team_no
        WHERE l.tid=$1 AND l.status IN ('sold','unsold') ORDER BY l.ended_at DESC NULLS LAST, l.ord DESC LIMIT $2;
        """,
        int(tid), int(limit)))


async def set_progress(tid: int) -> list[dict]:
    return [dict(r) for r in await fetch(
        """
        SELECT set_idx, MAX(set_code) AS set_code, MAX(set_name) AS set_name, MAX(pool_name) AS pool_name, MAX(round) AS round,
               COUNT(*) AS total, SUM(CASE WHEN status IN ('sold','unsold') THEN 1 ELSE 0 END) AS done
        FROM tour_lots WHERE tid=$1 GROUP BY set_idx ORDER BY MIN(ord);
        """,
        int(tid))]


async def bids(tid: int, ord_: int, limit: int = 6) -> list[dict]:
    return _rows(await fetch(
        """
        SELECT b.bid_no, b.team_no, b.amount_lakh, b.at, t.name AS team_name, t.base_code, t.short, t.owner_name
        FROM tour_bids b LEFT JOIN tour_teams t ON t.tid=b.tid AND t.team_no=b.team_no
        WHERE b.tid=$1 AND b.ord=$2 ORDER BY b.bid_no DESC LIMIT $3;
        """,
        int(tid), int(ord_), int(limit)))


async def unsold_players(tid: int) -> list[dict]:
    """Players whose latest lot ended unsold and who never found a buyer."""
    return _rows(await fetch(
        """
        SELECT l.* FROM tour_lots l
        WHERE l.tid=$1 AND l.status='unsold'
          AND NOT EXISTS (SELECT 1 FROM tour_lots s WHERE s.tid=l.tid AND s.pkey=l.pkey AND s.status='sold')
          AND l.ord = (SELECT MAX(x.ord) FROM tour_lots x WHERE x.tid=l.tid AND x.pkey=l.pkey)
        ORDER BY l.ord;
        """,
        int(tid)))


async def add_round(tid: int, round_no: int, lots: list[dict[str, Any]]) -> int:
    """Accelerated round: fresh lots (new ord numbers) for players who went unsold."""

    async def _tx(conn):
        base = int(await conn.fetchval("SELECT COALESCE(MAX(ord), 0) FROM tour_lots WHERE tid=$1;", int(tid)))
        set_base = int(await conn.fetchval("SELECT COALESCE(MAX(set_idx), -1) + 1 FROM tour_lots WHERE tid=$1;", int(tid)))
        for index, lot in enumerate(lots, start=1):
            await conn.execute(
                """
                INSERT INTO tour_lots(tid, ord, round, set_idx, set_code, set_name, pool_name, set_pos, set_size,
                    pkey, name, role, country, bat_level, bowl_level, batting_hand, bowling_hand, overseas, card, base_lakh)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19::jsonb,$20);
                """,
                int(tid), base + index, int(round_no), set_base + int(lot["set_idx"]), lot["set_code"], lot["set_name"],
                lot["pool_name"], int(lot["set_pos"]), int(lot["set_size"]), lot["pkey"], lot["name"], lot["role"],
                lot.get("country"), int(lot.get("bat_level") or 0), int(lot.get("bowl_level") or 0),
                lot.get("batting_hand"), lot.get("bowling_hand"), bool(lot.get("overseas")),
                json.dumps(lot.get("card") or {}, default=str), int(lot["base_lakh"]),
            )
        await conn.execute(
            "UPDATE tour_auction SET round=$2, accel_done=TRUE, total_lots=total_lots+$3, version=version+1 WHERE tid=$1;",
            int(tid), int(round_no), len(lots))
        return len(lots)

    return int(await transaction(_tx))


# ---------------------------------------------------------------------------
# bidding + hammer
# ---------------------------------------------------------------------------
async def place_bid(tid: int, ord_: int, team_no: int, user_id: int | None, expected_lakh: int | None = None,
                    bid_seconds: int | None = None) -> dict[str, Any]:
    """Place the next legal bid for ``team_no``.

    ``expected_lakh`` is the price the bidder *saw on their button*; if somebody
    bid in between, the call is refused (reason 'moved') instead of silently
    paying a higher price.
    Returns {"ok": True, ...} or {"ok": False, "reason": ...}.
    """
    bid_seconds = int(bid_seconds or tc.BID_SECONDS)

    async def _tx(conn):
        lot = await conn.fetchrow("SELECT * FROM tour_lots WHERE tid=$1 AND ord=$2 FOR UPDATE;", int(tid), int(ord_))
        if lot is None or lot["status"] != "live":
            return {"ok": False, "reason": "closed"}
        auction = await conn.fetchrow("SELECT status FROM tour_auction WHERE tid=$1;", int(tid))
        if auction is None or auction["status"] == "paused":
            return {"ok": False, "reason": "paused"}
        if auction["status"] != "running":
            return {"ok": False, "reason": "closed"}
        now = await conn.fetchval("SELECT NOW();")
        if lot["deadline_at"] is not None and lot["deadline_at"] <= now:
            return {"ok": False, "reason": "closed"}
        team = await conn.fetchrow("SELECT * FROM tour_teams WHERE tid=$1 AND team_no=$2;", int(tid), int(team_no))
        if team is None:
            return {"ok": False, "reason": "no_team"}
        if lot["leader_team_no"] is not None and int(lot["leader_team_no"]) == int(team_no):
            return {"ok": False, "reason": "leading", "price": int(lot["cur_bid_lakh"])}
        tour = await conn.fetchrow(
            "SELECT ladder, squad_min, squad_max, overseas_squad_max, min_base_lakh FROM tour_tournaments WHERE tid=$1;", int(tid))
        ladder = tc.normalize_ladder(json.loads(tour["ladder"]) if isinstance(tour["ladder"], str) else tour["ladder"])
        price = tc.next_bid(int(lot["cur_bid_lakh"]), int(lot["base_lakh"]), int(lot["bid_count"]) > 0, ladder)
        if expected_lakh is not None and int(expected_lakh) != price:
            return {"ok": False, "reason": "moved", "price": price}
        rules = {"squad_min": tour["squad_min"], "squad_max": tour["squad_max"],
                 "overseas_squad_max": tour["overseas_squad_max"], "min_base": tour["min_base_lakh"]}
        allowed, reason = tc.check_bid_allowed(dict(team), {"overseas": bool(lot["overseas"])}, price, rules)
        if not allowed:
            return {"ok": False, "reason": reason, "price": price,
                    "limit": tc.max_bid_for_team(int(team["purse_left"]), int(team["squad_count"]), int(tour["squad_min"]), int(tour["min_base_lakh"]))}
        updated = await conn.fetchrow(
            """
            UPDATE tour_lots SET cur_bid_lakh=$3, leader_team_no=$4, bid_count=bid_count+1,
                   deadline_at = NOW() + ($5::int * INTERVAL '1 second'), said_once=FALSE, said_twice=FALSE
            WHERE tid=$1 AND ord=$2 RETURNING *;
            """,
            int(tid), int(ord_), price, int(team_no), int(bid_seconds))
        await conn.execute(
            "INSERT INTO tour_bids(tid, ord, bid_no, team_no, user_id, amount_lakh) VALUES($1,$2,$3,$4,$5,$6);",
            int(tid), int(ord_), int(updated["bid_count"]), int(team_no), user_id, price)
        await conn.execute("UPDATE tour_auction SET version=version+1 WHERE tid=$1;", int(tid))
        return {"ok": True, "price": price, "lot": dict(updated), "team": dict(team)}

    out = await transaction(_tx)
    if out.get("lot"):
        out["lot"] = _row(out["lot"])
    if out.get("team"):
        out["team"] = _row(out["team"])
    return out


async def settle(tid: int, ord_: int, between_s: int | None = None, force: bool = False) -> dict[str, Any] | None:
    """The hammer. Acts on a running auction's live lot whose deadline has passed
    (``force``: the host ended the auction - sell/close the live lot right now)."""
    between_s = tc.BETWEEN_LOTS_SECONDS if between_s is None else int(between_s)

    async def _tx(conn):
        lot = await conn.fetchrow("SELECT * FROM tour_lots WHERE tid=$1 AND ord=$2 FOR UPDATE;", int(tid), int(ord_))
        if lot is None or lot["status"] != "live":
            return None
        auction = await conn.fetchrow("SELECT status FROM tour_auction WHERE tid=$1;", int(tid))
        allowed = {"running", "ending"} if force else {"running"}
        if auction is None or auction["status"] not in allowed:
            return None
        now = await conn.fetchval("SELECT NOW();")
        if not force and lot["deadline_at"] is not None and lot["deadline_at"] > now:
            return None
        if int(lot["bid_count"]) > 0 and lot["leader_team_no"] is not None:
            team_no, price = int(lot["leader_team_no"]), int(lot["cur_bid_lakh"])
            team = await conn.fetchrow("SELECT * FROM tour_teams WHERE tid=$1 AND team_no=$2 FOR UPDATE;", int(tid), team_no)
            sold = await conn.fetchrow(
                "UPDATE tour_lots SET status='sold', sold_team_no=$3, sold_lakh=$4, ended_at=NOW() WHERE tid=$1 AND ord=$2 RETURNING *;",
                int(tid), int(ord_), team_no, price)
            await conn.execute(
                """
                INSERT INTO tour_squad(tid, team_no, pkey, name, role, country, bat_level, bowl_level, batting_hand, bowling_hand,
                                       overseas, price_lakh, lot_ord, source, card)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,'auction',$14::jsonb)
                ON CONFLICT (tid, team_no, pkey) DO NOTHING;
                """,
                int(tid), team_no, lot["pkey"], lot["name"], lot["role"], lot["country"], int(lot["bat_level"]), int(lot["bowl_level"]),
                lot["batting_hand"], lot["bowling_hand"], bool(lot["overseas"]), price, int(ord_),
                lot["card"] if isinstance(lot["card"], str) else json.dumps(lot["card"], default=str))
            updated_team = await conn.fetchrow(
                """
                UPDATE tour_teams SET purse_left=purse_left-$3, spent=spent+$3, squad_count=squad_count+1,
                       overseas_count=overseas_count + $4
                WHERE tid=$1 AND team_no=$2 RETURNING *;
                """,
                int(tid), team_no, price, 1 if lot["overseas"] else 0)
            result = {"sold": True, "lot": dict(sold), "team": dict(updated_team)}
        else:
            unsold = await conn.fetchrow(
                "UPDATE tour_lots SET status='unsold', ended_at=NOW() WHERE tid=$1 AND ord=$2 RETURNING *;", int(tid), int(ord_))
            result = {"sold": False, "lot": dict(unsold), "team": None}
        await conn.execute(
            "UPDATE tour_auction SET cur_ord=NULL, next_lot_at=NOW() + ($2::int * INTERVAL '1 second'), version=version+1 WHERE tid=$1;",
            int(tid), int(between_s))
        return result

    out = await transaction(_tx)
    if out:
        out["lot"] = _row(out["lot"])
        out["team"] = _row(out["team"]) if out.get("team") else None
    return out


# ---------------------------------------------------------------------------
# worker helpers (quiet: they run every second, so they skip the per-query logging of database.query)
# ---------------------------------------------------------------------------
async def _quiet_fetchrow(query: str, *args):
    from database.connection import get_pool
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(query, *args)


async def _quiet_fetch(query: str, *args):
    from database.connection import get_pool
    async with get_pool().acquire() as conn:
        return await conn.fetch(query, *args)


async def tick_state(tid: int) -> dict | None:
    """Everything the auctioneer needs for one tick, in a single query."""
    row = await _quiet_fetchrow(
        """
        SELECT a.status AS a_status, a.cur_ord, a.round, a.total_lots, a.version, a.next_lot_at, a.accel_done,
               a.paused_left_ms, a.pinned_msg_id, a.pin_failed, NOW() AS db_now,
               l.status AS l_status, l.deadline_at, l.bid_count, l.cur_bid_lakh, l.leader_team_no,
               l.said_once, l.said_twice, l.base_lakh, l.shown, l.a_msg_id, l.b_msg_id
        FROM tour_auction a LEFT JOIN tour_lots l ON l.tid = a.tid AND l.ord = a.cur_ord
        WHERE a.tid = $1;
        """,
        int(tid))
    return dict(row) if row else None


async def lot_view(tid: int, ord_: int) -> dict | None:
    """A lot plus its leading team (for rendering the bidding message)."""
    row = await _quiet_fetchrow(
        """
        SELECT l.*, t.name AS leader_name, t.short AS leader_short, t.base_code AS leader_base,
               t.owner_name AS leader_owner, t.owner_id AS leader_owner_id
        FROM tour_lots l LEFT JOIN tour_teams t ON t.tid = l.tid AND t.team_no = l.leader_team_no
        WHERE l.tid = $1 AND l.ord = $2;
        """,
        int(tid), int(ord_))
    return _row(row)


async def recent_bids(tid: int, ord_: int, limit: int = 3) -> list[dict]:
    rows = await _quiet_fetch(
        """
        SELECT b.bid_no, b.team_no, b.amount_lakh, t.short, t.base_code, t.name AS team_name
        FROM tour_bids b LEFT JOIN tour_teams t ON t.tid = b.tid AND t.team_no = b.team_no
        WHERE b.tid = $1 AND b.ord = $2 ORDER BY b.bid_no DESC LIMIT $3;
        """,
        int(tid), int(ord_), int(limit))
    return [dict(r) for r in rows]


async def set_shown(tid: int, ord_: int, level: int) -> None:
    await execute("UPDATE tour_lots SET shown=GREATEST(shown, $3) WHERE tid=$1 AND ord=$2;", int(tid), int(ord_), int(level))


async def unfinished_lots(tid: int) -> list[dict]:
    """Settled lots whose result has not been shown in the group yet (oldest first)."""
    return _rows(await _quiet_fetch(
        "SELECT * FROM tour_lots WHERE tid=$1 AND status IN ('sold','unsold') AND shown < 2 ORDER BY ord LIMIT 20;", int(tid)))


async def close_all_pending(tid: int) -> int:
    """End of the auction (host pressed End): nobody gets the rest. Pending lots become unsold."""
    rows = await fetch(
        "UPDATE tour_lots SET status='unsold', ended_at=NOW(), shown=2 WHERE tid=$1 AND status='pending' RETURNING ord;", int(tid))
    return len(rows)


async def sale_summary(tid: int) -> dict[str, Any]:
    counts_ = await counts(tid)
    buys = _rows(await fetch(
        """
        SELECT s.name, s.price_lakh, t.short, t.base_code FROM tour_squad s
        JOIN tour_teams t ON t.tid = s.tid AND t.team_no = s.team_no
        WHERE s.tid = $1 AND s.source = 'auction' ORDER BY s.price_lakh DESC, s.name LIMIT 8;
        """,
        int(tid)))
    for b in buys:
        b["short"] = b.get("short") or b.get("base_code")
    return {"sold": counts_["sold"], "unsold": counts_["unsold"], "pending": counts_["pending"], "buys": buys}


# ---------------------------------------------------------------------------
# squads
# ---------------------------------------------------------------------------
async def squad(tid: int, team_no: int) -> list[dict]:
    return _rows(await fetch(
        "SELECT * FROM tour_squad WHERE tid=$1 AND team_no=$2 ORDER BY xi DESC, price_lakh DESC, name;", int(tid), int(team_no)))


async def all_squads(tid: int) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for row in _rows(await fetch("SELECT * FROM tour_squad WHERE tid=$1 ORDER BY team_no, xi DESC, price_lakh DESC, name;", int(tid))):
        out.setdefault(int(row["team_no"]), []).append(row)
    return out


async def add_squad_player(tid: int, team_no: int, player: dict[str, Any], price_lakh: int, source: str = "autofill") -> bool:
    """Add one player to a squad (auto-complete after the auction). Keeps the team counters in step."""

    async def _tx(conn):
        inserted = await conn.fetchrow(
            """
            INSERT INTO tour_squad(tid, team_no, pkey, name, role, country, bat_level, bowl_level, batting_hand, bowling_hand,
                                   overseas, price_lakh, lot_ord, source, card)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb)
            ON CONFLICT (tid, team_no, pkey) DO NOTHING RETURNING pkey;
            """,
            int(tid), int(team_no), player["pkey"], player["name"], player["role"], player.get("country"),
            int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0), player.get("batting_hand"),
            player.get("bowling_hand"), bool(player.get("overseas")), int(price_lakh), player.get("ord"), source,
            player["card"] if isinstance(player.get("card"), str) else json.dumps(player.get("card") or {}, default=str))
        if inserted is None:
            return False
        await conn.execute(
            """
            UPDATE tour_teams SET purse_left=GREATEST(purse_left-$3, 0), spent=spent+$3, squad_count=squad_count+1,
                   overseas_count=overseas_count+$4 WHERE tid=$1 AND team_no=$2;
            """,
            int(tid), int(team_no), int(price_lakh), 1 if player.get("overseas") else 0)
        if player.get("ord") is not None:
            await conn.execute("UPDATE tour_lots SET status='sold', sold_team_no=$3, sold_lakh=$4 WHERE tid=$1 AND ord=$2 AND status='unsold';",
                               int(tid), int(player["ord"]), int(team_no), int(price_lakh))
        return True

    return bool(await transaction(_tx))


async def set_xi(tid: int, team_no: int, pkeys: list[str]) -> None:
    async def _tx(conn):
        await conn.execute("UPDATE tour_squad SET xi=FALSE WHERE tid=$1 AND team_no=$2;", int(tid), int(team_no))
        if pkeys:
            await conn.execute("UPDATE tour_squad SET xi=TRUE WHERE tid=$1 AND team_no=$2 AND pkey = ANY($3::text[]);",
                               int(tid), int(team_no), list(pkeys))

    await transaction(_tx)


# ---------------------------------------------------------------------------
# virtual team rows for the match engines
# ---------------------------------------------------------------------------
async def materialize_engine_squads(tid: int, engine_key: str) -> int:
    """Copy every auctioned squad into playint_players under the team's virtual code, so the
    existing IPL / WPL / T20I match flows can read it exactly like a franchise squad."""
    rows = await fetch(
        """
        INSERT INTO playint_players(engine_key, team_code, team_name, name, country, role, bat_level, bowl_level, batting_hand, bowling_hand)
        SELECT $2, t.code, t.name, s.name, s.country, s.role, s.bat_level, s.bowl_level, s.batting_hand, s.bowling_hand
        FROM tour_squad s JOIN tour_teams t ON t.tid = s.tid AND t.team_no = s.team_no
        WHERE s.tid = $1
        ON CONFLICT (engine_key, team_code, name) DO NOTHING
        RETURNING player_id;
        """,
        int(tid), str(engine_key))
    return len(rows)


async def purge_engine_squads(engine_key: str, codes: list[str]) -> None:
    if codes:
        await execute("DELETE FROM playint_players WHERE engine_key=$1 AND team_code = ANY($2::text[]);", str(engine_key), list(codes))
