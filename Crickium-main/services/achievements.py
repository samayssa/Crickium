"""Achievements: catalog, metric collection, progress and unlock checks.

Achievements are grouped in chapters and ordered as one long journey (a vertical
timeline in the Mini App). State is derived from live game data, so nothing is
written until an achievement is reached; unlocks are stored once.
"""

from __future__ import annotations

import time
from typing import Any

from database import achievements_repo as repo
from database.query import fetchrow, fetchval

# (key, chapter, icon, title, description, metric, target, points)
_RAW: list[tuple] = [
    # Chapter 1 - Rookie
    ("first_steps", "Rookie", "👣", "First Steps", "Open your Crickium account", "joined", 1, 5),
    ("squad_5", "Rookie", "👥", "Gathering Talent", "Hold 5 players in your squad", "squad", 5, 10),
    ("squad_11", "Rookie", "🧢", "Full Eleven", "Build a squad of 11 players", "squad", 11, 15),
    ("captain", "Rookie", "©️", "Leader of Men", "Appoint a captain", "captain", 1, 15),
    ("daily_1", "Rookie", "🎁", "Daily Habit", "Claim your first daily reward", "daily_total", 1, 10),
    ("first_pack", "Rookie", "📦", "Fresh Wrapper", "Open your first pack", "packs", 1, 15),
    # Chapter 2 - Debut
    ("match_1", "Debut", "🏏", "Debut Day", "Play your first match", "matches", 1, 20),
    ("win_1", "Debut", "🏆", "Taste of Victory", "Win your first match", "wins", 1, 25),
    ("match_5", "Debut", "🎯", "Getting Warmer", "Play 5 matches", "matches", 5, 20),
    ("win_5", "Debut", "🔥", "Winning Habit", "Win 5 matches", "wins", 5, 30),
    ("game_1", "Debut", "🎮", "Vs The Machine", "Finish a Play Game match", "g_games", 1, 20),
    ("game_win_1", "Debut", "⚔️", "Machine Slayer", "Beat the computer once", "g_wins", 1, 25),
    # Chapter 3 - Rising
    ("level_5", "Rising", "⭐", "Rising Star", "Reach level 5", "level", 5, 25),
    ("packs_5", "Rising", "🎴", "Pack Hunter", "Open 5 packs", "packs", 5, 25),
    ("squad_20", "Rising", "🧩", "Deep Bench", "Hold 20 players in your squad", "squad", 20, 30),
    ("daily_7", "Rising", "📅", "Week Warrior", "Claim 7 daily rewards", "daily_total", 7, 30),
    ("quest_5", "Rising", "📜", "Quest Taker", "Complete 5 quests", "quests", 5, 25),
    ("rare_card", "Rising", "💎", "Rare Find", "Own a Rare (75+) card", "best_card", 75, 30),
    ("game_5", "Rising", "🕹️", "Arcade Regular", "Finish 5 Play Game matches", "g_games", 5, 30),
    ("easy_win", "Rising", "🌱", "Easy Does It", "Win on Easy difficulty", "g_wins_easy", 1, 20),
    # Chapter 4 - Contender
    ("match_25", "Contender", "📈", "Seasoned Pro", "Play 25 matches", "matches", 25, 40),
    ("win_15", "Contender", "🥇", "Serial Winner", "Win 15 matches", "wins", 15, 45),
    ("level_15", "Contender", "🚀", "On The Rise", "Reach level 15", "level", 15, 40),
    ("epic_card", "Contender", "🔮", "Epic Collector", "Own an Epic (85+) card", "best_card", 85, 45),
    ("refer_1", "Contender", "🤝", "Recruiter", "Bring a friend into Crickium", "referrals", 1, 35),
    ("daily_30", "Contender", "🗓️", "Monthly Master", "Claim 30 daily rewards", "daily_total", 30, 50),
    ("medium_win", "Contender", "⚖️", "Fair Fight", "Win on Medium difficulty", "g_wins_medium", 1, 35),
    ("fifty", "Contender", "5️⃣", "Half Century", "Score 50+ in a Play Game match", "g_fifties", 1, 40),
    ("six_25", "Contender", "💥", "Sky Hitter", "Hit 25 sixes in Play Game", "g_sixes", 25, 40),
    ("chase_1", "Contender", "🎢", "Cool Chaser", "Win a Play Game chase", "g_chases", 1, 40),
    # Chapter 5 - Elite
    ("hard_win", "Elite", "🛡️", "Hard Earned", "Win on Hard difficulty", "g_wins_hard", 1, 60),
    ("win_50", "Elite", "👑", "Dynasty", "Win 50 matches", "wins", 50, 70),
    ("match_100", "Elite", "💯", "Centurion", "Play 100 matches", "matches", 100, 70),
    ("elite_card", "Elite", "🌟", "Elite Eye", "Own an Elite (90+) card", "best_card", 90, 60),
    ("hundred", "Elite", "💫", "Three Figures", "Score 100+ in a Play Game match", "g_hundreds", 1, 70),
    ("defend_1", "Elite", "🧱", "Fortress", "Defend a target in Play Game", "g_defended", 1, 60),
    ("streak_3", "Elite", "⚡", "Hat-trick Streak", "Win 3 Play Game matches in a row", "g_best_streak", 3, 60),
    ("level_30", "Elite", "🏔️", "High Altitude", "Reach level 30", "level", 30, 70),
    # Chapter 6 - Legend
    ("expert_win", "Legend", "🐉", "Expert Slayer", "Win on Expert difficulty", "g_wins_expert", 1, 120),
    ("legend_card", "Legend", "🏛️", "Legendary Collector", "Own a Legendary (95+) card", "best_card", 95, 100),
    ("win_150", "Legend", "🔱", "Living Legend", "Win 150 matches", "wins", 150, 120),
    ("streak_7", "Legend", "☄️", "Unstoppable", "Win 7 Play Game matches in a row", "g_best_streak", 7, 120),
    ("game_win_25", "Legend", "🎖️", "Machine Master", "Beat the computer 25 times", "g_wins", 25, 100),
    ("hall_of_fame", "Legend", "🌌", "Hall of Fame", "Unlock every other achievement", "_all", 1, 250),
]

CHAPTERS = ["Rookie", "Debut", "Rising", "Contender", "Elite", "Legend"]
CHAPTER_META = {
    "Rookie": ("🌱", "Every legend starts somewhere"),
    "Debut": ("🏏", "Step onto the pitch"),
    "Rising": ("⭐", "The grind begins"),
    "Contender": ("🔥", "Rivals start to notice"),
    "Elite": ("👑", "Among the best"),
    "Legend": ("🌌", "Immortal status"),
}
RANKS = [(0, "Newcomer"), (40, "Club Player"), (150, "Pro"), (350, "Star"), (650, "Superstar"), (1000, "Legend")]

CATALOG = [
    {"key": k, "chapter": ch, "icon": ic, "title": t, "desc": d, "metric": m, "target": tg, "points": p}
    for (k, ch, ic, t, d, m, tg, p) in _RAW
]
BY_KEY = {a["key"]: a for a in CATALOG}
_METRIC_CACHE: dict[int, tuple[float, dict]] = {}
_TTL = 20.0


async def _safe(coro, default=0):
    try:
        value = await coro
        return default if value is None else value
    except Exception as exc:
        print(f"[achievements] metric failed: {type(exc).__name__}: {exc}")
        return default


async def collect_metrics(user_id: int, force: bool = False) -> dict[str, Any]:
    cached = _METRIC_CACHE.get(user_id)
    if cached and not force and time.time() - cached[0] < _TTL:
        return dict(cached[1])
    uid = int(user_id)
    m: dict[str, Any] = {"joined": 1}
    row = await _safe(fetchrow("SELECT level, captain_player_id FROM users WHERE user_id=$1;", uid), None)
    m["level"] = int((row["level"] if row else 1) or 1)
    m["captain"] = 1 if (row and row["captain_player_id"]) else 0
    st = await _safe(fetchrow("SELECT matches, wins, losses FROM player_stats WHERE user_id=$1;", uid), None)
    m["matches"] = int((st["matches"] if st else 0) or 0)
    m["wins"] = int((st["wins"] if st else 0) or 0)
    sq = await _safe(fetchval("SELECT squad FROM team_squads WHERE user_id=$1;", uid), None)
    squad: list = []
    if sq:
        try:
            import json
            squad = json.loads(sq) if isinstance(sq, (str, bytes)) else list(sq)
        except Exception:
            squad = []
    m["squad"] = len(squad)
    best = 0
    for p in squad:
        try:
            best = max(best, int(p.get("bat_level") or 0), int(p.get("bowl_level") or 0))
        except Exception:
            pass
    m["best_card"] = best
    m["packs"] = int(await _safe(fetchval("SELECT COUNT(*) FROM pack_openings WHERE user_id=$1;", uid)))
    m["daily_total"] = int(await _safe(fetchval("SELECT total_claimed FROM daily_rewards WHERE user_id=$1;", uid)))
    m["referrals"] = int(await _safe(fetchval(
        "SELECT COUNT(*) FROM referrals WHERE referrer_id=$1 AND status='completed';", uid)))
    m["quests"] = int(await _safe(fetchval("SELECT COUNT(*) FROM quest_completions WHERE user_id=$1;", uid)))
    try:
        from database import miniapp_game_repo
        g = await miniapp_game_repo.career_stats(uid)
    except Exception as exc:
        print(f"[achievements] game stats failed: {type(exc).__name__}")
        g = {}
    wbd = g.get("wins_by_difficulty") or {}
    m["g_games"] = int(g.get("games") or 0)
    m["g_wins"] = int(g.get("wins") or 0)
    for d in ("easy", "medium", "hard", "expert"):
        m[f"g_wins_{d}"] = int(wbd.get(d) or g.get(f"wins_{d}") or 0)
    m["g_fifties"] = int(g.get("fifties") or 0)
    m["g_hundreds"] = int(g.get("hundreds") or 0)
    m["g_sixes"] = int(g.get("sixes") or 0)
    m["g_chases"] = int(g.get("chases_won") or 0)
    m["g_defended"] = int(g.get("defended") or 0)
    m["g_best_streak"] = int(g.get("best_streak") or 0)
    _METRIC_CACHE[user_id] = (time.time(), dict(m))
    return m


def _evaluate(metrics: dict, stored: dict) -> list[dict]:
    out = []
    for a in CATALOG:
        if a["metric"] == "_all":
            others = [x for x in CATALOG if x["key"] != a["key"]]
            cur = sum(1 for x in others if x["key"] in stored or metrics.get(x["metric"], 0) >= x["target"])
            target = len(others)
        else:
            cur = int(metrics.get(a["metric"], 0))
            target = a["target"]
        done = a["key"] in stored or cur >= target
        out.append({**a, "current": min(cur, target), "target": target, "done": done,
                    "pct": 100 if done else int(100 * cur / max(1, target)),
                    "at": (stored[a["key"]].isoformat() if a["key"] in stored and hasattr(stored[a["key"]], "isoformat") else None)})
    return out


def rank_for(points: int) -> tuple[str, str | None, int]:
    name, nxt, need = RANKS[0][1], None, 0
    for i, (floor, title) in enumerate(RANKS):
        if points >= floor:
            name = title
            if i + 1 < len(RANKS):
                nxt, need = RANKS[i + 1][1], RANKS[i + 1][0] - points
            else:
                nxt, need = None, 0
    return name, nxt, need


async def check_and_unlock(user_id: int) -> list[dict]:
    """Persist newly reached achievements; returns their display dicts."""
    metrics = await collect_metrics(user_id, force=True)
    stored = await repo.unlocked(user_id)
    items = _evaluate(metrics, stored)
    new_keys = [i["key"] for i in items if i["done"] and i["key"] not in stored]
    if not new_keys:
        return []
    fresh = set(await repo.unlock_many(user_id, new_keys))
    # hall_of_fame may become reachable after the others are stored
    stored2 = await repo.unlocked(user_id)
    items = _evaluate(metrics, stored2)
    extra = [i["key"] for i in items if i["done"] and i["key"] not in stored2]
    if extra:
        fresh |= set(await repo.unlock_many(user_id, extra))
    return [{"key": k, "icon": BY_KEY[k]["icon"], "title": BY_KEY[k]["title"], "points": BY_KEY[k]["points"]}
            for k in new_keys + extra if k in fresh]


async def summary(user_id: int) -> dict:
    """Light summary for profile cards: unlocked count, total, points, rank."""
    try:
        await check_and_unlock(user_id)
        metrics = await collect_metrics(user_id)
        stored = await repo.unlocked(user_id)
        items = _evaluate(metrics, stored)
    except Exception as exc:
        print(f"[achievements] summary failed: {type(exc).__name__}: {exc}")
        return {"done": 0, "total": len(CATALOG), "points": 0, "rank": RANKS[0][1]}
    done = [i for i in items if i["done"]]
    pts = sum(i["points"] for i in done)
    return {"done": len(done), "total": len(items), "points": pts, "rank": rank_for(pts)[0]}


async def build_response(user_id: int, viewer: Any = None) -> dict:
    try:
        await check_and_unlock(user_id)
    except Exception as exc:
        print(f"[achievements] unlock check failed: {type(exc).__name__}: {exc}")
    metrics = await collect_metrics(user_id)
    stored = await repo.unlocked(user_id)
    items = _evaluate(metrics, stored)
    done = [i for i in items if i["done"]]
    pts = sum(i["points"] for i in done)
    total_pts = sum(i["points"] for i in items)
    rank, nxt, need = rank_for(pts)
    next_item = next((i for i in items if not i["done"]), None)
    chapters = []
    for ch in CHAPTERS:
        rows = [i for i in items if i["chapter"] == ch]
        icon, tag = CHAPTER_META[ch]
        chapters.append({"name": ch, "icon": icon, "tagline": tag, "done": sum(1 for r in rows if r["done"]),
                         "total": len(rows), "items": rows})
    return {
        "ok": True,
        "done": len(done), "total": len(items), "points": pts, "total_points": total_pts,
        "pct": int(100 * len(done) / max(1, len(items))),
        "rank": rank, "next_rank": nxt, "points_to_next": need,
        "next": ({"key": next_item["key"], "title": next_item["title"], "icon": next_item["icon"],
                  "current": next_item["current"], "target": next_item["target"], "pct": next_item["pct"]}
                 if next_item else None),
        "chapters": chapters,
    }
