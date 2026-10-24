"""Headless checks for the tournament core (no DB, no Telegram).  Run: PYTHONPATH=. python3 tests/test_tournament_core.py"""
import random
import sys
import types
from datetime import date, datetime, timezone

if "asyncpg" not in sys.modules:
    try:
        import asyncpg  # noqa
    except ImportError:
        sys.modules["asyncpg"] = types.ModuleType("asyncpg")

from services import tournament_core as tc
from services import tournament_virtual as tv

# --- virtual team codes --------------------------------------------------------------
assert tv.make_code("csk", 12) == "CSK12"
assert tv.is_virtual("CSK12") and tv.is_virtual("upw7") and not tv.is_virtual("CSK") and not tv.is_virtual("T20I-IND")
assert tv.base_code("CSK12") == "CSK" and tv.base_code("csk") == "CSK" and tv.base_code("RCB301") == "RCB"
assert tv.tournament_of("MI9") == 9 and tv.tournament_of("MI") is None

# --- money -------------------------------------------------------------------------------
assert tc.fmt_money(5) == "₹5 L" and tc.fmt_money(75) == "₹75 L"
assert tc.fmt_money(100) == "₹1 Cr" and tc.fmt_money(125) == "₹1.25 Cr" and tc.fmt_money(110) == "₹1.1 Cr"
assert tc.fmt_money(12000) == "₹120 Cr" and tc.fmt_money(2075) == "₹20.75 Cr"
assert tc.parse_money("120") == 12000 and tc.parse_money("2.5cr") == 250 and tc.parse_money("75 lakh") == 75
assert tc.parse_money("₹5L") == 5 and tc.parse_money("1,5 crore") == 150 and tc.parse_money("abc") is None
assert tc.parse_money("120", default_unit="l") == 120

# --- the official IPL ladder ------------------------------------------------------------
L = tc.OFFICIAL_LADDER
assert [tc.step_for(p, L) for p in (20, 95, 100, 195, 200, 495, 500, 1200)] == [5, 5, 10, 10, 20, 20, 25, 25]
price, seen = 20, [20]
for _ in range(60):
    price = tc.next_bid(price, 20, True, L)
    seen.append(price)
assert seen[:4] == [20, 25, 30, 35] and 100 in seen and 110 in seen and 95 in seen and 200 in seen and 220 in seen
assert 105 not in seen and 205 not in seen
assert tc.next_bid(0, 50, False, L) == 50                  # opening bid = base price
assert tc.next_bid(500, 50, True, L) == 525 and tc.next_bid(525, 50, True, L) == 550
assert tc.parse_ladder_text("10") == ((None, 10),) and tc.parse_ladder_text("5 10 20 25") == L
assert tc.parse_ladder_text("5 10") is None and tc.parse_ladder_text("0") is None
assert tc.normalize_ladder([[100, 5], [None, 10]]) == ((100, 5), (None, 10))
assert tc.normalize_ladder([[300, 5], [100, 10], [None, 20]]) == L     # not increasing -> falls back to the official ladder
assert tc.ladder_rows(L)[0] == ("Up to ₹1 Cr", "+₹5 L") and tc.ladder_rows(L)[-1] == ("Above ₹5 Cr", "+₹25 L")

# --- squad / purse rules --------------------------------------------------------------
assert tc.max_bid_for_team(10000, 0, 18, 20) == 10000 - 17 * 20
assert tc.max_bid_for_team(300, 17, 18, 20) == 300            # last slot: whole purse allowed
assert tc.max_bid_for_team(300, 18, 18, 20) == 300
rules = {"squad_max": 25, "squad_min": 18, "overseas_squad_max": 8, "min_base": 20}
team = {"squad_count": 10, "overseas_count": 8, "purse_left": 5000}
assert tc.check_bid_allowed(team, {"overseas": True}, 100, rules) == (False, "overseas_full")
assert tc.check_bid_allowed(team, {"overseas": False}, 100, rules) == (True, "")
assert tc.check_bid_allowed({**team, "squad_count": 25}, {"overseas": False}, 100, rules) == (False, "squad_full")
assert tc.check_bid_allowed({**team, "purse_left": 150}, {"overseas": False}, 100, rules) == (False, "purse")


# --- pool + sets --------------------------------------------------------------------------
def make_pool(n, seed=1):
    r = random.Random(seed)
    roles = ["Batsman"] * 4 + ["Wicketkeeper"] * 1 + ["AllRounder"] * 3 + ["Bowler"] * 4
    hands = ["RAF", "LAM", "RAO", "LAL", "RAM"]
    out = []
    for i in range(n):
        role = roles[i % len(roles)]
        bat = r.randint(30, 99) if role != "Bowler" else r.randint(10, 40)
        bowl = r.randint(30, 99) if role in {"Bowler", "AllRounder"} else r.randint(5, 30)
        out.append({"name": f"Player {i}", "country": "India" if i % 3 else "Australia", "role": role, "bat_level": bat, "bowl_level": bowl,
                    "bowling_hand": hands[i % len(hands)]})
    return out


prof = tc.profile("PLAYIPL")
pool = make_pool(210)
sets = tc.build_auction_sets(pool, prof, random.Random(7))
flat = [lot for s in sets for lot in s["lots"]]
assert len(flat) == 210 and len({l["key"] for l in flat}) == 210, "every player exactly once"
assert sets[0]["pool"] == tc.POOL_MARQUEE and sets[0]["code"] == "M1"
assert {l["base_lakh"] for l in flat} <= {200, 150, 125, 100, 75, 50, 30, 20}
marquee = [l for s in sets if s["pool"] == tc.POOL_MARQUEE for l in s["lots"]]
rest = [l for s in sets if s["pool"] != tc.POOL_MARQUEE for l in s["lots"]]
assert min(m["base_lakh"] for m in marquee) >= 100, "marquee players carry big base prices"
assert sum(1 for l in flat if l["base_lakh"] == 200) <= 9
pools_in_order = [s["pool"] for s in sets]
assert pools_in_order == sorted(pools_in_order, key=[tc.POOL_MARQUEE, tc.POOL_CAPPED, tc.POOL_UNCAPPED].index)
sizes = [len(s["lots"]) for s in sets]
assert min(sizes) >= 3 and max(sizes) <= 12, sizes
# every call is a different running order, but never loses a player
orders = {tuple(l["key"] for s in tc.build_auction_sets(pool, prof, random.Random(seed)) for l in s["lots"]) for seed in range(6)}
assert len(orders) == 6
set_orders = {tuple(s["code"] for s in tc.build_auction_sets(pool, prof, random.Random(seed))) for seed in range(8)}
assert len(set_orders) > 1, "set order is shuffled between auctions"
# spinners and fast bowlers are split
codes = " ".join(s["code"] for s in sets)
assert "SP1" in codes and "FA1" in codes and "BA1" in codes and "AL1" in codes and "WK1" in codes
# duplicates by name collapse; a tiny pool still works (no marquee)
tiny = tc.build_auction_sets([{"name": "A", "role": "Batsman"}, {"name": "a", "role": "Bowler"}, {"name": "B", "role": "Bowler"}], prof, random.Random(1))
assert sum(len(s["lots"]) for s in tiny) == 2 and all(s["pool"] != tc.POOL_MARQUEE for s in tiny)
acc = tc.build_accelerated_sets(flat[:30], random.Random(3))
assert sum(len(s["lots"]) for s in acc) == 30 and acc[0]["pool"] == tc.POOL_ACCEL

# --- XI feasibility ----------------------------------------------------------------------
def mk(n_bat, n_wk, n_ar, n_bowl, overseas=0):
    out = []
    for role, n in (("Batsman", n_bat), ("Wicketkeeper", n_wk), ("AllRounder", n_ar), ("Bowler", n_bowl)):
        for i in range(n):
            out.append({"role": role, "country": "India"})
    for i in range(overseas):
        out[i]["country"] = "Australia"
    return out


assert tc.xi_feasible(mk(5, 2, 4, 6), 4)
assert not tc.xi_feasible(mk(5, 0, 4, 6), 4), "no keeper"
assert not tc.xi_feasible(mk(5, 2, 1, 6), 4), "one all-rounder"
assert not tc.xi_feasible(mk(5, 2, 4, 2), 4), "two bowlers"
assert not tc.xi_feasible(mk(3, 1, 3, 3), 4), "only ten players"
assert tc.xi_feasible(mk(5, 2, 4, 6, overseas=5), 4)
assert not tc.xi_feasible(mk(5, 2, 4, 6, overseas=17), 4), "needs 7+ home-grown players"
assert tc.xi_feasible(mk(5, 2, 4, 6, overseas=17), 0), "no overseas rule (T20 World Cup)"
assert tc.roles_needed(mk(8, 0, 0, 0), 15)[:3][0] == "Wicketkeeper" and "AllRounder" in tc.roles_needed(mk(8, 0, 0, 0), 15)[:3]

# --- fixtures ---------------------------------------------------------------------------------
ids = [1, 2, 3, 4, 5, 6, 7, 8]
fx = tc.build_league_fixtures(ids, rng=random.Random(5))
assert len(fx) == 28 and max(f["day_no"] for f in fx) == 7
pairs = {frozenset((f["home"], f["away"])) for f in fx}
assert len(pairs) == 28, "every pair meets exactly once"
for d in range(1, 8):
    day = [f for f in fx if f["day_no"] == d]
    teams = [t for f in day for t in (f["home"], f["away"])]
    assert len(day) == 4 and len(set(teams)) == 8, "one match per team per day"
fx7 = tc.build_league_fixtures([1, 2, 3, 4, 5], rng=random.Random(2))
assert len(fx7) == 10 and max(f["day_no"] for f in fx7) == 5
fx4 = tc.build_league_fixtures([1, 2, 3, 4], rng=random.Random(2))
assert len(fx4) == 12                                                        # double round robin
fx2 = tc.build_league_fixtures([1, 2], rng=random.Random(2))
assert len(fx2) == 3 and {f["day_no"] for f in fx2} == {1, 2, 3}
assert tc.playoff_plan(2) == {"qualify": 0, "stages": []} and tc.playoff_plan(3)["stages"] == ["F"] and tc.playoff_plan(8)["stages"] == ["Q1", "EL", "Q2", "F"]
plan = tc.playoff_plan(8)
seeds = [10, 20, 30, 40]
first = tc.next_playoff_fixtures(plan, seeds, {}, [])
assert [(f["stage"], f["home"], f["away"], f["new_day"]) for f in first] == [("Q1", 10, 20, True), ("EL", 30, 40, False)]
mid = tc.next_playoff_fixtures(plan, seeds, {"Q1": {"winner": 10, "loser": 20}, "EL": {"winner": 40, "loser": 30}}, ["Q1", "EL"])
assert [(f["stage"], f["home"], f["away"]) for f in mid] == [("Q2", 20, 40)]
last = tc.next_playoff_fixtures(plan, seeds, {"Q1": {"winner": 10, "loser": 20}, "EL": {"winner": 40, "loser": 30}, "Q2": {"winner": 20, "loser": 40}}, ["Q1", "EL", "Q2"])
assert [(f["stage"], f["home"], f["away"]) for f in last] == [("F", 10, 20)]
assert tc.next_playoff_fixtures(tc.playoff_plan(3), [7, 8, 9], {}, []) == [{"stage": "F", "home": 7, "away": 8, "new_day": True}]
assert tc.next_playoff_fixtures(tc.playoff_plan(2), [7, 8], {}, []) == []

# --- table / NRR ---------------------------------------------------------------------------------
teams = [{"team_id": i, "name": n} for i, n in ((1, "Alpha"), (2, "Bravo"), (3, "Charlie"))]
F = [
    {"stage": "league", "status": "done", "day_no": 1, "seq": 1, "home_team_id": 1, "away_team_id": 2, "result_type": "win", "winner_team_id": 1,
     "home_runs": 180, "home_wkts": 5, "home_balls": 120, "away_runs": 150, "away_wkts": 10, "away_balls": 110},
    {"stage": "league", "status": "done", "day_no": 2, "seq": 1, "home_team_id": 2, "away_team_id": 3, "result_type": "walkover", "winner_team_id": 3},
    {"stage": "league", "status": "done", "day_no": 3, "seq": 1, "home_team_id": 1, "away_team_id": 3, "result_type": "abandoned", "winner_team_id": None},
    {"stage": "league", "status": "scheduled", "day_no": 4, "seq": 1, "home_team_id": 2, "away_team_id": 1},
    {"stage": "F", "status": "done", "day_no": 9, "seq": 1, "home_team_id": 1, "away_team_id": 2, "result_type": "win", "winner_team_id": 2},
]
table = tc.compute_table(teams, F)
by = {r["team_id"]: r for r in table}
assert by[1]["points"] == 2 and by[1]["played"] == 2 and by[1]["won"] == 1 and by[1]["abandoned"] == 1
assert by[3]["points"] == 2 and by[3]["won"] == 1 and by[3]["played"] == 2
assert by[2]["points"] == 0 and by[2]["lost"] == 2 and by[2]["played"] == 2
# NRR: Alpha 180 off 20 ov  vs 150 conceded (all out -> charged full 20 ov) = 9.0 - 7.5 = 1.5
assert abs(by[1]["nrr"] - 1.5) < 1e-6 and abs(by[2]["nrr"] + 1.5) < 1e-6 and by[3]["nrr"] == 0
assert [r["team_id"] for r in table] == [1, 3, 2], "points tie broken by wins tie -> NRR"
assert by[1]["form"] == ["W", "A"] and by[3]["form"] == ["W", "A"] and by[2]["form"] == ["L", "L"]
nr = tc.compute_table(teams, [{"stage": "league", "status": "done", "day_no": 1, "seq": 1, "home_team_id": 1, "away_team_id": 2, "result_type": "no_result"}])
assert {r["team_id"]: r["points"] for r in nr}[1] == 1 and {r["team_id"]: r["nr"] for r in nr}[2] == 1

# --- no shows ----------------------------------------------------------------------------------------
assert tc.resolve_no_show(True, True) == {"action": "play"}
assert tc.resolve_no_show(True, False) == {"action": "walkover", "winner": "home"}
assert tc.resolve_no_show(False, True) == {"action": "walkover", "winner": "away"}
assert tc.resolve_no_show(False, False) == {"action": "abandoned", "winner": None}
assert tc.resolve_no_show(False, False, "Q1", 1, 2) == {"action": "abandoned", "winner": "home"}
assert tc.resolve_no_show(False, False, "F", 3, 1) == {"action": "abandoned", "winner": "away"}

# --- statistics from a match payload ---------------------------------------------------------------
payload = {
    "potm": {"name": "Ace Batter"},
    "milestones": [{"type": "BAT_50", "innings": 1, "player_name": "Ace Batter", "balls": 24, "runs": 50},
                   {"type": "BAT_100", "innings": 1, "player_name": "Ace Batter", "balls": 51, "runs": 100}],
    "innings": [
        {"innings_number": 1, "batting_team_id": 111, "runs": 201, "wickets": 4, "legal_balls": 120,
         "batters": [{"name": "Ace Batter", "runs": 104, "balls": 55, "fours": 9, "sixes": 6, "dismissed": False, "player_id": 1},
                     {"name": "Duck", "runs": 0, "balls": 3, "fours": 0, "sixes": 0, "dismissed": True},
                     {"name": "Bench", "runs": 0, "balls": 0, "fours": 0, "sixes": 0, "dismissed": False}],
         "bowlers": [{"name": "Opp Bowler", "balls": 24, "runs": 40, "wickets": 1}]},
        {"innings_number": 2, "batting_team_id": 222, "runs": 180, "wickets": 10, "legal_balls": 118,
         "batters": [{"name": "Chaser", "runs": 62, "balls": 40, "fours": 5, "sixes": 3, "dismissed": True}],
         "bowlers": [{"name": "Strike Bowler", "balls": 24, "runs": 22, "wickets": 4}]},
    ],
}
ex = tc.extract_match(payload, {111: 10, 222: 20})
lines = {(l["team_id"], l["player_name"]): l for l in ex["lines"]}
ace = lines[(10, "Ace Batter")]
assert ace["runs"] == 104 and ace["fifty_balls"] == 24 and ace["hundred_balls"] == 51 and ace["potm"] and ace["mvp"] > 150
assert (10, "Bench") not in lines and lines[(10, "Duck")]["mvp"] < 0
assert lines[(10, "Strike Bowler")]["wickets"] == 4 and lines[(10, "Strike Bowler")]["bowl_balls"] == 24
assert (20, "Opp Bowler") in lines, "bowlers belong to the fielding side"
assert lines[(20, "Chaser")]["fifty_balls"] == 40, "without a milestone log the final balls are the fallback"
sc = tc.fixture_scores(ex, 10, 20)
assert sc == {"home_runs": 201, "home_wkts": 4, "home_balls": 120, "away_runs": 180, "away_wkts": 10, "away_balls": 118}
assert tc.match_margin(ex["innings"], 10) == "by 21 runs"
assert tc.match_margin(ex["innings"], 20) == "by 1 wicket" or tc.match_margin(ex["innings"], 20).startswith("by")
assert tc.match_margin(ex["innings"], 10, super_over=True) == "in the Super Over"
assert tc.score_text(201, 4, 120) == "201/4 (20.0)" and tc.fmt_overs(118) == "19.4"

# --- match day clock --------------------------------------------------------------------------------------
start, end = tc.window_bounds(date(2026, 10, 21), 17 * 60, 21 * 60)
assert start == datetime(2026, 10, 21, 11, 30, tzinfo=timezone.utc) and end == datetime(2026, 10, 21, 15, 30, tzinfo=timezone.utc)
noon_ist = datetime(2026, 10, 21, 6, 30, tzinfo=timezone.utc)          # 12:00 IST
assert tc.first_play_date(noon_ist, 17 * 60, 21 * 60) == date(2026, 10, 21)
late = datetime(2026, 10, 21, 14, 30, tzinfo=timezone.utc)             # 20:00 IST - inside the guard window
assert tc.first_play_date(late, 17 * 60, 21 * 60) == date(2026, 10, 22)
assert tc.fmt_window(17 * 60, 21 * 60) == "5 PM – 9 PM IST" and tc.fmt_clock(18 * 60 + 30) == "6:30 PM" and tc.fmt_clock(0) == "12 AM"
assert "<" not in tc.clean_name("  <b>Samay</b>  Premier   League ")
assert tc.clean_name("ab") is None and tc.clean_name("Samay Premier League") == "Samay Premier League"

# --- pool selection / flat lots / best XI / auto-fill ---------------------------------------------------
big = make_pool(210, seed=9)
pick1 = tc.select_pool(big, 100, random.Random(1))
pick2 = tc.select_pool(big, 100, random.Random(2))
assert len(pick1) == len(pick2) == 100 and len({p["name"] for p in pick1}) == 100
assert {p["name"] for p in pick1} != {p["name"] for p in pick2}, "a different pool every auction"
top = sorted(big, key=lambda p: -tc.player_value(p))[:12]
assert all(t["name"] in {p["name"] for p in pick1} for t in top), "stars are always in"
assert len(tc.select_pool(big[:30], 100)) == 30 and "_v" not in tc.select_pool(big, 50)[0]
assert tc.pool_target(4, 25, 210) == 150 and tc.pool_target(10, 25, 210) == 210 and tc.pool_target(2, 15, 500) == 60
assert tc.pool_target(16, 25, 900) == 320
assert tc.pool_is_enough(8, 18, 210) and not tc.pool_is_enough(10, 25, 200)

prof_i = tc.profile("PLAYIPL")
flat = tc.flatten_sets(tc.build_auction_sets(make_pool(80, seed=4), prof_i, random.Random(3)))
assert [l["ord"] for l in flat] == list(range(1, 81)) and len({l["pkey"] for l in flat}) == 80
assert flat[0]["set_pos"] == 1 and flat[0]["set_size"] >= 1 and flat[0]["base_lakh"] >= 20 and isinstance(flat[0]["card"], dict)
assert all(l["role"] in tc.ROLES for l in flat)
accel = tc.accelerated_lots([{**flat[i], "name": flat[i]["name"]} for i in range(7)], random.Random(5))
assert sorted(l["pkey"] for l in accel) == sorted(flat[i]["pkey"] for i in range(7)) and all(l["base_lakh"] == flat[[x["pkey"] for x in flat].index(l["pkey"])]["base_lakh"] for l in accel)
assert accel[0]["set_name"].startswith("Accelerated") and [l["ord"] for l in accel] == list(range(1, 8))


def mk(name, role, bat, bowl, country="India"):
    return {"name": name, "role": role, "bat_level": bat, "bowl_level": bowl, "country": country}


squad = ([mk(f"B{i}", "Batsman", 90 - i, 20) for i in range(5)] + [mk(f"K{i}", "Wicketkeeper", 80 - i, 10) for i in range(2)]
         + [mk(f"A{i}", "AllRounder", 85 - i, 80 - i) for i in range(4)] + [mk(f"W{i}", "Bowler", 20, 88 - i) for i in range(6)])
xi = tc.pick_best_xi(squad, 0)
assert xi is not None and len(xi) == 11 and len({p["name"] for p in xi}) == 11
rc = tc.role_counts(xi)
assert rc["Wicketkeeper"] >= 1 and rc["Batsman"] >= 1 and 2 <= rc["AllRounder"] <= 4 and 3 <= rc["Bowler"] <= 4
assert tc.xi_feasible(xi, 0)
# brute force: the XI is the best legal one
import itertools
best_val = 0
for combo in itertools.combinations(squad, 11):
    c = tc.role_counts(combo)
    if c["Wicketkeeper"] >= 1 and c["Batsman"] >= 1 and 2 <= c["AllRounder"] <= 4 and 3 <= c["Bowler"] <= 4:
        best_val = max(best_val, sum(tc.player_value(p) for p in combo))
assert abs(sum(tc.player_value(p) for p in xi) - best_val) < 1e-6, "best XI is optimal"
assert tc.pick_best_xi(squad[:10], 0) is None
# overseas cap
os_squad = ([mk(f"OB{i}", "Batsman", 95 - i, 20, "Australia") for i in range(4)] + [mk(f"HB{i}", "Batsman", 60 - i, 20) for i in range(3)]
            + [mk("OK", "Wicketkeeper", 90, 10, "England"), mk("HK", "Wicketkeeper", 55, 10)]
            + [mk(f"OA{i}", "AllRounder", 85, 85, "South Africa") for i in range(3)] + [mk(f"HA{i}", "AllRounder", 50, 50) for i in range(2)]
            + [mk(f"OW{i}", "Bowler", 20, 95 - i, "New Zealand") for i in range(4)] + [mk(f"HW{i}", "Bowler", 20, 55 - i) for i in range(4)])
xi_os = tc.pick_best_xi(os_squad, 4)
assert xi_os and sum(1 for p in xi_os if tc.is_overseas(p["country"])) <= 4 and tc.xi_feasible(xi_os, 4)
assert sum(1 for p in tc.pick_best_xi(os_squad, 0) if tc.is_overseas(p["country"])) > 4, "no cap -> stars win"

# auto-fill: a squad with no keeper and only 6 players
thin = [mk(f"T{i}", "Batsman", 70, 10) for i in range(4)] + [mk("TA", "AllRounder", 70, 70), mk("TW", "Bowler", 10, 70)]
cands = [mk(f"C{i}", "Wicketkeeper" if i < 2 else "Bowler" if i < 8 else "AllRounder" if i < 12 else "Batsman", 60 + i, 60 + i) for i in range(20)]
for c in cands:
    c["base_lakh"] = 30
rules_a = {"squad_min": 15, "squad_max": 20, "overseas_squad_max": 0, "xi_overseas_max": 0}
plan = tc.plan_autofill(thin, cands, rules_a, 5000)
filled = thin + [p for p, _price in plan]
assert len(filled) >= 15 and tc.xi_feasible(filled, 0) and all(price == 30 for _p, price in plan)
assert tc.role_counts(filled)["Wicketkeeper"] >= 1
broke = tc.plan_autofill(thin, cands, rules_a, 50)
assert sum(price for _p, price in broke) <= 50 and len(broke) >= 9, "never spends more than the purse (extra slots cost 0)"
assert tc.plan_autofill(thin, [], rules_a, 5000) == []

# --- database rows -> points table -------------------------------------------------------------------------
t_rows = [{"team_no": 1, "name": "Alpha", "code": "CSK1", "base_code": "CSK", "short": "CSK", "owner_id": 10, "owner_name": "A"},
          {"team_no": 2, "name": "Beta", "code": "MI1", "base_code": "MI", "short": None, "owner_id": 20, "owner_name": "B"}]
f_rows = [{"stage": "league", "status": "done", "day_no": 1, "seq": 1, "home_team_no": 1, "away_team_no": 2, "result_type": "win",
           "winner_team_no": 2, "home_runs": 150, "home_wkts": 10, "home_balls": 110, "away_runs": 151, "away_wkts": 3, "away_balls": 100}]
tab = tc.table_from_rows(t_rows, f_rows, 20)
assert [r["team_no"] for r in tab] == [2, 1] and tab[0]["points"] == 2 and tab[0]["short"] == "MI" and tab[1]["short"] == "CSK"
assert tab[0]["owner_name"] == "B" and tab[0]["pos"] == 1

print("tournament core: all checks passed")
