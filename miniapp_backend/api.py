"""Authenticated Mini App data and actions backed by Crickium's existing DB."""
from __future__ import annotations

import json
import math
import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .auth import TelegramViewer
from .config import BOT_TOKEN
from .db import acquire, execute, fetch, fetchrow, fetchval

from engines.level_engine import MAX_LEVEL, xp_to_next_level
from services.card_identity import (
    card_entity_id,
    is_showcase_player_id,
    owned_same_card,
    showcase_squad_player_id,
)
from utils.price_chart import get_price
from utils.rarity import get_rarity


MAX_SQUAD_SIZE = 25
KITBAG_COOLDOWN_SECONDS = 60 * 60
DAILY_COOLDOWN_SECONDS = 24 * 60 * 60

# This schedule mirrors handlers/daily.py and is deliberately owned by the
# server. The client cannot submit or alter a reward amount.
DAILY_REWARDS = {
    1: {"coins": 500, "rubies": 100, "player_range": None},
    2: {"coins": 1000, "rubies": 200, "player_range": None},
    3: {"coins": 1500, "rubies": 300, "player_range": None},
    4: {"coins": 2500, "rubies": 0, "player_range": (55, 64)},
    5: {"coins": 4000, "rubies": 0, "player_range": (65, 74)},
    6: {"coins": 7000, "rubies": 0, "player_range": (75, 84)},
    7: {"coins": 10000, "rubies": 0, "player_range": (85, 89)},
}

MATCH_TABLES = (
    ("play_matches", "PLAY"),
    ("playso_matches", "PLAYSO"),
    ("playint_matches", "PLAYINT"),
    ("playipl_matches", "PLAYIPL"),
    ("wpl_matches", "PLAYWPL"),
    ("match_challenges", "MATCH"),
)
MATCH_SOURCE_TABLES = {
    "PLAY": ("play_matches", "match_id"),
    "PLAYSO": ("playso_matches", "match_id"),
    "PLAYINT": ("playint_matches", "match_id"),
    "PLAYIPL": ("playipl_matches", "match_id"),
    "PLAYWPL": ("wpl_matches", "match_id"),
    "MATCH": ("match_challenges", "challenge_id"),
}
_TERMINAL_MATCH_STATUSES = {"completed", "ended", "finished", "complete", "declined", "abandoned", "expired", "timed_out", "cancelled"}

_CARD_IMAGES = {
    "global": ("player_card_images", "player_id"),
    "special": ("special_player_card_images", "special_player_id"),
    "showcase": ("showcase_player_card_images", "showcase_card_id"),
}


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback
    return value


def _squad(value: Any) -> list[dict[str, Any]]:
    decoded = _json_value(value, [])
    return [dict(p) for p in decoded if isinstance(p, dict)] if isinstance(decoded, list) else []


def _kind(player: dict[str, Any]) -> str:
    explicit = str(player.get("player_kind") or "").lower()
    if explicit in {"global", "special", "showcase"}:
        return explicit
    if player.get("is_showcase") or player.get("showcase_card_id"):
        return "showcase"
    if player.get("is_special") is True or int(player.get("player_id") or 0) < 0:
        return "showcase" if is_showcase_player_id(int(player.get("player_id") or 0)) else "special"
    return "global"


def _entity_id(player: dict[str, Any]) -> int:
    try:
        return card_entity_id(player)
    except Exception:
        return int(player.get("player_id") or 0)


def _display_name(viewer: TelegramViewer) -> str:
    name = " ".join(x.strip() for x in (viewer.first_name or "", viewer.last_name or "") if x and x.strip())
    return name or "Crickium Player"


def _role_icon(role: str | None) -> str:
    raw = str(role or "").strip().lower()
    if "keeper" in raw or raw in {"wk", "wicket-keeper"}:
        return "🧤"
    if "all" in raw:
        return "⚡"
    if "bowl" in raw:
        return "🎯"
    if "bat" in raw:
        return "🏏"
    return "👤"


def _card_type(player: dict[str, Any]) -> str:
    role = str(player.get("role") or "").lower()
    if "bowl" in role:
        return "ball"
    if "all" in role and int(player.get("bowl_level") or 0) > int(player.get("bat_level") or 0):
        return "ball"
    return "bat"


def _player_payload(player: dict[str, Any], *, owned: bool = False) -> dict[str, Any]:
    kind = _kind(player)
    bat = int(player.get("bat_level") or 0)
    bowl = int(player.get("bowl_level") or 0)
    overall = max(bat, bowl)
    entity_id = _entity_id(player)
    squad_id = int(player.get("player_id") or 0)
    if kind == "special":
        squad_id = -abs(int(player.get("special_edition_id") or entity_id))
    elif kind == "showcase":
        squad_id = int(player.get("player_id") or showcase_squad_player_id(entity_id))
    price = int(get_price(overall)[0])
    version = player.get("edition") if kind == "special" else player.get("showcase_name") if kind == "showcase" else None
    return {
        "player_id": squad_id,
        "entity_id": entity_id,
        "kind": kind,
        "name": str(player.get("name") or "Unknown"),
        "version": version,
        "country": player.get("country"),
        "role": str(player.get("role") or "Unknown"),
        "role_icon": _role_icon(player.get("role")),
        "bat_level": bat,
        "bowl_level": bowl,
        "overall": overall,
        "rarity": get_rarity(overall),
        "batting_hand": player.get("batting_hand"),
        "bowling_hand": player.get("bowling_hand"),
        "buy_price": price,
        "sell_price": int(get_price(overall)[1]),
        "owned": bool(owned),
        "card_type": _card_type(player),
        "card_image_url": f"/api/image/player?kind={kind}&id={entity_id}",
    }


def _is_owned(player: dict[str, Any], squad: list[dict[str, Any]]) -> bool:
    return any(owned_same_card(dict(item), player) for item in squad)


async def _ensure_user(viewer: TelegramViewer) -> dict[str, Any]:
    await execute(
        """
        INSERT INTO users(user_id, username, first_name, last_seen_at)
        VALUES($1,$2,$3,NOW())
        ON CONFLICT(user_id) DO UPDATE SET
            username=COALESCE(EXCLUDED.username,users.username),
            first_name=COALESCE(EXCLUDED.first_name,users.first_name),
            last_seen_at=NOW();
        """,
        int(viewer.id), viewer.username, viewer.first_name,
    )
    row = await fetchrow(
        """
        SELECT user_id,username,first_name,balance,rubies,total_spent,level,xp,
               franchise_name,sigils,total_sigils_earned
        FROM users WHERE user_id=$1;
        """,
        int(viewer.id),
    )
    return dict(row) if row else {"user_id": int(viewer.id), "balance": 0, "rubies": 0, "level": 1, "xp": 0}


async def _get_player_image_source(kind: str, entity_id: int) -> tuple[str, str] | None:
    """Return a custom image file ID or the player's default-template source.

    The Mini App deliberately uses the same PIL renderer and coordinate config as
    bot commands for cards without a custom uploaded image.
    """
    spec = _CARD_IMAGES.get(kind)
    if not spec:
        return None
    table, id_column = spec
    row = await fetchrow(f"SELECT file_id FROM {table} WHERE {id_column}=$1;", int(entity_id))
    if row and row.get("file_id"):
        return str(row["file_id"]), "custom"
    player = await _select_player(kind, entity_id)
    if not player:
        return None
    # Keep template selection identical to the bot renderer, including role
    # aliases and the All Rounder batting/bowling-level tiebreaker.
    from services.card_provider import resolve_card_type
    card = resolve_card_type(player)
    template = await fetchrow("SELECT file_id FROM template_card_image WHERE card_type=$1;", card)
    return (str(template["file_id"]), "template") if template and template.get("file_id") else ("", "template")


async def _download_telegram_image(file_id: str) -> tuple[bytes, str] | None:
    if not file_id or not BOT_TOKEN:
        return None
    import httpx

    async with httpx.AsyncClient(timeout=20) as client:
        info = await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile", params={"file_id": file_id})
        info.raise_for_status()
        payload = info.json()
        file_path = ((payload.get("result") or {}).get("file_path"))
        if not payload.get("ok") or not file_path:
            return None
        response = await client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
        response.raise_for_status()
        return response.content, response.headers.get("content-type", "image/jpeg").split(";", 1)[0]


async def player_image_bytes(kind: str, entity_id: int) -> tuple[bytes, str, str] | None:
    """Proxy custom art, or render a default card with the bot's exact coordinates."""
    source = await _get_player_image_source(kind, entity_id)
    if not source:
        return None
    file_id, image_kind = source
    if image_kind == "custom":
        downloaded = await _download_telegram_image(file_id)
        return (downloaded[0], downloaded[1], "custom") if downloaded else None

    player = await _select_player(kind, entity_id)
    if not player:
        return None
    template_bytes = None
    if file_id:
        try:
            downloaded = await _download_telegram_image(file_id)
            if downloaded:
                template_bytes = downloaded[0]
        except Exception as exc:
            # If Telegram's stored template cannot be fetched, use the same
            # bundled template fallback that the bot's card provider uses.
            print(f"[miniapp_backend] card template fetch failed: {type(exc).__name__}")
    try:
        from services.card_provider import resolve_card_type
        from services.player_card import render_player_card
        rendered = render_player_card(player, template_bytes=template_bytes, card_type=resolve_card_type(player))
        return rendered, "image/png", "rendered-template"
    except Exception as exc:
        print(f"[miniapp_backend] default card render failed: {type(exc).__name__}")
        # Returning no image allows the frontend's branded fallback to remain usable.
        return None


async def profile_image_bytes(user_id: int) -> tuple[bytes, str] | None:
    if not BOT_TOKEN:
        return None
    import httpx

    async with httpx.AsyncClient(timeout=15) as client:
        photos = await client.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUserProfilePhotos",
            params={"user_id": int(user_id), "limit": 1},
        )
        photos.raise_for_status()
        payload = photos.json()
        sets = ((payload.get("result") or {}).get("photos") or [])
        if not payload.get("ok") or not sets or not sets[0]:
            return None
        file_id = sets[0][-1].get("file_id")
        if not file_id:
            return None
        info = await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile", params={"file_id": file_id})
        info.raise_for_status()
        file_path = ((info.json().get("result") or {}).get("file_path"))
        if not file_path:
            return None
        image = await client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
        image.raise_for_status()
        return image.content, image.headers.get("content-type", "image/jpeg").split(";", 1)[0]


async def _owned_squad(user_id: int) -> list[dict[str, Any]]:
    row = await fetchrow("SELECT squad FROM team_squads WHERE user_id=$1;", int(user_id))
    return _squad(row["squad"] if row else None)


def _player_id_set(squad: list[dict[str, Any]]) -> set[int]:
    return {int(player.get("player_id") or 0) for player in squad if int(player.get("player_id") or 0) != 0}


def _normalize_lineup_ids(value: Any, squad: list[dict[str, Any]]) -> list[int]:
    """Match engines require eleven unique squad IDs; repair stale lineups safely."""
    available = _player_id_set(squad)
    raw = _json_value(value, [])
    result: list[int] = []
    seen: set[int] = set()
    if isinstance(raw, list):
        for item in raw:
            try:
                player_id = int(item)
            except (TypeError, ValueError):
                continue
            if player_id in available and player_id not in seen:
                result.append(player_id)
                seen.add(player_id)
            if len(result) >= 11:
                break
    for player in squad:
        player_id = int(player.get("player_id") or 0)
        if player_id and player_id not in seen:
            result.append(player_id)
            seen.add(player_id)
        if len(result) >= min(11, len(available)):
            break
    return result[:11]


async def _effective_lineup_ids(user_id: int, squad: list[dict[str, Any]]) -> list[int]:
    row = await fetchrow("SELECT player_ids FROM team_lineups WHERE user_id=$1;", int(user_id))
    return _normalize_lineup_ids(row["player_ids"] if row else None, squad)


def _save_lineup_ids_conn(conn: Any, user_id: int, player_ids: list[int]) -> Any:
    return conn.execute(
        """
        INSERT INTO team_lineups(user_id,player_ids,updated_at) VALUES($1,$2::jsonb,NOW())
        ON CONFLICT(user_id) DO UPDATE SET player_ids=EXCLUDED.player_ids,updated_at=NOW();
        """,
        int(user_id), json.dumps(player_ids),
    )


async def _rank_for_user(user_id: int) -> int:
    rank = await fetchval(
        """
        SELECT rank FROM (
            SELECT user_id, ROW_NUMBER() OVER(ORDER BY COALESCE(level,1) DESC,COALESCE(xp,0) DESC,user_id ASC) rank
            FROM users
        ) ranked WHERE user_id=$1;
        """,
        int(user_id),
    )
    return int(rank or 0)


async def _runtime_session(engine: str, match_id: int):
    """Load an internal engine snapshot if one exists; returns None on unsupported/unavailable state."""
    engine = str(engine or "").upper()
    if engine not in {"PLAY", "PLAYINT", "PLAYIPL", "PLAYWPL"}:
        return None
    try:
        row = await fetchrow(
            "SELECT payload FROM game_session_snapshots WHERE engine=$1 AND match_id=$2;",
            engine, int(match_id),
        )
        if not row or not row.get("payload"):
            return None
        # Snapshots are produced and stored by this application's own game
        # session recovery code; decode failures are treated as unavailable data.
        import pickle
        return pickle.loads(bytes(row["payload"]))
    except Exception as exc:
        print(f"[miniapp_backend] runtime snapshot unavailable engine={engine} id={int(match_id)} type={type(exc).__name__}")
        return None


def _team_names_from_match(row: dict[str, Any]) -> tuple[str, str]:
    first = row.get("challenger_team_name") or row.get("challenger_team_code") or row.get("challenger_name") or "Challenger"
    second = row.get("opponent_team_name") or row.get("opponent_team_code") or row.get("opponent_name") or "Opponent"
    return str(first), str(second)


async def _active_matches(user_id: int, limit: int = 5) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    # Each engine has its own table. A small per-source cap still finds concurrent
    # modes while avoiding dozens of snapshot reads on every Matches refresh.
    per_source_limit = max(5, min(int(limit), 10))
    for table, engine in MATCH_TABLES:
        try:
            if table == "match_challenges":
                rows = await fetch(
                    """
                    SELECT challenge_id AS match_id,challenger_id,challenger_name,
                           opponent_id,opponent_name,status,NULL::text AS pitch,
                           format AS match_type,created_at
                    FROM match_challenges
                    WHERE (challenger_id=$1 OR opponent_id=$1)
                      AND LOWER(COALESCE(status,'')) NOT IN
                          ('completed','ended','finished','complete','declined','abandoned','expired','timed_out','cancelled')
                    ORDER BY created_at DESC LIMIT $2;
                    """,
                    int(user_id), int(per_source_limit),
                )
            elif table == "playso_matches":
                rows = await fetch(
                    """
                    SELECT match_id,challenger_id,challenger_name,challenger_username,
                           opponent_id,opponent_name,opponent_username,status,pitch,format AS match_type,
                           stadium,innings_no,state,created_at
                    FROM playso_matches
                    WHERE (challenger_id=$1 OR opponent_id=$1)
                      AND LOWER(COALESCE(status,'')) NOT IN
                          ('completed','ended','finished','complete','declined','abandoned','expired','timed_out','cancelled')
                    ORDER BY created_at DESC LIMIT $2;
                    """,
                    int(user_id), int(per_source_limit),
                )
            elif table in {"playint_matches", "playipl_matches", "wpl_matches"}:
                rows = await fetch(
                    f"""
                    SELECT match_id,challenger_id,challenger_name,challenger_username,
                           opponent_id,opponent_name,opponent_username,status,pitch,created_at,
                           challenger_team_code,challenger_team_name,opponent_team_code,opponent_team_name
                    FROM {table}
                    WHERE (challenger_id=$1 OR opponent_id=$1)
                      AND LOWER(COALESCE(status,'')) NOT IN
                          ('completed','ended','finished','complete','declined','abandoned','expired','timed_out','cancelled')
                    ORDER BY created_at DESC LIMIT $2;
                    """,
                    int(user_id), int(per_source_limit),
                )
            else:
                rows = await fetch(
                    f"""
                    SELECT match_id,challenger_id,challenger_name,challenger_username,
                           opponent_id,opponent_name,opponent_username,status,pitch,created_at
                    FROM {table}
                    WHERE (challenger_id=$1 OR opponent_id=$1)
                      AND LOWER(COALESCE(status,'')) NOT IN
                          ('completed','ended','finished','complete','declined','abandoned','expired','timed_out','cancelled')
                    ORDER BY created_at DESC LIMIT $2;
                    """,
                    int(user_id), int(per_source_limit),
                )
        except Exception as exc:
            print(f"[miniapp_backend] match source {table} unavailable: {type(exc).__name__}")
            continue
        for raw in rows:
            item = dict(raw)
            item["engine"] = engine
            item["match_id"] = int(item["match_id"])
            item["match_type"] = item.get("match_type") or engine
            item["created_at"] = item["created_at"].isoformat() if item.get("created_at") else None
            first, second = _team_names_from_match(item)
            item["team_a_name"] = first
            item["team_b_name"] = second
            status = str(item.get("status") or "").lower()
            item["is_live"] = status in {"live", "playing", "innings_break", "chase", "in_progress"}
            item["current_score"] = None

            if engine == "PLAYSO":
                state = _json_value(item.get("state"), {})
                if isinstance(state, dict) and ("runs" in state or "legal_balls" in state):
                    balls = max(0, int(state.get("legal_balls") or 0))
                    item["runs"] = max(0, int(state.get("runs") or 0))
                    item["wickets"] = max(0, int(state.get("wickets") or 0))
                    item["over_text"] = f"{balls // 6}.{balls % 6}"
                    item["target"] = state.get("target")
                    item["current_score"] = f"{item['runs']}/{item['wickets']} ({item['over_text']})"
            else:
                session = await _runtime_session(engine, item["match_id"])
                innings = getattr(session, "innings", None) if session else None
                score = getattr(innings, "score", None) if innings else None
                if score is not None:
                    runs = int(getattr(score, "runs", 0) or 0)
                    wickets = int(getattr(score, "wickets", 0) or 0)
                    balls = int(getattr(score, "legal_balls", 0) or 0)
                    item["runs"] = runs
                    item["wickets"] = wickets
                    item["over_text"] = f"{balls // 6}.{balls % 6}"
                    item["target"] = getattr(innings, "target", None)
                    item["current_score"] = f"{runs}/{wickets} ({item['over_text']})"
                    item["batting_team"] = getattr(session, "batting_team_display", None)
            result.append(item)
    result.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    # Match IDs are local to their engine. Preserve source engine in all cards/routes.
    return result[:limit]


async def build_home_response(viewer: TelegramViewer) -> dict[str, Any]:
    user = await _ensure_user(viewer)
    stats_row = await fetchrow(
        "SELECT matches,wins,losses,runs,wickets FROM player_stats WHERE user_id=$1;",
        int(viewer.id),
    )
    stats = dict(stats_row) if stats_row else {}
    squad = await _owned_squad(viewer.id)
    level = max(1, min(MAX_LEVEL, int(user.get("level") or 1)))
    xp = max(0, int(user.get("xp") or 0))
    next_xp = xp_to_next_level(level)
    daily = await fetchrow("SELECT streak,total_claimed,last_claim_at,next_claim_at FROM daily_rewards WHERE user_id=$1;", int(viewer.id))
    daily_next = daily["next_claim_at"] if daily else None
    now = datetime.now(timezone.utc)
    if daily_next and daily_next.tzinfo is None:
        daily_next = daily_next.replace(tzinfo=timezone.utc)
    daily_available = not daily_next or daily_next <= now
    rank = await _rank_for_user(viewer.id)
    matches_played = int(stats.get("matches") or 0)
    wins = int(stats.get("wins") or 0)
    recent = await fetch(
        """
        SELECT engine,match_id,match_type,won,pitch,target,batting_runs,bowling_wickets,played_at
        FROM quest_match_summaries WHERE user_id=$1 ORDER BY played_at DESC LIMIT 5;
        """,
        int(viewer.id),
    )
    active = await _active_matches(viewer.id, limit=4)
    progress = 100 if next_xp is None else max(0, min(100, round(xp * 100 / max(1, next_xp))))
    photo_url = "/api/image/profile"
    return {
        "profile": {
            "id": int(viewer.id),
            "display_name": _display_name(viewer),
            "first_name": viewer.first_name,
            "last_name": viewer.last_name,
            "username": viewer.username,
            "photo_url": photo_url,
        },
        "wallet": {
            "coins": int(user.get("balance") or 0),
            "rubies": int(user.get("rubies") or 0),
            "sigils": int(user.get("sigils") or 0),
            "total_spent": int(user.get("total_spent") or 0),
        },
        "progress": {
            "level": level, "xp": xp, "xp_to_next": next_xp,
            "progress_percent": progress, "max_level": level >= MAX_LEVEL,
        },
        "stats": {
            "matches_played": matches_played,
            "matches_won": wins,
            "matches_lost": int(stats.get("losses") or 0),
            "win_percentage": round(wins * 100 / matches_played, 1) if matches_played else 0.0,
            "runs": int(stats.get("runs") or 0),
            "wickets": int(stats.get("wickets") or 0),
            "rank": rank,
        },
        "league": {"label": "User level", "progress_percent": progress, "progress_text": f"Level {level} • {xp:,} / {next_xp if next_xp is not None else 'MAX'} XP"},
        "squad": {"count": len(squad), "capacity": MAX_SQUAD_SIZE},
        "daily_reward": {
            "streak": int((daily or {}).get("streak") or 0),
            "total_claimed": int((daily or {}).get("total_claimed") or 0),
            "available": daily_available,
            "seconds_until_available": max(0, int((daily_next - now).total_seconds())) if daily_next and not daily_available else 0,
            "days": _daily_reward_schedule(int((daily or {}).get("streak") or 0), daily_available),
        },
        "recent_matches": [_match_summary(dict(row)) for row in recent],
        "active_matches": active,
    }


def _daily_reward_schedule(streak: int, available: bool) -> list[dict[str, Any]]:
    next_day = 1 if streak >= 7 else streak + 1
    if not available:
        next_day = 1 if streak >= 7 else streak + 1
    result = []
    for day, reward in DAILY_REWARDS.items():
        range_value = reward["player_range"]
        result.append({
            "day": day,
            "coins": reward["coins"],
            "rubies": reward["rubies"],
            "player_range": list(range_value) if range_value else None,
            "status": "claimed" if day <= streak else "available" if day == next_day and available else "locked",
        })
    return result


def _match_summary(row: dict[str, Any]) -> dict[str, Any]:
    played_at = row.get("played_at")
    return {
        "engine": row.get("engine"),
        "match_id": int(row.get("match_id") or 0),
        "match_type": row.get("match_type") or row.get("engine") or "Match",
        "won": bool(row.get("won")),
        "pitch": row.get("pitch"),
        "target": int(row.get("target") or 0),
        "runs": int(row.get("batting_runs") or 0),
        "wickets": int(row.get("bowling_wickets") or 0),
        "played_at": played_at.isoformat() if played_at else None,
        "detail_available": True,
    }


async def _query_players(kind: str, query: str, limit: int) -> list[dict[str, Any]]:
    pattern = f"%{query.strip()}%" if query.strip() else "%"
    if kind == "global":
        rows = await fetch(
            """
            SELECT player_id,name,country,role,bat_level,bowl_level,batting_hand,bowling_hand,
                   NULL::text edition,NULL::text showcase_name,NULL::bigint special_edition_id,
                   'global'::text player_kind
            FROM players
            WHERE ($1='%' OR LOWER(name) LIKE LOWER($1) OR LOWER(COALESCE(country,'')) LIKE LOWER($1))
            ORDER BY GREATEST(bat_level,bowl_level) DESC,name LIMIT $2;
            """, pattern, int(limit),
        )
    elif kind == "special":
        rows = await fetch(
            """
            SELECT -special_player_id AS player_id,name,country,role,bat_level,bowl_level,
                   batting_hand,bowling_hand,edition,NULL::text showcase_name,
                   special_player_id AS special_edition_id,'special'::text player_kind
            FROM special_edition_players
            WHERE ($1='%' OR LOWER(name) LIKE LOWER($1) OR LOWER(COALESCE(country,'')) LIKE LOWER($1)
                   OR LOWER(edition) LIKE LOWER($1))
            ORDER BY GREATEST(bat_level,bowl_level) DESC,name LIMIT $2;
            """, pattern, int(limit),
        )
    else:
        rows = await fetch(
            """
            SELECT -(1000000000000::bigint+c.showcase_card_id) AS player_id,c.name,c.country,c.role,
                   c.bat_level,c.bowl_level,c.batting_hand,c.bowling_hand,NULL::text edition,
                   s.set_name AS showcase_name,c.showcase_card_id AS special_edition_id,
                   'showcase'::text player_kind
            FROM showcase_cards c JOIN showcase_sets s USING(showcase_set_id)
            WHERE s.status='active'
              AND ($1='%' OR LOWER(c.name) LIKE LOWER($1) OR LOWER(COALESCE(c.country,'')) LIKE LOWER($1)
                   OR LOWER(s.set_name) LIKE LOWER($1))
            ORDER BY GREATEST(c.bat_level,c.bowl_level) DESC,c.name LIMIT $2;
            """, pattern, int(limit),
        )
    return [dict(row) for row in rows]


async def build_player_search_response(viewer: TelegramViewer, query: str, limit: int = 10) -> dict[str, Any]:
    limit = max(1, min(int(limit), 30))
    query = str(query or "").strip()
    groups = await asyncio.gather(
        _query_players("global", query, limit),
        _query_players("special", query, limit),
        _query_players("showcase", query, limit),
        return_exceptions=True,
    )
    rows = []
    for group in groups:
        if isinstance(group, Exception):
            print(f"[miniapp_backend] player search source failed: {group!r}")
            continue
        rows.extend(group)
    rows.sort(key=lambda p: (-max(int(p.get("bat_level") or 0), int(p.get("bowl_level") or 0)), str(p.get("name") or "")))
    rows = rows[:limit]
    squad = await _owned_squad(viewer.id)
    results = [_player_payload(row, owned=_is_owned(row, squad)) for row in rows]
    return {"query": query, "count": len(results), "results": results}


async def build_market_response(viewer: TelegramViewer, params: dict[str, str]) -> dict[str, Any]:
    query = (params.get("q") or "").strip()
    requested_role = (params.get("role") or "all").strip().lower()
    allowed_roles = {
        "batsman": {"batsman", "batter"},
        "bowler": {"bowler"},
        "allrounder": {"allrounder", "all-rounder"},
        "wicketkeeper": {"wicketkeeper", "wicket-keeper", "wicket keeper", "wk"},
    }
    try:
        limit = max(1, min(int(params.get("limit") or 24), 50))
    except ValueError:
        limit = 24
    # Market browsing must not silently be limited to the 50 highest-rated
    # cards returned by the search endpoint. Read a bounded catalog window from
    # each real card source, then apply the user's filters and server prices.
    groups = await asyncio.gather(
        _query_players("global", query, 250),
        _query_players("special", query, 250),
        _query_players("showcase", query, 250),
        return_exceptions=True,
    )
    candidates: list[dict[str, Any]] = []
    for group in groups:
        if isinstance(group, Exception):
            print(f"[miniapp_backend] market source failed: {group!r}")
            continue
        candidates.extend(group)
    squad = await _owned_squad(viewer.id)
    items = [_player_payload(row, owned=_is_owned(row, squad)) for row in candidates]
    role_set = allowed_roles.get(requested_role)
    if role_set:
        normalized_roles = {"".join(ch for ch in role_name if ch.isalnum()) for role_name in role_set}
        def role_matches(role: str) -> bool:
            normalized = "".join(ch for ch in role.lower() if ch.isalnum())
            if requested_role == "allrounder":
                return "allrounder" in normalized
            if requested_role == "wicketkeeper":
                return "wicketkeeper" in normalized or normalized == "wk"
            return normalized in normalized_roles
        items = [item for item in items if role_matches(str(item["role"]))]
    country = (params.get("country") or "").strip().lower()
    if country:
        items = [item for item in items if country in str(item.get("country") or "").lower()]
    for field, key in (("min_overall", "min"), ("max_overall", "max"), ("min_bat", "min_bat"), ("max_bat", "max_bat"), ("min_bowl", "min_bowl"), ("max_bowl", "max_bowl")):
        raw = params.get(key)
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if field.startswith("min"):
            items = [item for item in items if int(item["overall" if field.endswith("overall") else "bat_level" if field.endswith("bat") else "bowl_level"]) >= value]
        else:
            items = [item for item in items if int(item["overall" if field.endswith("overall") else "bat_level" if field.endswith("bat") else "bowl_level"]) <= value]
    for key, comparator in (("min_price", lambda price, value: price >= value), ("max_price", lambda price, value: price <= value)):
        raw = params.get(key)
        if raw:
            try:
                value = max(0, int(raw))
                items = [item for item in items if comparator(int(item["buy_price"]), value)]
            except ValueError:
                pass
    sort = (params.get("sort") or "overall").lower()
    if sort == "price_asc":
        items.sort(key=lambda item: (item["buy_price"], item["name"].lower()))
    elif sort == "price_desc":
        items.sort(key=lambda item: (-item["buy_price"], item["name"].lower()))
    elif sort == "name":
        items.sort(key=lambda item: item["name"].lower())
    elif sort == "bat":
        items.sort(key=lambda item: (-item["bat_level"], item["name"].lower()))
    elif sort == "bowl":
        items.sort(key=lambda item: (-item["bowl_level"], item["name"].lower()))
    else:
        items.sort(key=lambda item: (-item["overall"], item["name"].lower()))
    countries = sorted({str(item["country"]) for item in items if item.get("country")})
    total = len(items)
    return {
        "items": items[:limit],
        "count": total,
        "countries": countries,
        "limit": limit,
        "scanned_count": len(candidates),
        "catalog_window_truncated": len(candidates) >= 750,
    }


async def build_player_detail_response(viewer: TelegramViewer, *, kind: str, entity_id: int) -> dict[str, Any] | None:
    if kind == "global":
        row = await fetchrow("SELECT *, 'global'::text player_kind FROM players WHERE player_id=$1;", int(entity_id))
    elif kind == "special":
        row = await fetchrow("SELECT *, -special_player_id AS player_id, special_player_id AS special_edition_id, 'special'::text player_kind, TRUE AS is_special FROM special_edition_players WHERE special_player_id=$1;", int(entity_id))
    elif kind == "showcase":
        row = await fetchrow(
            """
            SELECT c.*, -(1000000000000::bigint+c.showcase_card_id) AS player_id,
                   c.showcase_card_id AS special_edition_id,s.set_name AS showcase_name,
                   'showcase'::text player_kind,TRUE AS is_showcase
            FROM showcase_cards c JOIN showcase_sets s USING(showcase_set_id)
            WHERE c.showcase_card_id=$1 AND s.status='active';
            """, int(entity_id),
        )
    else:
        return None
    if not row:
        return None
    player = dict(row)
    squad = await _owned_squad(viewer.id)
    summary = _player_payload(player, owned=_is_owned(player, squad))
    pid = int(summary["player_id"])
    stats_row = await fetchrow(
        """
        SELECT COALESCE(SUM(bat_matches),0) bat_matches,
               COALESCE(SUM(bat_innings),0) bat_innings,
               COALESCE(SUM(runs),0) runs,
               COALESCE(SUM(fifties),0) fifties,
               COALESCE(SUM(centuries),0) centuries,
               COALESCE(SUM(bat_balls),0) bat_balls,
               COALESCE(SUM(dismissals),0) dismissals,
               COALESCE(SUM(bowl_matches),0) bowl_matches,
               COALESCE(SUM(bowl_innings),0) bowl_innings,
               COALESCE(SUM(wickets),0) wickets,
               COALESCE(SUM(bowl_balls),0) bowl_balls,
               COALESCE(SUM(bowl_runs),0) bowl_runs,
               COALESCE(MAX(runs),0) highest_recorded_match_runs,
               COALESCE(MAX(wickets),0) best_recorded_match_wickets
         FROM player_user_match_stats WHERE user_id=$1 AND player_id=$2;
         """, int(viewer.id), pid,
    )
    stat = dict(stats_row) if stats_row else {}
    runs = int(stat.get("runs") or 0)
    dismissals = int(stat.get("dismissals") or 0)
    bat_balls = int(stat.get("bat_balls") or 0)
    wickets = int(stat.get("wickets") or 0)
    bowl_balls = int(stat.get("bowl_balls") or 0)
    bowl_runs = int(stat.get("bowl_runs") or 0)
    history = await fetch(
        """
        SELECT match_id,bat_matches,bat_innings,runs,fifties,centuries,bat_balls,dismissals,
               bowl_matches,bowl_innings,wickets,three_wickets,five_wickets,bowl_balls,bowl_runs,created_at
         FROM player_user_match_stats WHERE user_id=$1 AND player_id=$2 ORDER BY created_at DESC LIMIT 20;
         """, int(viewer.id), pid,
    )
    return {
        **summary,
        "description": " • ".join(filter(None, [
            summary["role"], str(player.get("country") or ""),
            str(player.get("edition") or player.get("showcase_name") or ""),
        ])),
        "batting_stats": {
            "matches": int(stat.get("bat_matches") or 0),
            "innings": int(stat.get("bat_innings") or 0),
            "runs": runs,
            "average": round(runs / dismissals, 2) if dismissals else None,
            "strike_rate": round(runs * 100 / bat_balls, 2) if bat_balls else None,
            "highest_recorded_match_runs": int(stat.get("highest_recorded_match_runs") or 0),
            "fifties": int(stat.get("fifties") or 0),
            "centuries": int(stat.get("centuries") or 0),
            "fours": None, "sixes": None,
        },
        "bowling_stats": {
            "matches": int(stat.get("bowl_matches") or 0),
            "innings": int(stat.get("bowl_innings") or 0),
            "wickets": wickets,
            "runs_conceded": bowl_runs,
            "economy": round(bowl_runs * 6 / bowl_balls, 2) if bowl_balls else None,
            "average": round(bowl_runs / wickets, 2) if wickets else None,
            "strike_rate": round(bowl_balls / wickets, 2) if wickets else None,
            "best_recorded_match_wickets": int(stat.get("best_recorded_match_wickets") or 0),
        },
        "match_history": [
            {key: (value.isoformat() if isinstance(value, datetime) else value) for key, value in dict(item).items()}
            for item in history
        ],
        "stats_note": "Only this Telegram user's values stored in player_user_match_stats are shown. Highest score, fours, sixes and full scorecards are not recorded in this table.",
    }


async def _pack_owned_cards(user_id: int, limit: int = 120) -> list[dict[str, Any]]:
    """Return cards the pack service actually persisted as owned card inventory.

    These are kept separate from the active squad: opening a pack does not silently
    promote a reward into the user's playing XI/squad.
    """
    try:
        rows = await fetch(
            """
            SELECT pci.card_id,pci.opening_id,pci.player_id,pci.card_kind,pci.position,
                   po.pack_key,po.opened_at,po.cards
            FROM pack_card_inventory pci
            JOIN pack_openings po ON po.opening_id=pci.opening_id
            WHERE pci.user_id=$1
            ORDER BY po.opened_at DESC,pci.position ASC LIMIT $2;
            """, int(user_id), max(1, min(int(limit), 250)),
        )
    except Exception as exc:
        # Older databases can lack the pack ledger until the additive migration
        # has run. The UI should remain available while the startup migration runs.
        print(f"[miniapp_backend] pack collection unavailable: {type(exc).__name__}")
        return []

    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        cards = _json_value(row.get("cards"), [])
        if not isinstance(cards, list):
            continue
        position = int(row.get("position") or 0)
        if position < 1 or position > len(cards):
            continue
        card = cards[position - 1]
        if not isinstance(card, dict) or not isinstance(card.get("player"), dict):
            continue
        player = dict(card["player"])
        # Prefer the immutable opening snapshot, but verify it matches the
        # normalized ownership ledger row before showing it as a collected card.
        try:
            if int(player.get("player_id") or card.get("player_id") or 0) != int(row.get("player_id") or 0):
                continue
        except (TypeError, ValueError):
            continue
        card_ovr = int(card.get("ovr") or max(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0)))
        output.append({
            **_player_payload(player, owned=True),
            # Pack inventory sells using the saved opening's OVR. Mirror that
            # exact value in the collection UI to keep confirmation and credit aligned.
            "sell_price": int(get_price(card_ovr)[1]),
            "pack_key": str(row.get("pack_key") or ""),
            "pack_slot": str(row.get("card_kind") or card.get("kind") or "bonus"),
            "card_id": int(row.get("card_id") or 0),
            "opening_id": str(row.get("opening_id") or ""),
            "opened_at": _iso(row.get("opened_at")),
            "card_ovr": card_ovr,
        })
    return output


async def build_squad_response(viewer: TelegramViewer) -> dict[str, Any]:
    squad = await _owned_squad(viewer.id)
    pack_cards = await _pack_owned_cards(int(viewer.id))
    lineup_ids = await _effective_lineup_ids(int(viewer.id), squad)
    lineup_set = set(lineup_ids)
    players = []
    for player in squad:
        squad_player_id = int(player.get("player_id") or 0)
        players.append(
            _player_payload(player, owned=True)
            | {
                "squad_player_id": squad_player_id,
                "in_playing_xi": squad_player_id in lineup_set,
                "is_captain": squad_player_id == int(player.get("captain_player_id") or 0),
            }
        )
    return {
        "count": len(squad),
        "capacity": MAX_SQUAD_SIZE,
        "players": players,
        "lineup_player_ids": lineup_ids,
        "playing_xi_count": len(lineup_ids),
        "substitute_count": max(0, len(squad) - len(lineup_ids)),
        "pack_cards": pack_cards,
        "pack_card_count": len(pack_cards),
    }


async def build_news_list_response(viewer: TelegramViewer, limit: int = 30) -> dict[str, Any]:
    """Return recently published club news for the authenticated Mini App viewer."""
    del viewer  # Access control is enforced by the ASGI router; articles are club-wide.
    bounded_limit = max(1, min(int(limit or 30), 50))
    rows = await fetch(
        """
        SELECT article_id,title,description,created_at
        FROM miniapp_news_articles WHERE is_published=TRUE
        ORDER BY created_at DESC, article_id DESC LIMIT $1;
        """,
        bounded_limit,
    )
    articles = []
    for row in rows:
        description = str(row.get("description") or "")
        excerpt = re.sub(r"\s+", " ", description).strip()
        if len(excerpt) > 190:
            excerpt = excerpt[:187].rstrip() + "…"
        article_id = int(row.get("article_id") or 0)
        articles.append({
            "article_id": article_id,
            "title": str(row.get("title") or "News update"),
            "excerpt": excerpt,
            "created_at": _iso(row.get("created_at")),
            "image_url": f"/api/image/news?id={article_id}",
        })
    return {"articles": articles, "count": len(articles)}


async def build_news_detail_response(viewer: TelegramViewer, article_id: int) -> dict[str, Any] | None:
    """Return one published article, excluding its private Telegram file ID."""
    del viewer
    try:
        article_id = int(article_id)
    except (TypeError, ValueError):
        return None
    if article_id <= 0:
        return None
    row = await fetchrow(
        """
        SELECT article_id,title,description,created_at
        FROM miniapp_news_articles WHERE article_id=$1 AND is_published=TRUE;
        """,
        article_id,
    )
    if not row:
        return None
    article_id = int(row.get("article_id") or 0)
    return {
        "article_id": article_id,
        "title": str(row.get("title") or "News update"),
        "description": str(row.get("description") or ""),
        "created_at": _iso(row.get("created_at")),
        "image_url": f"/api/image/news?id={article_id}",
    }


async def news_image_bytes(article_id: int) -> tuple[bytes, str] | None:
    """Resolve an admin-published article's Telegram file ID and proxy its image."""
    if not BOT_TOKEN:
        return None
    row = await fetchrow(
        "SELECT image_file_id FROM miniapp_news_articles WHERE article_id=$1 AND is_published=TRUE;",
        int(article_id),
    )
    if not row or not row.get("image_file_id"):
        return None
    return await _download_telegram_image(str(row["image_file_id"]))


async def swap_playing_xi_players(viewer: TelegramViewer, playing_player_id: int, substitute_player_id: int) -> dict[str, Any]:
    """Swap a squad member with a substitute in the persisted bot lineup."""
    playing_player_id = int(playing_player_id)
    substitute_player_id = int(substitute_player_id)
    if not playing_player_id or not substitute_player_id or playing_player_id == substitute_player_id:
        return {"status": "invalid_selection"}
    async with acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            if not user:
                return {"status": "user_missing"}
            squad_row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            squad = _squad(squad_row["squad"] if squad_row else None)
            squad_ids = _player_id_set(squad)
            if playing_player_id not in squad_ids or substitute_player_id not in squad_ids:
                return {"status": "player_not_owned"}
            if len(squad) < 11:
                return {"status": "squad_too_small", "count": len(squad)}
            lineup_row = await conn.fetchrow("SELECT player_ids FROM team_lineups WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            lineup_ids = _normalize_lineup_ids(lineup_row["player_ids"] if lineup_row else None, squad)
            if playing_player_id not in lineup_ids:
                return {"status": "player_not_in_xi"}
            if substitute_player_id in lineup_ids:
                return {"status": "player_already_in_xi"}
            # Mirror /changeXL: swap the incoming player's squad position with
            # the outgoing one as well as updating the authoritative XI IDs.
            # The bot's /squad numbering and XI management therefore stay aligned.
            playing_index = next(i for i, item in enumerate(squad) if int(item.get("player_id") or 0) == playing_player_id)
            substitute_index = next(i for i, item in enumerate(squad) if int(item.get("player_id") or 0) == substitute_player_id)
            updated_squad = list(squad)
            updated_squad[playing_index], updated_squad[substitute_index] = updated_squad[substitute_index], updated_squad[playing_index]
            await conn.execute(
                "UPDATE team_squads SET squad=$2::jsonb,updated_at=NOW() WHERE user_id=$1;",
                int(viewer.id), json.dumps(updated_squad, default=str),
            )
            lineup_ids[lineup_ids.index(playing_player_id)] = substitute_player_id
            await _save_lineup_ids_conn(conn, int(viewer.id), lineup_ids)
    try:
        from services.quest_engine import record_quest_event
        await record_quest_event(
            int(viewer.id), "PLAYING_XI_CONFIRMED",
            metadata={"source": "miniapp", "playing_player_id": substitute_player_id, "substituted_player_id": playing_player_id},
        )
    except Exception as exc:
        print(f"[miniapp_backend] lineup quest event failed: {type(exc).__name__}")
    return {"status": "success", "lineup_player_ids": lineup_ids}


async def update_playing_xi_order(viewer: TelegramViewer, player_ids: list[Any]) -> dict[str, Any]:
    """Persist an XI reorder without changing which eleven players are selected."""
    try:
        requested = [int(value) for value in player_ids]
    except (TypeError, ValueError):
        return {"status": "invalid_order"}
    if len(requested) != 11 or len(set(requested)) != 11 or any(value == 0 for value in requested):
        return {"status": "invalid_order"}

    async with acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            if not user:
                return {"status": "user_missing"}
            squad_row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            squad = _squad(squad_row["squad"] if squad_row else None)
            squad_ids = _player_id_set(squad)
            if len(squad_ids) < 11 or not set(requested).issubset(squad_ids):
                return {"status": "player_not_owned"}
            lineup_row = await conn.fetchrow("SELECT player_ids FROM team_lineups WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            current = _normalize_lineup_ids(lineup_row["player_ids"] if lineup_row else None, squad)
            if len(current) != 11 or set(current) != set(requested):
                return {"status": "lineup_changed", "lineup_player_ids": current}
            # Keep the bot's existing squad numbering/order aligned with the XI:
            # first eleven cards are now in the saved Playing XI order, followed
            # by substitutes in their previous relative order.
            requested_set = set(requested)
            squad_by_id = {int(item.get("player_id") or 0): dict(item) for item in squad}
            ordered_xi = [squad_by_id[player_id] for player_id in requested]
            substitutes = [dict(item) for item in squad if int(item.get("player_id") or 0) not in requested_set]
            await conn.execute(
                "UPDATE team_squads SET squad=$2::jsonb,updated_at=NOW() WHERE user_id=$1;",
                int(viewer.id), json.dumps(ordered_xi + substitutes, default=str),
            )
            await _save_lineup_ids_conn(conn, int(viewer.id), requested)
    try:
        from services.quest_engine import record_quest_event
        await record_quest_event(int(viewer.id), "PLAYING_XI_ORDER_UPDATED", metadata={"source": "miniapp", "player_ids": requested})
    except Exception as exc:
        print(f"[miniapp_backend] lineup-order quest event failed: {type(exc).__name__}")
    return {"status": "success", "lineup_player_ids": requested}


async def sell_squad_players(viewer: TelegramViewer, player_ids: list[Any]) -> dict[str, Any]:
    """Atomically sell selected substitutes at the bot's resale prices."""
    try:
        selected_ids = [int(value) for value in player_ids]
    except (TypeError, ValueError):
        return {"status": "invalid_selection"}
    if not selected_ids or any(value == 0 for value in selected_ids) or len(set(selected_ids)) != len(selected_ids):
        return {"status": "invalid_selection"}

    sold_players: list[dict[str, Any]] = []
    total_sell_price = 0
    remaining_count = 0
    balance = 0
    async with acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT balance FROM users WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            if not user:
                return {"status": "user_missing"}
            squad_row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            squad = _squad(squad_row["squad"] if squad_row else None)
            by_id = {int(item.get("player_id") or 0): item for item in squad if int(item.get("player_id") or 0)}
            if any(player_id not in by_id for player_id in selected_ids):
                return {"status": "player_not_owned"}
            lineup_row = await conn.fetchrow("SELECT player_ids FROM team_lineups WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            lineup_ids = _normalize_lineup_ids(lineup_row["player_ids"] if lineup_row else None, squad)
            if any(player_id in lineup_ids for player_id in selected_ids):
                return {"status": "playing_xi_player_selected"}
            if len(squad) - len(selected_ids) < 11:
                return {"status": "minimum_squad_size", "count": len(squad), "minimum": 11}

            for player_id in selected_ids:
                player = dict(by_id[player_id])
                overall = max(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))
                sale_price = int(get_price(overall)[1])
                total_sell_price += sale_price
                sold_players.append({"player_id": player_id, "name": str(player.get("name") or "Player"), "sell_price": sale_price})

            selected_set = set(selected_ids)
            remaining_squad = [dict(item) for item in squad if int(item.get("player_id") or 0) not in selected_set]
            # The UI only presents substitutes for bulk-sale, so the XI remains unchanged.
            await conn.execute(
                "UPDATE team_squads SET squad=$2::jsonb,updated_at=NOW() WHERE user_id=$1;",
                int(viewer.id), json.dumps(remaining_squad, default=str),
            )
            await conn.execute(
                "UPDATE users SET balance=COALESCE(balance,0)+$1,last_seen_at=NOW() WHERE user_id=$2;",
                total_sell_price, int(viewer.id),
            )
            remaining_count = len(remaining_squad)
            balance = int(user.get("balance") or 0) + total_sell_price

    for player_id in selected_ids:
        try:
            from database.player_user_stats_repo import reset_player_user_stats
            await reset_player_user_stats(int(viewer.id), int(player_id))
        except Exception as exc:
            print(f"[miniapp_backend] sold-player stats reset failed: {type(exc).__name__}")
        try:
            from services.quest_engine import record_quest_event
            await record_quest_event(int(viewer.id), "PLAYER_SOLD", metadata={"player_id": int(player_id), "source": "miniapp_bulk"})
        except Exception as exc:
            print(f"[miniapp_backend] bulk-sale quest event failed: {type(exc).__name__}")
    return {
        "status": "success",
        "sold_players": sold_players,
        "sold_count": len(sold_players),
        "total_sell_price": total_sell_price,
        "balance": balance,
        "count": remaining_count,
    }


async def sell_squad_player(viewer: TelegramViewer, player_id: int) -> dict[str, Any]:
    """Sell an owned squad card atomically, using the same price chart as /sell."""
    player_id = int(player_id)
    if not player_id:
        return {"status": "invalid_selection"}
    player_payload = None
    sell_price = 0
    count = 0
    balance = 0
    async with acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT balance FROM users WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            if not user:
                return {"status": "user_missing"}
            squad_row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            squad = _squad(squad_row["squad"] if squad_row else None)
            index = next((i for i, item in enumerate(squad) if int(item.get("player_id") or 0) == player_id), None)
            if index is None:
                return {"status": "player_not_owned"}
            if len(squad) <= 11:
                return {"status": "minimum_squad_size", "count": len(squad), "minimum": 11}
            player = dict(squad[index])
            overall = max(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))
            sell_price = int(get_price(overall)[1])
            player_payload = _player_payload(player, owned=True)
            remaining_squad = squad[:index] + squad[index + 1:]
            remaining_ids = _player_id_set(remaining_squad)
            lineup_row = await conn.fetchrow("SELECT player_ids FROM team_lineups WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            current_lineup = _normalize_lineup_ids(lineup_row["player_ids"] if lineup_row else None, squad)
            # If a sold player was in the XI, refill that XI slot from the bench.
            next_lineup = [pid for pid in current_lineup if pid in remaining_ids]
            for item in remaining_squad:
                pid = int(item.get("player_id") or 0)
                if pid and pid not in next_lineup:
                    next_lineup.append(pid)
                if len(next_lineup) >= min(11, len(remaining_squad)):
                    break
            next_lineup = next_lineup[:11]
            await conn.execute(
                "UPDATE team_squads SET squad=$2::jsonb,updated_at=NOW() WHERE user_id=$1;",
                int(viewer.id), json.dumps(remaining_squad, default=str),
            )
            await _save_lineup_ids_conn(conn, int(viewer.id), next_lineup)
            await conn.execute(
                "UPDATE users SET balance=COALESCE(balance,0)+$1,last_seen_at=NOW() WHERE user_id=$2;",
                sell_price, int(viewer.id),
            )
            balance = int(user.get("balance") or 0) + sell_price
            count = len(remaining_squad)
    try:
        from database.player_user_stats_repo import reset_player_user_stats
        await reset_player_user_stats(int(viewer.id), int(player_id))
    except Exception as exc:
        print(f"[miniapp_backend] sold-player stats reset failed: {type(exc).__name__}")
    try:
        from services.quest_engine import record_quest_event
        await record_quest_event(int(viewer.id), "PLAYER_SOLD", metadata={"player_id": player_id, "source": "miniapp"})
    except Exception as exc:
        print(f"[miniapp_backend] player-sale quest event failed: {type(exc).__name__}")
    return {"status": "success", "player": player_payload, "sell_price": sell_price, "balance": balance, "count": count}


async def sell_pack_collection_card(viewer: TelegramViewer, card_id: int) -> dict[str, Any]:
    """Sell a separately-owned pack reward without changing the active squad."""
    card_id = int(card_id)
    if card_id <= 0:
        return {"status": "invalid_selection"}
    sold_player: dict[str, Any] | None = None
    sell_price = 0
    balance = 0
    async with acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT balance FROM users WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            if not user:
                return {"status": "user_missing"}
            row = await conn.fetchrow(
                """
                SELECT pci.card_id,pci.player_id,pci.position,po.cards
                FROM pack_card_inventory pci
                JOIN pack_openings po ON po.opening_id=pci.opening_id
                WHERE pci.card_id=$1 AND pci.user_id=$2
                FOR UPDATE OF pci;
                """,
                card_id, int(viewer.id),
            )
            if not row:
                return {"status": "card_not_owned"}
            cards = _json_value(row.get("cards"), [])
            position = int(row.get("position") or 0)
            if not isinstance(cards, list) or position < 1 or position > len(cards):
                return {"status": "card_data_unavailable"}
            saved_card = cards[position - 1]
            if not isinstance(saved_card, dict) or not isinstance(saved_card.get("player"), dict):
                return {"status": "card_data_unavailable"}
            player = dict(saved_card["player"])
            if int(player.get("player_id") or saved_card.get("player_id") or 0) != int(row.get("player_id") or 0):
                return {"status": "card_data_unavailable"}
            overall = int(saved_card.get("ovr") or max(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0)))
            sell_price = int(get_price(overall)[1])
            sold_player = _player_payload(player, owned=True)
            await conn.execute("DELETE FROM pack_card_inventory WHERE card_id=$1 AND user_id=$2;", card_id, int(viewer.id))
            await conn.execute(
                "UPDATE users SET balance=COALESCE(balance,0)+$1,last_seen_at=NOW() WHERE user_id=$2;",
                sell_price, int(viewer.id),
            )
            balance = int(user.get("balance") or 0) + sell_price
    return {"status": "success", "player": sold_player, "sell_price": sell_price, "balance": balance, "card_id": card_id}


async def build_leaderboard_response(viewer: TelegramViewer, limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    rows = await fetch(
        """
        SELECT u.user_id,u.username,u.first_name,COALESCE(u.level,1) AS level,
               COALESCE(u.xp,0) AS xp,COALESCE(ps.matches,0) AS matches,
               COALESCE(ps.wins,0) AS wins,COALESCE(ps.losses,0) AS losses
        FROM users u LEFT JOIN player_stats ps ON ps.user_id=u.user_id
        ORDER BY COALESCE(u.level,1) DESC,COALESCE(u.xp,0) DESC,u.user_id ASC LIMIT $1;
        """, limit,
    )
    result = []
    for position, row in enumerate(rows, start=1):
        item = dict(row)
        matches = int(item["matches"] or 0)
        result.append({
            "rank": position, "user_id": int(item["user_id"]),
            "name": " ".join(filter(None, [item.get("first_name"), f"@{item['username']}" if item.get("username") else None])) or "Crickium Player",
            "username": item.get("username"), "level": int(item["level"] or 1),
            "xp": int(item["xp"] or 0), "matches": matches,
            "wins": int(item["wins"] or 0), "losses": int(item["losses"] or 0),
            "win_percentage": round(int(item["wins"] or 0) * 100 / matches, 1) if matches else 0.0,
            "is_viewer": int(item["user_id"]) == int(viewer.id),
        })
    return {"entries": result, "viewer_rank": await _rank_for_user(viewer.id), "scope": "global"}


async def build_matches_response(viewer: TelegramViewer) -> dict[str, Any]:
    recent = await fetch(
        """
        SELECT engine,match_id,match_type,won,pitch,target,batting_runs,bowling_wickets,played_at
        FROM quest_match_summaries WHERE user_id=$1 ORDER BY played_at DESC LIMIT 50;
        """, int(viewer.id),
    )
    active = await _active_matches(viewer.id, limit=30)

    # The summary table is deliberately compact and does not contain team labels.
    # Enrich history in one bounded query per engine rather than issuing one query
    # per card (which would create avoidable database load on each Matches refresh).
    recent_dicts = [dict(raw) for raw in recent]
    ids_by_engine: dict[str, set[int]] = {}
    for row in recent_dicts:
        engine = str(row.get("engine") or "MATCH").upper()
        try:
            match_id = int(row.get("match_id") or 0)
        except (TypeError, ValueError):
            continue
        if match_id > 0 and engine in MATCH_SOURCE_TABLES:
            ids_by_engine.setdefault(engine, set()).add(match_id)
    source_rows: dict[tuple[str, int], dict[str, Any]] = {}
    for engine, match_ids in ids_by_engine.items():
        table, id_column = MATCH_SOURCE_TABLES[engine]
        try:
            rows = await fetch(
                f"SELECT * FROM {table} WHERE {id_column}=ANY($1::bigint[]) AND (challenger_id=$2 OR opponent_id=$2);",
                sorted(match_ids), int(viewer.id),
            )
            for source_row in rows:
                source = dict(source_row)
                try:
                    source_id = int(source.get(id_column) or source.get("match_id") or source.get("challenge_id") or 0)
                except (TypeError, ValueError):
                    continue
                source_rows[(engine, source_id)] = source
        except Exception as exc:
            print(f"[miniapp_backend] recent match enrichment unavailable engine={engine} type={type(exc).__name__}")

    recent_items: list[dict[str, Any]] = []
    for row in recent_dicts:
        engine = str(row.get("engine") or "MATCH").upper()
        match_id = int(row.get("match_id") or 0)
        item = _match_summary(row)
        item.update({"engine": engine, "match_id": match_id, "status": "completed",
                     "is_live": False, "is_completed": True,
                     "current_score": None, "over_text": None})
        source = source_rows.get((engine, match_id))
        if source:
            first, second = _team_names_from_match(source)
            item.update({
                "team_a_name": first, "team_b_name": second,
                "challenger_name": str(source.get("challenger_name") or first),
                "opponent_name": str(source.get("opponent_name") or second),
                "match_type": row.get("match_type") or source.get("format") or source.get("match_type") or engine,
                "created_at": _iso(source.get("created_at")),
            })
        else:
            # Don't substitute invented teams when older source rows no longer exist.
            item.update({"team_a_name": "Team details unavailable", "team_b_name": "Team details unavailable"})
        recent_items.append(item)
    return {
        "recent": recent_items,
        "active": active,
        "note": "Only stored Crickium match records are shown. Live details require an available current-session snapshot; completed details use persisted summaries. Missing scores and team names are not invented.",
    }


def _as_mapping(value: Any) -> dict[str, Any]:
    decoded = _json_value(value, {})
    return dict(decoded) if isinstance(decoded, dict) else {}


def _json_list(value: Any) -> list[dict[str, Any]]:
    decoded = _json_value(value, [])
    return [dict(item) for item in decoded if isinstance(item, dict)] if isinstance(decoded, list) else []


def _iso(value: Any) -> str | None:
    try:
        return value.isoformat() if value else None
    except Exception:
        return str(value) if value else None


def _overs_from_balls(balls: Any) -> str:
    value = max(0, int(balls or 0))
    return f"{value // 6}.{value % 6}"


def _simple_batter(batter: dict[str, Any], striker_name: str | None = None, non_striker_name: str | None = None) -> dict[str, Any]:
    name = str(batter.get("name") or "Player")
    runs = int(batter.get("runs") or 0)
    balls = int(batter.get("balls") or 0)
    dismissed = bool(batter.get("dismissed"))
    appeared = bool(batter.get("appeared")) or balls > 0 or runs > 0 or dismissed or name in {striker_name, non_striker_name}
    status = "out" if dismissed else "at crease" if name in {striker_name, non_striker_name} else "not out" if appeared else "yet to bat"
    fours = int(batter.get("fours") or batter.get("4s") or 0)
    sixes = int(batter.get("sixes") or batter.get("6s") or 0)
    return {
        "name": name, "runs": runs, "balls": balls, "fours": fours, "sixes": sixes,
        "strike_rate": round(runs * 100 / balls, 1) if balls else 0.0,
        "dismissed": dismissed, "dismissal_text": batter.get("dismissal_text"),
        "status": status,
    }


def _simple_bowler(bowler: dict[str, Any], current_bowler_name: str | None = None) -> dict[str, Any]:
    balls = int(bowler.get("balls") or 0)
    runs = int(bowler.get("runs") or 0)
    wickets = int(bowler.get("wickets") or 0)
    name = str(bowler.get("name") or "Bowler")
    return {
        "name": name, "balls": balls, "overs": _overs_from_balls(balls),
        "maidens": int(bowler.get("maidens") or 0), "runs": runs, "wickets": wickets,
        "economy": round(runs * 6 / balls, 2) if balls else 0.0,
        "is_current": name == current_bowler_name,
    }


async def _source_match_row(engine: str, match_id: int, user_id: int) -> dict[str, Any] | None:
    spec = MATCH_SOURCE_TABLES.get(str(engine or "").upper())
    if not spec:
        return None
    table, id_column = spec
    try:
        row = await fetchrow(
            f"SELECT * FROM {table} WHERE {id_column}=$1 AND (challenger_id=$2 OR opponent_id=$2);",
            int(match_id), int(user_id),
        )
        return dict(row) if row else None
    except Exception as exc:
        print(f"[miniapp_backend] match detail source unavailable engine={str(engine).upper()} type={type(exc).__name__}")
        return None


def _attach_live_session_fields(payload: dict[str, Any], session: Any) -> dict[str, Any]:
    innings_obj = getattr(session, "innings", None)
    if innings_obj is None:
        return payload
    striker = getattr(innings_obj, "striker", None)
    non_striker = getattr(innings_obj, "non_striker", None)

    def slot(value: Any, status: str) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            "name": str(getattr(value, "name", None) or "Player"),
            "runs": int(getattr(value, "runs", 0) or 0),
            "balls": int(getattr(value, "balls", 0) or 0),
            "fours": int(getattr(value, "fours", 0) or 0),
            "sixes": int(getattr(value, "sixes", 0) or 0),
            "dismissed": bool(getattr(value, "dismissed", False)),
            "dismissal_text": getattr(value, "dismissal_text", None),
            "strike_rate": round(int(getattr(value, "runs", 0) or 0) * 100 / int(getattr(value, "balls", 0) or 1), 1) if int(getattr(value, "balls", 0) or 0) else 0.0,
            "status": status,
        }

    pair = []
    if striker is not None:
        pair.append(slot(striker, "on strike"))
    if non_striker is not None:
        pair.append(slot(non_striker, "non-striker"))
    bowler = getattr(session, "current_bowler", None) or {}
    bowler = dict(bowler) if isinstance(bowler, dict) else {}
    bowler_stats_map = getattr(session, "bowler_stats", {}) or {}
    bowler_id = bowler.get("player_id")
    figures = (bowler_stats_map.get(int(bowler_id or 0)) or bowler_stats_map.get(str(bowler_id or 0)) or {}) if bowler_id is not None else {}
    if bowler:
        payload["current_bowler"] = _simple_bowler({"name": bowler.get("name") or "Bowler", **dict(figures)}, str(bowler.get("name") or "Bowler"))
    else:
        payload["current_bowler"] = None
    payload["current_batting_pair"] = [item for item in pair if item]
    payload["partnership"] = {
        "runs": int(getattr(session, "partnership_runs", 0) or 0),
        "balls": int(getattr(session, "partnership_balls", 0) or 0),
    }
    payload["this_over"] = list(getattr(session, "this_over", []) or [])
    payload["current_innings_number"] = int(getattr(innings_obj, "innings_number", 1) or 1)
    return payload


async def _live_engine_payload(engine: str, match_id: int, match_row: dict[str, Any], status: str) -> dict[str, Any] | None:
    engine = str(engine or "").upper()
    try:
        from services.match_analysis.collector import collect_runtime_session, collect_playso_match
        if engine == "PLAYSO":
            state = _as_mapping(match_row.get("state"))
            if not state:
                return None
            payload = collect_playso_match(match_row, state, termination="live")
            selected = {int(item.get("player_id") or 0): item for item in (state.get("batting_xi") or []) if isinstance(item, dict)}
            striker_id = int(state.get("current_striker_id") or 0)
            non_id = int(state.get("current_non_striker_id") or 0)
            stats = state.get("batter_stats") or {}
            pair = []
            for player_id, tag in ((striker_id, "on strike"), (non_id, "non-striker")):
                if not player_id:
                    continue
                player = selected.get(player_id, {})
                stat = dict(stats.get(str(player_id)) or {})
                item = _simple_batter({"name": player.get("name") or stat.get("name"), **stat, "appeared": str(player_id) in stats})
                item["status"] = tag
                item["player_id"] = player_id
                pair.append(item)

            selected_bowler_id = int(state.get("selected_bowler") or 0)
            bowling_xi = [item for item in (state.get("bowling_xi") or []) if isinstance(item, dict)]
            bowler_stats = state.get("bowler_stats") or {}
            bowler_row = next((item for item in bowling_xi if int(item.get("player_id") or 0) == selected_bowler_id), {})
            bowl_stat = dict(bowler_stats.get(str(selected_bowler_id)) or {})
            current_bowler_name = str(bowler_row.get("name") or bowl_stat.get("name") or "")
            current_bowler = _simple_bowler({"name": current_bowler_name, **bowl_stat}, current_bowler_name) if bowler_row or bowl_stat else None

            # The live Super Over state keeps statistics only for batters who
            # have faced a delivery. Expand that to the selected batting XI so
            # the scorecard can correctly distinguish yet-to-bat players.
            wicket_names = {str(x.get("batter") or x.get("name") or "") for x in (state.get("wickets_fallen") or []) if isinstance(x, dict)}
            full_batters = []
            for player_id, player in selected.items():
                name = str(player.get("name") or (stats.get(str(player_id)) or {}).get("name") or "Player")
                stat = dict(stats.get(str(player_id)) or {})
                raw_batter = {"name": name, **stat,
                              "dismissed": bool(stat.get("dismissed") or name in wicket_names),
                              "appeared": str(player_id) in stats}
                formatted = _simple_batter(raw_batter, name if player_id == striker_id else None,
                                            name if player_id == non_id else None)
                formatted["player_id"] = player_id
                full_batters.append(formatted)

            full_bowlers = []
            for player in bowling_xi:
                player_id = int(player.get("player_id") or 0)
                stat = dict(bowler_stats.get(str(player_id)) or {})
                formatted = _simple_bowler({"name": player.get("name") or stat.get("name") or "Bowler", **stat}, current_bowler_name)
                formatted["player_id"] = player_id
                full_bowlers.append(formatted)

            current_no = int(state.get("innings_no") or match_row.get("innings_no") or 1)
            for inning in (payload.get("innings") or []):
                if int(inning.get("innings_number") or 1) == current_no:
                    inning["batters"] = full_batters
                    inning["bowlers"] = full_bowlers
                    inning["current_batting_pair"] = pair
                    inning["partnership"] = None
                    inning["current_bowler"] = current_bowler
                    break
            payload["current_batting_pair"] = pair
            payload["current_bowler"] = current_bowler
            payload["partnership"] = None
            payload["current_innings_number"] = current_no
            current_balls = [ball for ball in (state.get("analysis_balls") or []) if isinstance(ball, dict) and int(ball.get("innings_number") or 1) == current_no]
            payload["this_over"] = [str(ball.get("symbol") or ball.get("outcome") or ball.get("runs") or "•") for ball in current_balls[-6:]]
            return payload

        session = await _runtime_session(engine, match_id)
        if session is None:
            return None
        payload = collect_runtime_session(engine, session, termination="live")
        _attach_live_session_fields(payload, session)
        return payload
    except Exception as exc:
        print(f"[miniapp_backend] live detail unavailable engine={engine} id={int(match_id)} type={type(exc).__name__}")
        return None


def _summary_innings_from_rows(rows: list[dict[str, Any]], source_match: dict[str, Any] | None, engine: str, match_id: int) -> list[dict[str, Any]]:
    # Completed summaries persist per-user batting stats and each user's bowling
    # figures. Pair the defending/chasing rows to recover both innings without
    # fabricating delivery-level commentary that is not stored in this table.
    defending = next((r for r in rows if bool(r.get("defending"))), None)
    chasing = next((r for r in rows if bool(r.get("chasing"))), None)
    innings_out = []
    def team_name(user_id: int, fallback: str) -> str:
        if source_match:
            if int(source_match.get("challenger_id") or 0) == int(user_id):
                return str(source_match.get("challenger_team_name") or source_match.get("challenger_team_code") or source_match.get("challenger_name") or fallback)
            if int(source_match.get("opponent_id") or 0) == int(user_id):
                return str(source_match.get("opponent_team_name") or source_match.get("opponent_team_code") or source_match.get("opponent_name") or fallback)
        return fallback
    for number, batting_row, bowling_row in ((1, defending, chasing), (2, chasing, defending)):
        if not batting_row:
            continue
        user_id = int(batting_row.get("user_id") or 0)
        batting = _json_list(batting_row.get("batting"))
        bowling = _json_list(bowling_row.get("bowling")) if bowling_row else []
        balls_history = _json_list(batting_row.get("over_history"))
        inning_over_rows = []
        for over in balls_history:
            if int(over.get("innings_number") or 0) != number:
                continue
            if user_id and int(over.get("batting_team_id") or 0) not in {0, user_id}:
                continue
            inning_over_rows.append({
                "over": int(over.get("over_number") or 0), "runs": int(over.get("runs") or 0),
                "wickets": int(over.get("wickets") or 0), "legal_balls": int(over.get("balls") or 0),
                "balls": [],
            })
        legal_balls = sum(int(x.get("legal_balls") or 0) for x in inning_over_rows)
        runs = int(batting_row.get("batting_runs") or 0)
        wickets = sum(1 for x in batting if bool(x.get("dismissed")))
        if inning_over_rows:
            wickets = sum(int(x.get("wickets") or 0) for x in inning_over_rows)
        batters = [_simple_batter(item) for item in batting]
        bowlers = [_simple_bowler(item) for item in bowling]
        innings_out.append({
            "innings_number": number,
            "batting_team_id": user_id,
            "bowling_team_id": int((bowling_row or {}).get("user_id") or 0),
            "batting_team_display": team_name(user_id, "Chasing side" if number == 2 else "Defending side"),
            "bowling_team_display": team_name(int((bowling_row or {}).get("user_id") or 0), "Bowling side"),
            "runs": runs, "wickets": wickets, "legal_balls": legal_balls,
            "over_text": _overs_from_balls(legal_balls),
            "target": int(batting_row.get("target") or 0) or None,
            "batters": batters, "bowlers": bowlers,
            "analysis_overs": inning_over_rows, "analysis_balls": [],
            "extras": {}, "source": "stored match summary",
        })
    return innings_out


async def build_match_detail(viewer: TelegramViewer, engine: str, match_id: int) -> dict[str, Any] | None:
    engine = str(engine or "").upper()
    try:
        match_id = int(match_id)
    except (TypeError, ValueError):
        return None
    if engine not in MATCH_SOURCE_TABLES:
        return None

    # Gate all direct/guessable match IDs through participant ownership before
    # returning the match record or its snapshots.
    source_match = await _source_match_row(engine, match_id, int(viewer.id))
    summary = await fetchrow(
        """
        SELECT * FROM quest_match_summaries
        WHERE user_id=$1 AND LOWER(engine)=LOWER($2) AND match_id=$3
        ORDER BY played_at DESC LIMIT 1;
        """, int(viewer.id), engine, match_id,
    )

    active_status = str((source_match or {}).get("status") or "").lower()
    if source_match and active_status not in _TERMINAL_MATCH_STATUSES:
        live_payload = await _live_engine_payload(engine, match_id, source_match, active_status)
        if live_payload:
            first, second = _team_names_from_match(source_match)
            innings = live_payload.get("innings") or []
            current_no = int(live_payload.get("current_innings_number") or (innings[-1].get("innings_number") if innings else 1) or 1)
            current = next((item for item in innings if int(item.get("innings_number") or 1) == current_no), innings[-1] if innings else {})
            balls = int(current.get("legal_balls") or 0)
            live_payload.update({
                "engine": engine, "match_id": match_id,
                "match_type": source_match.get("format") or source_match.get("match_type") or engine,
                "status": active_status or "live", "is_live": active_status in {"live", "playing", "innings_break", "chase", "in_progress"},
                "is_completed": False, "team_a_name": first, "team_b_name": second,
                "challenger_name": str(source_match.get("challenger_name") or first),
                "opponent_name": str(source_match.get("opponent_name") or second),
                "pitch": source_match.get("pitch"), "stadium": source_match.get("stadium"),
                "created_at": _iso(source_match.get("created_at")), "played_at": None,
                "current_innings_number": current_no, "runs": int(current.get("runs") or 0),
                "wickets": int(current.get("wickets") or 0), "over_text": current.get("over_text") or _overs_from_balls(balls),
                "target": current.get("target"), "scorecard_available": bool(current),
                "result_text": None, "winner_name": None,
            })
            return live_payload

    if summary:
        # Existing score summaries are the entitlement check; only then load the
        # other participant's summary, if present, to reconstruct both innings.
        summary_rows = await fetch(
            "SELECT user_id,engine,match_id,won,chasing,defending,target,batting_runs,bowling_wickets,batting,bowling,over_history,played_at FROM quest_match_summaries WHERE LOWER(engine)=LOWER($1) AND match_id=$2 ORDER BY played_at DESC;",
            engine, match_id,
        )
        cleaned = [dict(row) for row in summary_rows]
        for row in cleaned:
            row["batting"] = _json_list(row.get("batting"))
            row["bowling"] = _json_list(row.get("bowling"))
            row["over_history"] = _json_list(row.get("over_history"))
        innings = _summary_innings_from_rows(cleaned, source_match, engine, match_id)
        user_summary = dict(summary)
        user_won = bool(user_summary.get("won"))
        winner_name = None
        for row in cleaned:
            if bool(row.get("won")):
                uid = int(row.get("user_id") or 0)
                if source_match:
                    if uid == int(source_match.get("challenger_id") or 0):
                        winner_name = source_match.get("challenger_team_name") or source_match.get("challenger_name")
                    elif uid == int(source_match.get("opponent_id") or 0):
                        winner_name = source_match.get("opponent_team_name") or source_match.get("opponent_name")
                if not winner_name:
                    winner_name = "Winner" if uid != 0 else None
                break
        first, second = _team_names_from_match(source_match or {})
        result_text = None
        if user_won and winner_name:
            result_text = f"{winner_name} won"
        elif cleaned and any(bool(row.get("won")) for row in cleaned):
            result_text = f"{winner_name} won" if winner_name else "Result recorded"
        elif cleaned:
            result_text = "Result recorded; winner unavailable"
        return {
            "engine": engine, "match_id": match_id,
            "match_type": user_summary.get("match_type") or engine,
            "status": "completed", "is_live": False, "is_completed": True,
            "team_a_name": first, "team_b_name": second,
            "challenger_name": str((source_match or {}).get("challenger_name") or first),
            "opponent_name": str((source_match or {}).get("opponent_name") or second),
            "pitch": user_summary.get("pitch") or (source_match or {}).get("pitch"),
            "stadium": (source_match or {}).get("stadium"),
            "created_at": _iso((source_match or {}).get("created_at")),
            "played_at": _iso(user_summary.get("played_at")),
            "innings": innings, "scorecard_available": bool(innings),
            "current_innings_number": innings[-1].get("innings_number") if innings else None,
            "result_text": result_text, "winner_name": winner_name,
            "won": user_won, "target": user_summary.get("target"),
            "runs": int(user_summary.get("batting_runs") or 0),
            "wickets": int(user_summary.get("bowling_wickets") or 0),
            "over_text": innings[-1].get("over_text") if innings else None,
            "note": "Completed match details are reconstructed only from the summaries stored by Crickium. Per-ball commentary is shown only where the engine persisted delivery records.",
        }

    if not source_match:
        return None
    first, second = _team_names_from_match(source_match)
    status = str(source_match.get("status") or "unknown")
    return {
        "engine": engine, "match_id": match_id,
        "match_type": source_match.get("format") or source_match.get("match_type") or engine,
        "status": status, "is_live": status.lower() in {"live", "playing", "innings_break", "chase", "in_progress"},
        "is_completed": status.lower() in _TERMINAL_MATCH_STATUSES,
        "team_a_name": first, "team_b_name": second,
        "challenger_name": str(source_match.get("challenger_name") or first),
        "opponent_name": str(source_match.get("opponent_name") or second),
        "pitch": source_match.get("pitch"), "stadium": source_match.get("stadium"),
        "created_at": _iso(source_match.get("created_at")), "played_at": None,
        "innings": [], "scorecard_available": False, "result_text": None, "winner_name": None,
        "note": "The match exists, but this engine has not persisted a detailed scorecard for this record. No score or commentary has been fabricated.",
    }


async def build_quests_response(viewer: TelegramViewer) -> dict[str, Any]:
    from services.quest_engine import get_quest_view
    output = {}
    for period_type in ("daily", "weekly", "monthly"):
        try:
            period, tasks, completed = await get_quest_view(int(viewer.id), period_type)
            output[period_type] = {
                "period_key": str(period["period_key"]),
                "ends_at": period["end_at"].isoformat() if period.get("end_at") else None,
                "tasks": [
                    {
                        "id": str(task["id"]), "title": task["title"],
                        "description": task["description"], "difficulty": task["difficulty"],
                        "category": task["category"], "target": int(task["target"] or 0),
                        "completed": str(task["id"]) in completed,
                        "reward": {"sigils": int(task.get("sigils") or 0), "coins": int(task.get("coins") or 0), "rubies": int(task.get("rubies") or 0)},
                    }
                    for task in tasks
                ],
            }
        except Exception as exc:
            print(f"[miniapp_backend] {period_type} quests unavailable: {exc!r}")
            output[period_type] = {"tasks": [], "unavailable": True}
    return output


async def build_rewards_response(viewer: TelegramViewer) -> dict[str, Any]:
    home = await build_home_response(viewer)
    last = await fetchrow(
        "SELECT opened_at FROM miniapp_kitbag_claims WHERE user_id=$1 ORDER BY opened_at DESC LIMIT 1;",
        int(viewer.id),
    )
    pending_row = await fetchrow(
        "SELECT claim_id,player_id,player_kind,opened_at FROM miniapp_kitbag_claims WHERE user_id=$1 AND status='pending' ORDER BY opened_at DESC LIMIT 1;",
        int(viewer.id),
    )
    pending = None
    if pending_row:
        pending_player = await _select_player(str(pending_row.get("player_kind") or "global"), int(pending_row["player_id"]))
        if pending_player:
            pending = {
                "claim_id": str(pending_row["claim_id"]),
                "player": _player_payload(pending_player, owned=True),
                "opened_at": _iso(pending_row.get("opened_at")),
            }
    now = datetime.now(timezone.utc)
    last_at = last["opened_at"] if last else None
    if last_at and last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    seconds = max(0, int((last_at + timedelta(seconds=KITBAG_COOLDOWN_SECONDS) - now).total_seconds())) if last_at else 0
    return {
        "daily": home["daily_reward"],
        "kitbag": {"available": seconds == 0 and pending is None, "seconds_until_available": seconds, "reward_overall": 85, "pending_replacement": pending is not None},
        "pending_kitbag": pending,
        "available_systems": ["daily_streak", "hourly_kitbag", "quests"],
    }


async def _select_player(kind: str, entity_id: int) -> dict[str, Any] | None:
    if kind == "global":
        row = await fetchrow("SELECT *, 'global'::text player_kind FROM players WHERE player_id=$1;", int(entity_id))
    elif kind == "special":
        row = await fetchrow(
            "SELECT *,-special_player_id AS player_id,special_player_id AS special_edition_id,'special'::text player_kind,TRUE AS is_special FROM special_edition_players WHERE special_player_id=$1;",
            int(entity_id),
        )
    elif kind == "showcase":
        row = await fetchrow(
            """
            SELECT c.*,-(1000000000000::bigint+c.showcase_card_id) AS player_id,
                   c.showcase_card_id AS special_edition_id,s.set_name AS showcase_name,
                   'showcase'::text player_kind,TRUE AS is_showcase
            FROM showcase_cards c JOIN showcase_sets s USING(showcase_set_id)
            WHERE c.showcase_card_id=$1 AND s.status='active';
            """, int(entity_id),
        )
    else:
        return None
    return dict(row) if row else None


def _pack_opening_for_client(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize a persisted pack opening without rerolling its cards."""
    data = dict(record or {})
    cards = _json_value(data.get("cards"), [])
    output_cards = []
    if isinstance(cards, list):
        for raw_card in cards:
            if not isinstance(raw_card, dict):
                continue
            card = dict(raw_card)
            player = card.get("player")
            if isinstance(player, dict):
                client_player = _player_payload(dict(player), owned=True)
                card["player"] = client_player
                card["player_payload"] = client_player
            card["ovr"] = int(card.get("ovr") or (max(int((player or {}).get("bat_level") or 0), int((player or {}).get("bowl_level") or 0)) if isinstance(player, dict) else 0))
            output_cards.append(card)
    opened_at = data.get("opened_at")
    return {
        "opening_id": str(data.get("opening_id") or ""),
        "pack_key": str(data.get("pack_key") or ""),
        "status": str(data.get("status") or "opened"),
        "opened_at": _iso(opened_at),
        "cards": output_cards,
        "coins": int(data.get("coins") or 0),
        "rubies": int(data.get("rubies") or 0),
    }


async def build_pack_shop_response(viewer: TelegramViewer) -> dict[str, Any]:
    """Expose the bot's authoritative catalogue, balances and unopened inventory."""
    from services.pack_system import PACK_CATALOG, inventory

    user = await _ensure_user(viewer)
    quantities = await inventory(int(viewer.id))
    packs = []
    for key, entry in PACK_CATALOG.items():
        packs.append({
            "key": key,
            "name": str(entry["name"]),
            "price_sigils": int(entry["price"]),
            "cards": int(entry["cards"]),
            "core_ovr": [int(entry["core"][0]), int(entry["core"][1])],
            "bonus_ovr": [int(entry["bonus"][0]), int(entry["bonus"][1])],
            "coins_reward": int(entry["coins"]),
            "rubies_reward": int(entry["rubies"]),
            "description": str(entry["description"]),
            "quantity": int(quantities.get(key, 0)),
            "enabled": True,
        })
    return {
        "wallet": {
            "sigils": int(user.get("sigils") or 0),
            "coins": int(user.get("balance") or 0),
            "rubies": int(user.get("rubies") or 0),
        },
        "packs": packs,
        "note": "Prices, card bands and rewards come from Crickium's existing server catalogue.",
    }


async def build_pack_openings_response(viewer: TelegramViewer, limit: int = 10) -> dict[str, Any]:
    limit = max(1, min(int(limit), 20))
    rows = await fetch(
        "SELECT opening_id,pack_key,cards,coins,rubies,status,opened_at FROM pack_openings WHERE user_id=$1 ORDER BY opened_at DESC LIMIT $2;",
        int(viewer.id), limit,
    )
    return {"openings": [_pack_opening_for_client(dict(row)) for row in rows]}


async def build_pack_opening_detail(viewer: TelegramViewer, opening_id: str) -> dict[str, Any] | None:
    from services.pack_system import get_opening
    record = await get_opening(int(viewer.id), str(opening_id))
    return _pack_opening_for_client(dict(record)) if record else None


async def purchase_market_player(viewer: TelegramViewer, kind: str, entity_id: int) -> dict[str, Any]:
    player = await _select_player(kind, entity_id)
    if not player:
        return {"status": "not_found"}
    payload = _player_payload(player)
    price = int(payload["buy_price"])
    squad_player = dict(player)
    squad_player["player_id"] = int(payload["player_id"])
    squad_player["player_kind"] = kind
    if kind == "special":
        squad_player.update({"is_special": True, "special_edition_id": int(entity_id)})
    elif kind == "showcase":
        squad_player.update({"is_showcase": True, "showcase_card_id": int(entity_id)})

    async with acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "SELECT balance FROM users WHERE user_id=$1 FOR UPDATE;",
                int(viewer.id),
            )
            if not user:
                return {"status": "user_missing"}
            squad_row = await conn.fetchrow(
                "SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;",
                int(viewer.id),
            )
            squad = _squad(squad_row["squad"] if squad_row else None)
            if _is_owned(squad_player, squad):
                return {"status": "already_owned"}
            if len(squad) >= MAX_SQUAD_SIZE:
                return {"status": "squad_full"}
            if int(user["balance"] or 0) < price:
                return {"status": "insufficient_balance", "price": price}
            await conn.execute(
                "UPDATE users SET balance=balance-$1,total_spent=COALESCE(total_spent,0)+$1,last_seen_at=NOW() WHERE user_id=$2;",
                price, int(viewer.id),
            )
            squad.append(squad_player)
            await conn.execute(
                """
                INSERT INTO team_squads(user_id,squad,updated_at) VALUES($1,$2::jsonb,NOW())
                ON CONFLICT(user_id) DO UPDATE SET squad=EXCLUDED.squad,updated_at=NOW();
                """,
                int(viewer.id), json.dumps(squad, default=str),
            )
    try:
        from database.player_user_stats_repo import reset_player_user_stats
        await reset_player_user_stats(int(viewer.id), int(payload["player_id"]))
    except Exception as exc:
        print(f"[miniapp_backend] post-purchase player stats reset failed: {exc!r}")
    try:
        from services.quest_engine import record_quest_event
        await record_quest_event(int(viewer.id), "PLAYER_PURCHASE", metadata={"player_id": int(payload["player_id"]), "player_kind": kind})
        await record_quest_event(int(viewer.id), "COIN_SPENT", value=price, metadata={"source": "miniapp_market"})
    except Exception as exc:
        print(f"[miniapp_backend] purchase quest event failed: {exc!r}")
    return {"status": "success", "player": payload, "price": price}


async def claim_kitbag(viewer: TelegramViewer) -> dict[str, Any]:
    claim_id = uuid.uuid4()
    needs_replacement = False
    async with acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            if not user:
                return {"status": "user_missing"}
            pending = await conn.fetchrow(
                "SELECT claim_id,player_id FROM miniapp_kitbag_claims WHERE user_id=$1 AND status='pending' ORDER BY opened_at DESC LIMIT 1 FOR UPDATE;",
                int(viewer.id),
            )
            if pending:
                return {"status": "replacement_pending", "claim_id": str(pending["claim_id"]), "player_id": int(pending["player_id"])}
            squad_row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            squad = _squad(squad_row["squad"] if squad_row else None)
            last = await conn.fetchrow(
                "SELECT opened_at FROM miniapp_kitbag_claims WHERE user_id=$1 ORDER BY opened_at DESC LIMIT 1;",
                int(viewer.id),
            )
            now = datetime.now(timezone.utc)
            last_at = last["opened_at"] if last else None
            if last_at and last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=timezone.utc)
            if last_at and (now - last_at).total_seconds() < KITBAG_COOLDOWN_SECONDS:
                remaining = KITBAG_COOLDOWN_SECONDS - int((now - last_at).total_seconds())
                return {"status": "cooldown", "seconds_until_available": remaining}
            # This is a real OVR 85 catalog card. At squad capacity the claim is
            # persisted as pending, so the player can choose whom to replace.
            candidates = await conn.fetch(
                """
                SELECT p.*,'global'::text player_kind
                FROM players p
                WHERE GREATEST(COALESCE(p.bat_level,0),COALESCE(p.bowl_level,0))=85
                  AND NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements($1::jsonb) e
                    WHERE e->>'player_id'=p.player_id::text
                  )
                ORDER BY random() LIMIT 50;
                """,
                json.dumps(squad, default=str),
            )
            if not candidates:
                return {"status": "no_player"}
            player = dict(candidates[0])
            needs_replacement = len(squad) >= MAX_SQUAD_SIZE
            if not needs_replacement:
                squad.append(player)
                await conn.execute(
                    """
                    INSERT INTO team_squads(user_id,squad,updated_at) VALUES($1,$2::jsonb,NOW())
                    ON CONFLICT(user_id) DO UPDATE SET squad=EXCLUDED.squad,updated_at=NOW();
                    """,
                    int(viewer.id), json.dumps(squad, default=str),
                )
            await conn.execute(
                """
                INSERT INTO miniapp_kitbag_claims(claim_id,user_id,player_id,player_kind,opened_at,status)
                VALUES($1,$2,$3,'global',NOW(),$4);
                """,
                claim_id, int(viewer.id), int(player["player_id"]), "pending" if needs_replacement else "added",
            )
    try:
        from services.quest_engine import record_quest_event
        await record_quest_event(int(viewer.id), "KITBAG_OPEN")
    except Exception as exc:
        print(f"[miniapp_backend] kitbag quest event failed: {type(exc).__name__}")
    return {
        "status": "success",
        "player": _player_payload(player, owned=True),
        "claim_id": str(claim_id),
        "requires_replacement": needs_replacement,
        "squad_count": len(squad),
    }


async def replace_kitbag_player(viewer: TelegramViewer, claim_id: str, replaced_player_id: int) -> dict[str, Any]:
    """Resolve a saved Kit Bag reward, replacing a card only if the squad is full."""
    try:
        parsed_claim_id = uuid.UUID(str(claim_id))
    except (TypeError, ValueError, AttributeError):
        return {"status": "claim_not_found"}
    try:
        replaced_player_id = int(replaced_player_id or 0)
    except (TypeError, ValueError):
        return {"status": "invalid_replacement"}
    new_player: dict[str, Any] | None = None
    old_player: dict[str, Any] | None = None
    count = 0
    added_without_replacement = False
    async with acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            if not user:
                return {"status": "user_missing"}
            claim = await conn.fetchrow(
                "SELECT claim_id,player_id,player_kind,status FROM miniapp_kitbag_claims WHERE claim_id=$1 AND user_id=$2 FOR UPDATE;",
                parsed_claim_id, int(viewer.id),
            )
            if not claim:
                return {"status": "claim_not_found"}
            if str(claim.get("status") or "added") != "pending":
                return {"status": "claim_already_resolved"}
            squad_row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            squad = _squad(squad_row["squad"] if squad_row else None)
            claimed_id = int(claim["player_id"])
            if claimed_id in _player_id_set(squad):
                return {"status": "reward_already_owned"}
            player_row = await conn.fetchrow("SELECT *, 'global'::text player_kind FROM players WHERE player_id=$1;", claimed_id)
            if not player_row:
                return {"status": "player_not_found"}
            new_player = dict(player_row)
            lineup_row = await conn.fetchrow("SELECT player_ids FROM team_lineups WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            lineup_ids = _normalize_lineup_ids(lineup_row["player_ids"] if lineup_row else None, squad)

            if len(squad) < MAX_SQUAD_SIZE:
                # The user may have sold a card after opening the Kit Bag. Use
                # that now-free slot instead of forcing a second, unnecessary sale.
                updated_squad = list(squad) + [new_player]
                await conn.execute(
                    """
                    INSERT INTO team_squads(user_id,squad,updated_at) VALUES($1,$2::jsonb,NOW())
                    ON CONFLICT(user_id) DO UPDATE SET squad=EXCLUDED.squad,updated_at=NOW();
                    """,
                    int(viewer.id), json.dumps(updated_squad, default=str),
                )
                await _save_lineup_ids_conn(conn, int(viewer.id), lineup_ids)
                await conn.execute(
                    "UPDATE miniapp_kitbag_claims SET status='added',replaced_player_id=NULL,resolved_at=NOW() WHERE claim_id=$1 AND user_id=$2;",
                    parsed_claim_id, int(viewer.id),
                )
                added_without_replacement = True
            else:
                if not replaced_player_id:
                    return {"status": "invalid_replacement"}
                old_index = next((i for i, item in enumerate(squad) if int(item.get("player_id") or 0) == replaced_player_id), None)
                if old_index is None:
                    return {"status": "replacement_not_owned"}
                old_player = dict(squad[old_index])
                updated_squad = list(squad)
                updated_squad[old_index] = new_player
                lineup_ids = [claimed_id if pid == replaced_player_id else pid for pid in lineup_ids]
                await conn.execute(
                    "UPDATE team_squads SET squad=$2::jsonb,updated_at=NOW() WHERE user_id=$1;",
                    int(viewer.id), json.dumps(updated_squad, default=str),
                )
                await _save_lineup_ids_conn(conn, int(viewer.id), lineup_ids)
                await conn.execute(
                    "UPDATE miniapp_kitbag_claims SET status='replaced',replaced_player_id=$1,resolved_at=NOW() WHERE claim_id=$2 AND user_id=$3;",
                    replaced_player_id, parsed_claim_id, int(viewer.id),
                )
            count = len(updated_squad)
    if old_player:
        try:
            from database.player_user_stats_repo import reset_player_user_stats
            await reset_player_user_stats(int(viewer.id), int(replaced_player_id))
        except Exception as exc:
            print(f"[miniapp_backend] replaced-player stats reset failed: {type(exc).__name__}")
    try:
        from services.quest_engine import record_quest_event
        await record_quest_event(
            int(viewer.id), "TEAM_UPDATE",
            metadata={"source": "kitbag_replacement" if old_player else "kitbag_pending_add", "player_id": int(claim["player_id"]), "replaced_player_id": replaced_player_id if old_player else None},
        )
    except Exception as exc:
        print(f"[miniapp_backend] kitbag replacement quest event failed: {type(exc).__name__}")
    return {
        "status": "success",
        "player": _player_payload(new_player or {}, owned=True),
        "replaced_player": _player_payload(old_player, owned=False) if old_player else None,
        "added_without_replacement": added_without_replacement,
        "count": count,
    }


async def claim_daily_reward(viewer: TelegramViewer) -> dict[str, Any]:
    # Preserve the existing bot's debut gate and seven-day progression.
    debut = await fetchval("SELECT 1 FROM team_squads WHERE user_id=$1 LIMIT 1;", int(viewer.id))
    if not debut:
        return {"status": "debut_required"}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result: dict[str, Any] | None = None
    async with acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            if not user:
                return {"status": "user_missing"}
            daily = await conn.fetchrow(
                "SELECT streak,last_claim_at,next_claim_at FROM daily_rewards WHERE user_id=$1 FOR UPDATE;",
                int(viewer.id),
            )
            next_claim = daily["next_claim_at"] if daily else None
            if next_claim and next_claim.tzinfo is not None:
                next_claim = next_claim.replace(tzinfo=None)
            if next_claim and now < next_claim:
                return {"status": "cooldown", "seconds_until_available": max(0, int((next_claim - now).total_seconds()))}
            streak = int(daily["streak"] or 0) if daily else 0
            last_claim = daily["last_claim_at"] if daily else None
            if last_claim and last_claim.tzinfo is not None:
                last_claim = last_claim.replace(tzinfo=None)
            if last_claim and now >= last_claim + timedelta(seconds=2 * DAILY_COOLDOWN_SECONDS):
                streak = 0
            day = 1 if streak >= 7 else streak + 1
            reward = DAILY_REWARDS[day]
            squad_row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            squad = _squad(squad_row["squad"] if squad_row else None)
            player = None
            if reward["player_range"]:
                if len(squad) >= MAX_SQUAD_SIZE:
                    return {"status": "squad_full", "day": day}
                low, high = reward["player_range"]
                owned_ids = [int(item.get("player_id") or 0) for item in squad]
                player_row = await conn.fetchrow(
                    """
                    SELECT * FROM players
                    WHERE GREATEST(COALESCE(bat_level,0),COALESCE(bowl_level,0)) BETWEEN $1 AND $2
                      AND NOT(player_id=ANY($3::bigint[]))
                    ORDER BY random() LIMIT 1;
                    """, int(low), int(high), owned_ids,
                )
                if not player_row:
                    return {"status": "no_player", "day": day, "player_range": [low, high]}
                player = dict(player_row)
                squad.append(player)
                await conn.execute(
                    """
                    INSERT INTO team_squads(user_id,squad,updated_at) VALUES($1,$2::jsonb,NOW())
                    ON CONFLICT(user_id) DO UPDATE SET squad=EXCLUDED.squad,updated_at=NOW();
                    """, int(viewer.id), json.dumps(squad, default=str),
                )
            next_at = now + timedelta(seconds=DAILY_COOLDOWN_SECONDS)
            await conn.execute(
                "UPDATE users SET balance=COALESCE(balance,0)+$1,rubies=COALESCE(rubies,0)+$2,last_seen_at=NOW() WHERE user_id=$3;",
                int(reward["coins"]), int(reward["rubies"]), int(viewer.id),
            )
            await conn.execute(
                """
                INSERT INTO daily_rewards(user_id,streak,total_claimed,last_claim_at,next_claim_at,updated_at)
                VALUES($1,$2,1,NOW(),$3,NOW())
                ON CONFLICT(user_id) DO UPDATE SET
                  streak=EXCLUDED.streak,
                  total_claimed=daily_rewards.total_claimed+1,
                  last_claim_at=EXCLUDED.last_claim_at,
                  next_claim_at=EXCLUDED.next_claim_at,
                  updated_at=NOW();
                """, int(viewer.id), day, next_at,
            )
            result = {"status": "success", "day": day, "reward": reward, "player": player}
    if result and result["status"] == "success":
        try:
            from services.quest_engine import record_quest_event
            await record_quest_event(int(viewer.id), "DAILY_REWARD_CLAIM", metadata={"day": int(result["day"])})
        except Exception as exc:
            print(f"[miniapp_backend] daily reward quest event failed: {exc!r}")
        result["player"] = _player_payload(result["player"], owned=True) if result.get("player") else None
    return result or {"status": "error"}


async def build_transaction_history(viewer: TelegramViewer, limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(int(limit), 50))
    rows = await fetch(
        """
        SELECT completion_id::text AS id,period_type AS source,
               ('Quest reward: '||task_id) AS description,
               coins,rubies,sigils AS tokens,completed_at AS created_at
        FROM quest_completions WHERE user_id=$1
        ORDER BY completed_at DESC LIMIT $2;
        """, int(viewer.id), limit,
    )
    return {
        "items": [
            {
                "id": str(row["id"]), "source": row["source"], "description": row["description"],
                "coins": int(row["coins"] or 0), "rubies": int(row["rubies"] or 0),
                "sigils": int(row["tokens"] or 0),
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
            for row in rows
        ],
        "note": "The bot does not maintain a complete unified wallet ledger; this view shows recorded Quest reward credits only.",
    }
