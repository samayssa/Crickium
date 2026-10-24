"""Headless checks for the giveaway core (no DB, no Telegram)."""
import asyncio, json, sys, types, collections

if "asyncpg" not in sys.modules:
    try:
        import asyncpg  # noqa
    except ImportError:
        sys.modules["asyncpg"] = types.ModuleType("asyncpg")

from services import giveaway as gw

# --- duration parsing
assert gw.parse_duration("90m") == 5400
assert gw.parse_duration("2h 30m") == 9000
assert gw.parse_duration("1d12h") == 129600
assert gw.parse_duration("now") == 0
assert gw.parse_duration("abc") is None and gw.parse_duration("") is None and gw.parse_duration("5 lightyears") is None
assert gw.fmt_duration(9000) == "2h 30m"

# --- scoring: more activity => higher weight, order favours active users
assert gw.score_from({}) == 0
hi = gw.score_from({"matches": 200, "wins": 100, "claims": 300})
lo = gw.score_from({"matches": 2})
assert hi > lo > 0 and gw.chance_weight(hi) > gw.chance_weight(lo) > 1
scores = {1: hi, 2: lo, 3: 0}
wins = collections.Counter(gw.weighted_order([1, 2, 3], scores)[0] for _ in range(4000))
assert wins[1] > wins[2] > wins[3] > 0, wins

# --- draw: skips ineligible, credits first eligible in ONE transaction, finishes giveaway
CARD = {"player_id": 7, "name": "Star", "country": "IN", "role": "Batsman", "bat_level": 95, "bowl_level": 40, "kind": "global"}
DB = {"status": "ending", "squads": {10: [], 11: [{"player_id": 7, "kind": "global"}], 12: []}}


class Conn:
    async def fetchval(self, sql, *a):
        if "FROM giveaways" in sql: return DB["status"]
        return 1
    async def fetchrow(self, sql, *a): return {"squad": json.dumps(DB["squads"][a[0]])}
    async def execute(self, sql, *a):
        if "UPDATE team_squads" in sql: DB["squads"][a[1]] = json.loads(a[0])
        if "status='ended'" in sql: DB["status"] = "ended"; DB["winner"] = a[1]


async def tx(cb): return await cb(Conn())
async def entries(gid): return [{"user_id": u, "username": f"u{u}", "first_name": f"U{u}"} for u in (10, 11, 12)]
async def resolve(card): return dict(CARD)
async def member(uid, use_cache=True): return uid != 12   # 12 left the group
async def scores_for(ids): return {10: 5.0, 11: 500.0, 12: 900.0}
async def get(gid): return {"giveaway_id": gid, "status": DB["status"], "winner_id": DB.get("winner")}
async def no_winner(gid, note, n): DB["status"] = "ended"; DB["note"] = note; return await get(gid)

gw.transaction, gw.resolve_live_card, gw.is_main_member, gw.scores_for = tx, resolve, member, scores_for
gw.repo.entries, gw.repo.get, gw.repo.mark_ended_without_winner = entries, get, no_winner

res = asyncio.run(gw.run_draw({"giveaway_id": 1, "player": CARD}))
assert res["status"] == "won", res
assert DB["winner"] == 10, DB                    # 11 owns it, 12 left -> only 10 eligible
assert len(DB["squads"][10]) == 1 and DB["squads"][10][0]["player_id"] == 7
assert DB["status"] == "ended"
# a second draw on a finished giveaway must not double-credit
DB["status"] = "ended"
res = asyncio.run(gw.run_draw({"giveaway_id": 1, "player": CARD}))
assert res["status"] == "stale" and len(DB["squads"][10]) == 1, res
# nobody eligible -> ended without winner
DB.update(status="ending", squads={10: [{"player_id": 7, "kind": "global"}], 11: [{"player_id": 7, "kind": "global"}], 12: []})
res = asyncio.run(gw.run_draw({"giveaway_id": 2, "player": CARD}))
assert res["status"] == "no_winner" and DB["status"] == "ended", res
print("giveaway tests OK")
