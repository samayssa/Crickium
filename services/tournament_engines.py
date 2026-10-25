"""Match-engine adapters for tournaments.

A tournament fixture is played on one of the four existing match engines:

=========  ===========  =============================================================
engine     preset       squads
=========  ===========  =============================================================
PLAYIPL    IPL          franchise squads (``playint_players``, engine key ``IPL``)
PLAYWPL    WPL          franchise squads (engine key ``WPL``)
PLAYINT    T20 World    national squads (engine key ``T20I``)
PLAY       Custom       the players' own squads (``team_squads``)
=========  ===========  =============================================================

For an auction tournament the franchise engines read the auctioned squad from ``playint_players`` under the
team's *virtual* code (``CSK12``, see :mod:`services.tournament_virtual`), so nothing in them changes. The
Custom engine has no team codes - it asks :func:`custom_squads` for the auctioned squad and its stored best XI.

This module only *starts* matches (skipping the challenge / accept / franchise-pick steps - the tournament
already decided who plays whom) and answers "is this chat or this player busy?". Results come back through
:mod:`services.tournament_hooks`.
"""
from __future__ import annotations

import html
import importlib
import traceback
from typing import Any, Iterable

from database import tournament_auction_repo as ar
from database import tournament_repo as tr
from services import tournament_core as tc
from services import tournament_text as tx
from services import tournament_tg as _tg
from utils.mentions import mention_html

ENGINES = ("PLAY", "PLAYIPL", "PLAYWPL", "PLAYINT")
FRANCHISE_ENGINES = ("PLAYIPL", "PLAYWPL", "PLAYINT")
ENGINE_KEY = {"PLAYIPL": "IPL", "PLAYWPL": "WPL", "PLAYINT": "T20I"}
_MATCH_TABLES = {"PLAY": "play_matches", "PLAYIPL": "playipl_matches", "PLAYWPL": "wpl_matches", "PLAYINT": "playint_matches"}
_ACTIVE_STATUSES = ["pending", "accepted", "team_selection", "pitch_selected", "toss_done", "lineup", "live"]
_REPO_MODULE = {"PLAY": "database.play_repo", "PLAYIPL": "database.playipl_repo", "PLAYWPL": "database.playwpl_repo", "PLAYINT": "database.playint_repo"}
_TEAMS_MODULE = {"PLAYIPL": "database.playipl_teams_repo", "PLAYWPL": "database.playwpl_teams_repo", "PLAYINT": "database.playint_teams_repo"}
_LINEUP_MODULE = {"PLAYIPL": "handlers.playipl.lineup", "PLAYWPL": "handlers.playwpl.lineup", "PLAYINT": "handlers.playint.lineup"}


def repo(engine: str):
    return importlib.import_module(_REPO_MODULE[str(engine).upper()])


def teams_repo(engine: str):
    return importlib.import_module(_TEAMS_MODULE[str(engine).upper()])


# ---------------------------------------------------------------------------
# is anybody busy?
# ---------------------------------------------------------------------------
async def _quiet_fetch(query: str, *args):
    from database.connection import get_pool
    async with get_pool().acquire() as conn:
        return await conn.fetch(query, *args)


async def busy(chat_id: int, user_ids: Iterable[int]) -> dict[str, Any]:
    """{"chat": bool, "users": [ids]} - one quiet query across all four engines' unfinished matches."""
    ids = sorted({int(u) for u in user_ids if u})
    union = " UNION ALL ".join(
        f"SELECT chat_id, challenger_id, opponent_id FROM {table} WHERE status = ANY($1::text[])" for table in _MATCH_TABLES.values())
    rows = await _quiet_fetch(
        f"SELECT chat_id, challenger_id, opponent_id FROM ({union}) m "
        "WHERE chat_id=$2 OR challenger_id = ANY($3::bigint[]) OR opponent_id = ANY($3::bigint[]);",
        _ACTIVE_STATUSES, int(chat_id), ids)
    chat_busy = any(int(r["chat_id"]) == int(chat_id) for r in rows)
    users = sorted({int(x) for r in rows for x in (r["challenger_id"], r["opponent_id"]) if int(x) in ids})
    return {"chat": chat_busy, "users": users}


# ---------------------------------------------------------------------------
# squad checks (before a tournament starts / a player joins)
# ---------------------------------------------------------------------------
async def team_player_count(engine: str, code: str) -> int:
    from database.query import fetchval
    return int(await fetchval("SELECT COUNT(*) FROM playint_players WHERE engine_key=$1 AND team_code=$2;",
                              ENGINE_KEY[engine], str(code)) or 0)


async def franchise_squad_problem(engine: str, code: str) -> str | None:
    """None when the franchise can field a legal XI, else a short reason (direct mode only)."""
    from database.query import fetch
    rows = await fetch("SELECT name, country, role, bat_level, bowl_level FROM playint_players WHERE engine_key=$1 AND team_code=$2;",
                       ENGINE_KEY[engine], str(code))
    if len(rows) < 11:
        return f"only {len(rows)} players are uploaded (11 needed)"
    players = [dict(r, overseas=tc.is_overseas(r["country"])) for r in rows]
    limit = int(tc.profile(engine).get("xi_overseas_max") or 0)
    if not tc.xi_feasible(players, limit):
        return "the uploaded players cannot make a legal Playing XI"
    return None


async def custom_player_problem(user_id: int) -> str | None:
    """Direct-mode Custom tournaments use the player's own cards - the same bar as /play."""
    from utils.debut_gate import get_playing_xi_status, has_minimum_team
    if not await has_minimum_team(int(user_id)):
        return "needs a team of at least 11 players"
    ok, _reason = await get_playing_xi_status(int(user_id))
    if not ok:
        return "Playing XI is not valid (min 3 batters, 1 keeper, 3 all-rounders, 3 bowlers)"
    return None


# ---------------------------------------------------------------------------
# the match card
# ---------------------------------------------------------------------------
def team_emoji(engine: str, code: str | None) -> str:
    try:
        if engine == "PLAYIPL":
            from utils.PremiumEmoji import ipl_team_emoji_html
            return ipl_team_emoji_html(code)
        if engine == "PLAYWPL":
            from utils.WPLPremiumEmoji import wpl_team_emoji_html
            return wpl_team_emoji_html(code)
        if engine == "PLAYINT":
            return teams_repo(engine).team_flag(code)
    except Exception:
        pass
    return "🏏"


def match_card(t: dict, fx: dict, home: dict, away: dict, home_user: dict, away_user: dict) -> str:
    engine = str(t.get("engine") or "PLAYIPL").upper()
    stage = tc.STAGE_LABELS.get(str(fx.get("stage") or "league"), "League")
    stage_line = f" · <b>{html.escape(stage)}</b>" if str(fx.get("stage") or "league") != "league" else ""

    def side(team: dict, user: dict) -> str:
        code = team.get("code")
        name = html.escape(str(team.get("name") or ""))
        short = html.escape(tx.team_short(team))
        who = mention_html(int(user["id"]), user.get("username"), user.get("first_name"))
        head = f"{team_emoji(engine, code)} <b>{short}</b>" + (f" · {name}" if name and name.lower() != short.lower() else "")
        return f"{head}\n      👤 {who}"

    return (
        f"🏆 <b>{html.escape(str(t.get('name') or 'Tournament'))}</b>\n"
        f"📍 <b>Match {int(fx['fno'])}</b>{stage_line}\n"
        f"{side(home, home_user)}\n"
        f"                🆚\n"
        f"{side(away, away_user)}\n"
        f"<blockquote>🏏 Both captains: choose your Playing XI, then the pitch and the toss follow.\n"
        f"⏱ Each step has a 3-minute clock.</blockquote>"
    )


# ---------------------------------------------------------------------------
# launching a fixture
# ---------------------------------------------------------------------------
def _user_dict(row: dict | None, fallback_id: int | None = None) -> dict:
    row = row or {}
    return {"id": int(row.get("user_id") or row.get("id") or fallback_id or 0),
            "username": row.get("username"), "first_name": row.get("first_name") or row.get("owner_name") or "Player"}


async def launch(t: dict, fx: dict, home: dict, away: dict, home_user: dict, away_user: dict) -> dict[str, Any]:
    """Create the engine match for ``fx`` and open its first stage in the host group.

    The fixture must already be claimed (``tr.claim_fixture_start``). Returns {"ok": True, "match_id": id} or
    {"ok": False, "error": text}; on failure the engine match (if any) is ended - the caller releases the fixture.
    """
    engine = str(t.get("engine") or "PLAYIPL").upper()
    chat_id = int(t["host_chat_id"])
    rp = repo(engine)
    match_id: int | None = None
    try:
        row = await rp.create_match(chat_id, int(home_user["id"]), home_user.get("username"), home_user.get("first_name"),
                                    int(away_user["id"]), away_user.get("username"), away_user.get("first_name"))
        match_id = int(dict(row)["match_id"])
        await rp.update_status(match_id, "accepted")
        await tr.attach_match(int(t["tid"]), int(fx["fno"]), match_id)
        if engine in FRANCHISE_ENGINES:
            names = teams_repo(engine)
            await rp.set_team(match_id, int(home_user["id"]), home["code"], names.team_name(home["code"]))
            await rp.set_team(match_id, int(away_user["id"]), away["code"], names.team_name(away["code"]))
        tg = _tg.get()
        await tg.send(chat_id, match_card(t, fx, home, away, home_user, away_user), tx.markup([tx.hub_button(int(t["tid"]))]))
        match = dict(await rp.get_match(match_id))
        if engine in FRANCHISE_ENGINES:
            lineup = importlib.import_module(_LINEUP_MODULE[engine])
            await lineup.send_build_messages(chat_id, match)
        else:
            from handlers.play.pitch import send_pitch_selection
            await send_pitch_selection(chat_id, match)
        try:   # the engines start their 3-minute inactivity clocks from callbacks; nobody has pressed anything yet
            from utils.game_inactivity import sync_after_change
            await sync_after_change(engine, match_id, None)
        except Exception as exc:
            print(f"[tournament/engines] inactivity clock not started for {engine}:{match_id}: {exc!r}")
        return {"ok": True, "match_id": match_id}
    except Exception as exc:
        traceback.print_exc()
        if match_id is not None:
            try:
                await rp.update_status(match_id, "ended")
            except Exception:
                pass
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "match_id": match_id}


# ---------------------------------------------------------------------------
# Custom engine + auction: the auctioned squad instead of the players' own cards
# ---------------------------------------------------------------------------
def batting_order(xi: list[dict]) -> list[dict]:
    """Keepers and batters first (best bat first), then all-rounders, then bowlers - the order the engine bats in."""
    def group(p: dict) -> int:
        role = tc.norm_role(p.get("role"))
        return {tc.ROLE_WK: 0, tc.ROLE_BAT: 0, tc.ROLE_AR: 1, tc.ROLE_BOWL: 2}.get(role, 1)
    return sorted(xi, key=lambda p: (group(p), -int(p.get("bat_level") or 0), str(p.get("name") or "")))


async def _card(row: dict, catalog: dict[str, dict], index: int) -> dict:
    """The engine card of a bought player. A special-edition / showcase card that was auctioned plays as exactly that card."""
    raw = row.get("card")
    if isinstance(raw, str):
        try:
            import json as _json
            raw = _json.loads(raw)
        except Exception:
            raw = {}
    raw = raw if isinstance(raw, dict) else {}
    if str(raw.get("player_kind") or "global") in ("special", "showcase"):
        from services import giveaway as _gw
        live = await _gw.resolve_live_card(raw)
        if live:
            entry = _gw.squad_entry({k: v for k, v in dict(live).items() if k not in {"created_at", "uploaded_by"}})
            return entry
    base = catalog.get(str(row.get("name") or ""))
    if base is not None:
        entry = {k: v for k, v in base.items() if k not in {"created_at", "uploaded_by"}}
    else:   # the catalog row vanished meanwhile: play the card exactly as it was bought
        entry = {"player_id": 9_000_000 + index, "name": row.get("name"), "country": row.get("country"), "role": row.get("role"),
                 "bat_level": row.get("bat_level"), "bowl_level": row.get("bowl_level"),
                 "batting_hand": row.get("batting_hand"), "bowling_hand": row.get("bowling_hand")}
    entry.update({"player_kind": "global", "is_special": False, "is_showcase": False, "edition": None,
                  "special_edition_id": None, "showcase_card_id": None, "showcase_set_id": None, "showcase_name": None})
    return entry


async def custom_squads(match_id: int) -> dict[int, dict[str, list[dict]]] | None:
    """For a Custom-engine match of an *auction* tournament: {user_id: {"xi": [...11], "squad": [...]}}.
    None for every other match (the engine then uses the players' own squads as usual)."""
    fx = await tr.fixture_by_match("PLAY", int(match_id))
    if fx is None:
        return None
    t = await tr.get(int(fx["tid"]))
    if t is None or str(t.get("mode")) != "auction":
        return None
    from database.query import fetch
    out: dict[int, dict[str, list[dict]]] = {}
    for side in ("home", "away"):
        team = await tr.team(int(fx["tid"]), int(fx[f"{side}_team_no"]))
        if team is None:
            return None
        uid = int(fx.get(f"{side}_user_id") or team.get("owner_id") or 0)
        rows = await ar.squad(int(fx["tid"]), int(team["team_no"]))
        names = [str(r["name"]) for r in rows]
        catalog = {str(r["name"]): dict(r) for r in await fetch("SELECT * FROM players WHERE name = ANY($1::text[]);", names)}
        cards = [await _card(r, catalog, i) for i, r in enumerate(rows)]
        by_name = {c["name"]: c for c in cards}
        xi = [by_name[str(r["name"])] for r in rows if r.get("xi") and str(r["name"]) in by_name]
        if len(xi) < 11:   # no stored XI (should not happen) - best legal eleven, else the highest rated
            picked = tc.pick_best_xi([dict(r, key=r["pkey"]) for r in rows], int(t.get("xi_overseas_max") or 0)) or []
            keys = {p["pkey"] for p in picked}
            xi = [by_name[str(r["name"])] for r in rows if r["pkey"] in keys and str(r["name"]) in by_name]
        if len(xi) < 11:
            rest = sorted((c for c in cards if c not in xi), key=lambda c: -max(int(c.get("bat_level") or 0), int(c.get("bowl_level") or 0)))
            xi = (xi + rest)[:11]
        out[uid] = {"xi": batting_order(xi), "squad": cards}
    return out
