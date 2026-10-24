"""Storage for the Mini App "Play Game" mode (user vs computer).

Tables
------
miniapp_game_sessions  one row per game; at most ONE active game per user
miniapp_game_results   one row per finished game (stats, achievements, history)

Writes are deliberately few: a game is stored when it is created, at every
stage change and at the end of every over - never per ball.
"""

from __future__ import annotations

import json
import time
from typing import Any

from database.query import execute, fetch, fetchrow, fetchval

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS miniapp_game_sessions(
        game_id TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        stage TEXT NOT NULL,
        state JSONB NOT NULL,
        rev INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_miniapp_game_active_user ON miniapp_game_sessions(user_id) WHERE status='active';",
    """
    CREATE TABLE IF NOT EXISTS miniapp_game_results(
        game_id TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        difficulty TEXT NOT NULL,
        overs INTEGER NOT NULL,
        sim TEXT NOT NULL,
        pitch TEXT NOT NULL,
        outcome TEXT NOT NULL,
        margin_type TEXT,
        margin INTEGER,
        forfeit BOOLEAN NOT NULL DEFAULT FALSE,
        user_runs INTEGER NOT NULL DEFAULT 0,
        user_wkts INTEGER NOT NULL DEFAULT 0,
        user_balls INTEGER NOT NULL DEFAULT 0,
        ai_runs INTEGER NOT NULL DEFAULT 0,
        ai_wkts INTEGER NOT NULL DEFAULT 0,
        ai_balls INTEGER NOT NULL DEFAULT 0,
        batted_first BOOLEAN,
        target INTEGER,
        opponent TEXT,
        summary JSONB,
        finished_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_miniapp_game_results_user ON miniapp_game_results(user_id, finished_at DESC);",
    """
    CREATE TABLE IF NOT EXISTS miniapp_game_links(
        token TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        user_name TEXT,
        chat_id BIGINT NOT NULL,
        message_id BIGINT,
        overs INTEGER,
        game_id TEXT,
        status TEXT NOT NULL DEFAULT 'open',
        result_text TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_miniapp_game_links_user ON miniapp_game_links(user_id, created_at DESC);",
]

_ready = False
_POOL_CACHE: dict[str, Any] = {"at": 0.0, "rows": []}
POOL_TTL = 900


async def ensure_schema() -> None:
    """Create the tables once per process (also run by both migrate modules)."""
    global _ready
    if _ready:
        return
    for statement in SCHEMA_STATEMENTS:
        await execute(statement)
    _ready = True


# ---------------------------------------------------------------------------
# user context
# ---------------------------------------------------------------------------
def _json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _lineup_ids(raw: Any, squad: list[dict]) -> list[int]:
    available = {int(p.get("player_id") or 0) for p in squad if int(p.get("player_id") or 0) != 0}
    out: list[int] = []
    seen: set[int] = set()
    for item in _json(raw, []) or []:
        try:
            pid = int(item)
        except (TypeError, ValueError):
            continue
        if pid in available and pid not in seen:
            out.append(pid)
            seen.add(pid)
        if len(out) >= 11:
            break
    for p in squad:
        pid = int(p.get("player_id") or 0)
        if pid and pid not in seen:
            out.append(pid)
            seen.add(pid)
        if len(out) >= min(11, len(available)):
            break
    return out[:11]


async def load_user_context(user_id: int) -> dict:
    """Everything needed to build a game: names, saved squad, saved XI order and
    the player upgrades the user has equipped."""
    from services.card_identity import card_entity_id, player_kind
    from services.miniapp_game_squads import make_player

    user = await fetchrow("SELECT user_id, first_name, franchise_name FROM users WHERE user_id=$1;", int(user_id))
    squad_row = await fetchrow("SELECT squad FROM team_squads WHERE user_id=$1;", int(user_id))
    raw_squad = [dict(p) for p in (_json(squad_row["squad"] if squad_row else None, []) or []) if isinstance(p, dict)]
    lineup_row = await fetchrow("SELECT player_ids FROM team_lineups WHERE user_id=$1;", int(user_id))
    lineup = _lineup_ids(lineup_row["player_ids"] if lineup_row else None, raw_squad)

    snapshot: dict = {}
    try:
        from database.player_upgrades_repo import load_snapshot_players
        snapshot = await load_snapshot_players([int(user_id)], {int(user_id): raw_squad})
    except Exception as exc:                                    # upgrades are a bonus, never a blocker
        print(f"[miniapp_game] upgrade snapshot unavailable: {type(exc).__name__}")

    squad = []
    for i, raw in enumerate(raw_squad):
        pid = int(raw.get("player_id") or 0)
        if not pid:
            continue
        try:
            kind = player_kind(raw)
            eid = card_entity_id(raw)
        except Exception:
            kind, eid = "global", pid
        item = snapshot.get(f"{int(user_id)}:{kind}:{pid}") or {}
        squad.append(make_player(raw, i, entity_id=eid, kind=kind,
                                 upg_bat=item.get("batting"), upg_bowl=item.get("bowling")))
    return {
        "first_name": (user["first_name"] if user else None),
        "franchise": (user["franchise_name"] if user else None),
        "squad": squad,
        "lineup": lineup,
    }


async def ai_pool() -> list[dict]:
    """Real players the computer team is drawn from (cached for 15 minutes)."""
    now = time.time()
    if _POOL_CACHE["rows"] and now - _POOL_CACHE["at"] < POOL_TTL:
        return _POOL_CACHE["rows"]
    rows = await fetch(
        "SELECT player_id, name, country, role, bat_level, bowl_level, batting_hand, bowling_hand FROM players;"
    )
    pool = [dict(r) for r in rows]
    _POOL_CACHE["rows"] = pool
    _POOL_CACHE["at"] = now
    return pool


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------
async def save_session(game: dict) -> None:
    await ensure_schema()
    status = "done" if game["stage"] == "done" else "active"
    await execute(
        """
        INSERT INTO miniapp_game_sessions(game_id,user_id,status,stage,state,rev,created_at,updated_at)
        VALUES($1,$2,$3,$4,$5::jsonb,$6,NOW(),NOW())
        ON CONFLICT(game_id) DO UPDATE SET
            status=EXCLUDED.status, stage=EXCLUDED.stage, state=EXCLUDED.state,
            rev=EXCLUDED.rev, updated_at=NOW();
        """,
        game["id"], int(game["uid"]), status, game["stage"], json.dumps(game, default=str), int(game["rev"]),
    )


async def load_active(user_id: int) -> dict | None:
    await ensure_schema()
    row = await fetchrow(
        "SELECT state FROM miniapp_game_sessions WHERE user_id=$1 AND status='active' ORDER BY updated_at DESC LIMIT 1;",
        int(user_id),
    )
    if not row:
        return None
    state = _json(row["state"], None)
    return state if isinstance(state, dict) else None


async def close_session(game_id: str) -> None:
    await execute("UPDATE miniapp_game_sessions SET status='done', updated_at=NOW() WHERE game_id=$1;", game_id)


async def prune_old(days: int = 30) -> None:
    """Housekeeping: finished session bodies are not needed after a month."""
    await execute(
        "DELETE FROM miniapp_game_sessions WHERE status='done' AND updated_at < NOW() - ($1::int * INTERVAL '1 day');",
        int(days),
    )


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------
def result_row(game: dict) -> dict | None:
    """Flatten a finished game into the results row (None = nothing to record)."""
    res = game.get("result")
    if not res:
        return None
    if res.get("forfeit") and not res.get("started"):
        return None
    inns = game.get("inn") or []
    u = next((i for i in inns if i["bat"] == "user"), None)
    a = next((i for i in inns if i["bat"] == "ai"), None)
    winner = res["winner"]
    outcome = "tie" if winner == "tie" else ("win" if winner == "user" else "loss")
    first = inns[0]["bat"] if inns else None
    summary = game_summary(game)
    return {
        "game_id": game["id"], "user_id": int(game["uid"]), "difficulty": game["cfg"]["difficulty"],
        "overs": int(game["cfg"]["overs"]), "sim": game["cfg"]["sim"], "pitch": game["cfg"]["pitch"],
        "outcome": outcome, "margin_type": res.get("type"), "margin": int(res.get("margin") or 0),
        "forfeit": bool(res.get("forfeit")),
        "user_runs": int(u["runs"]) if u else 0, "user_wkts": int(u["wkts"]) if u else 0,
        "user_balls": int(u["balls"]) if u else 0,
        "ai_runs": int(a["runs"]) if a else 0, "ai_wkts": int(a["wkts"]) if a else 0,
        "ai_balls": int(a["balls"]) if a else 0,
        "batted_first": (first == "user") if first else None,
        "target": game.get("target"), "opponent": game["teams"]["ai"]["name"], "summary": summary,
    }


def game_summary(game: dict) -> dict:
    """Personal highlights of the user's side, stored with the result."""
    best_bat = {"runs": 0, "balls": 0, "name": None}
    best_bowl = {"wkts": 0, "runs": 0, "balls": 0, "name": None}
    fours = sixes = 0
    hundreds = fifties = 0
    for inn in game.get("inn") or []:
        if inn["bat"] == "user":
            fours += int(inn.get("fours") or 0)
            sixes += int(inn.get("sixes") or 0)
            for b in inn["batters"]:
                if b["runs"] > best_bat["runs"]:
                    best_bat = {"runs": b["runs"], "balls": b["balls"], "name": b["name"]}
                if b["runs"] >= 100:
                    hundreds += 1
                elif b["runs"] >= 50:
                    fifties += 1
        else:
            for key, st in inn["bowlers"].items():
                if not st["balls"]:
                    continue
                if best_bowl["name"] is None or (st["wkts"], -st["runs"]) > (best_bowl["wkts"], -best_bowl["runs"]):
                    p = game["teams"]["user"]["xi"][int(key)]
                    best_bowl = {"wkts": st["wkts"], "runs": st["runs"], "balls": st["balls"], "name": p["name"]}
    pom = (game.get("result") or {}).get("pom") or {}
    return {
        "best_bat": best_bat, "best_bowl": best_bowl, "fours": fours, "sixes": sixes,
        "fifties": fifties, "hundreds": hundreds,
        "pom_user": bool(pom and pom.get("side") == "user"),
        "text": (game.get("result") or {}).get("text"),
        "xi_changes": int(game.get("xi_changes") or 0),
    }


async def record_result(game: dict) -> bool:
    """Insert the result once.  Returns True when a new row was written."""
    row = result_row(game)
    if not row:
        return False
    await ensure_schema()
    got = await fetchval(
        """
        INSERT INTO miniapp_game_results(
            game_id,user_id,difficulty,overs,sim,pitch,outcome,margin_type,margin,forfeit,
            user_runs,user_wkts,user_balls,ai_runs,ai_wkts,ai_balls,batted_first,target,opponent,summary)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20::jsonb)
        ON CONFLICT(game_id) DO NOTHING RETURNING game_id;
        """,
        row["game_id"], row["user_id"], row["difficulty"], row["overs"], row["sim"], row["pitch"],
        row["outcome"], row["margin_type"], row["margin"], row["forfeit"],
        row["user_runs"], row["user_wkts"], row["user_balls"], row["ai_runs"], row["ai_wkts"], row["ai_balls"],
        row["batted_first"], row["target"], row["opponent"], json.dumps(row["summary"], default=str),
    )
    return got is not None


async def recent_results(user_id: int, limit: int = 8) -> list[dict]:
    await ensure_schema()
    rows = await fetch(
        """
        SELECT game_id,difficulty,overs,sim,pitch,outcome,margin_type,margin,forfeit,user_runs,user_wkts,
               user_balls,ai_runs,ai_wkts,ai_balls,batted_first,target,opponent,summary,finished_at
        FROM miniapp_game_results WHERE user_id=$1 ORDER BY finished_at DESC LIMIT $2;
        """,
        int(user_id), int(limit),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["summary"] = _json(d.get("summary"), {})
        d["finished_at"] = d["finished_at"].isoformat() if d.get("finished_at") else None
        out.append(d)
    return out


async def career_stats(user_id: int) -> dict:
    """Aggregates for the mode's profile card and the achievements."""
    await ensure_schema()
    row = await fetchrow(
        """
        SELECT COUNT(*)::int AS games,
               COUNT(*) FILTER (WHERE outcome='win')::int AS wins,
               COUNT(*) FILTER (WHERE outcome='loss')::int AS losses,
               COUNT(*) FILTER (WHERE outcome='tie')::int AS ties,
               COALESCE(MAX(user_runs),0)::int AS high_score,
               COALESCE(SUM(user_runs),0)::int AS total_runs,
               COALESCE(SUM(ai_wkts),0)::int AS total_wickets,
               COUNT(*) FILTER (WHERE outcome='win' AND difficulty='easy')::int AS wins_easy,
               COUNT(*) FILTER (WHERE outcome='win' AND difficulty='medium')::int AS wins_medium,
               COUNT(*) FILTER (WHERE outcome='win' AND difficulty='hard')::int AS wins_hard,
               COUNT(*) FILTER (WHERE outcome='win' AND difficulty='expert')::int AS wins_expert,
               COUNT(*) FILTER (WHERE outcome='win' AND batted_first IS FALSE)::int AS chases_won,
               COUNT(*) FILTER (WHERE outcome='win' AND batted_first IS TRUE)::int AS defended,
               COUNT(*) FILTER (WHERE outcome='win' AND overs=20)::int AS wins_t20,
               COUNT(*) FILTER (WHERE outcome='win' AND overs=2)::int AS wins_2,
               COUNT(*) FILTER (WHERE outcome='win' AND overs=5)::int AS wins_5,
               COUNT(*) FILTER (WHERE outcome='win' AND overs=10)::int AS wins_10,
               COALESCE(SUM((summary->>'sixes')::int),0)::int AS sixes,
               COALESCE(SUM((summary->>'fours')::int),0)::int AS fours,
               COALESCE(SUM((summary->>'fifties')::int),0)::int AS fifties,
               COALESCE(SUM((summary->>'hundreds')::int),0)::int AS hundreds,
               COUNT(*) FILTER (WHERE (summary->>'pom_user')='true')::int AS pom,
               COALESCE(MAX((summary->'best_bowl'->>'wkts')::int),0)::int AS best_wkts,
               COUNT(*) FILTER (WHERE outcome='win' AND margin_type='runs' AND margin>=50)::int AS big_wins,
               COUNT(*) FILTER (WHERE outcome='win' AND margin_type='wickets' AND margin>=8)::int AS thrashings,
               COUNT(*) FILTER (WHERE outcome='win' AND margin_type='wickets' AND margin<=1)::int AS nail_biters,
               COUNT(*) FILTER (WHERE outcome='win' AND margin_type='runs' AND margin<=3)::int AS nail_biters_runs
        FROM miniapp_game_results WHERE user_id=$1;
        """,
        int(user_id),
    )
    stats = dict(row) if row else {}
    # longest winning streak, newest results last
    seq = await fetch(
        "SELECT outcome FROM miniapp_game_results WHERE user_id=$1 ORDER BY finished_at ASC LIMIT 500;", int(user_id)
    )
    best = run = 0
    for r in seq:
        run = run + 1 if r["outcome"] == "win" else 0
        best = max(best, run)
    stats["best_streak"] = best
    cur = 0
    for r in reversed(list(seq)):
        if r["outcome"] == "win":
            cur += 1
        else:
            break
    stats["streak"] = cur
    games = int(stats.get("games") or 0)
    stats["win_pct"] = round(100.0 * int(stats.get("wins") or 0) / games, 1) if games else 0.0
    return stats


# ---------------------------------------------------------------------------
# group links (/playai): one token per command use
# ---------------------------------------------------------------------------
async def create_link(user_id: int, user_name: str | None, chat_id: int, message_id: int | None,
                      overs: int | None) -> str:
    import secrets
    await ensure_schema()
    # an older, never-started link of the same user is replaced
    await execute("UPDATE miniapp_game_links SET status='expired' WHERE user_id=$1 AND status='open';", int(user_id))
    token = secrets.token_urlsafe(8).replace("-", "x").replace("_", "y")[:10]
    await execute(
        "INSERT INTO miniapp_game_links(token,user_id,user_name,chat_id,message_id,overs) VALUES($1,$2,$3,$4,$5,$6);",
        token, int(user_id), (user_name or "")[:80], int(chat_id), message_id, overs,
    )
    return token


async def get_link(token: str) -> dict | None:
    await ensure_schema()
    row = await fetchrow(
        "SELECT token,user_id,user_name,chat_id,message_id,overs,game_id,status,result_text,created_at "
        "FROM miniapp_game_links WHERE token=$1;", str(token or "")[:32])
    if not row:
        return None
    d = dict(row)
    d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
    return d


async def attach_link(token: str, game_id: str) -> None:
    await execute("UPDATE miniapp_game_links SET game_id=$2, status='playing' WHERE token=$1;", token, game_id)


async def finish_link(token: str, result_text: str | None) -> None:
    await execute("UPDATE miniapp_game_links SET status='done', result_text=$2 WHERE token=$1;", token,
                  (result_text or "")[:300])


async def load_state_by_id(game_id: str) -> dict | None:
    row = await fetchrow("SELECT state FROM miniapp_game_sessions WHERE game_id=$1;", game_id)
    if not row:
        return None
    state = _json(row["state"], None)
    return state if isinstance(state, dict) else None
