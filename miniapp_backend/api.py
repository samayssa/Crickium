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


async def _get_player_image_id(kind: str, entity_id: int) -> str | None:
    spec = _CARD_IMAGES.get(kind)
    if not spec:
        return None
    table, id_column = spec
    row = await fetchrow(f"SELECT file_id FROM {table} WHERE {id_column}=$1;", int(entity_id))
    if row:
        return str(row["file_id"])
    if kind == "global":
        role = await fetchrow("SELECT role,bat_level,bowl_level FROM players WHERE player_id=$1;", int(entity_id))
    elif kind == "special":
        role = await fetchrow("SELECT role,bat_level,bowl_level FROM special_edition_players WHERE special_player_id=$1;", int(entity_id))
    else:
        role = await fetchrow("SELECT role,bat_level,bowl_level FROM showcase_cards WHERE showcase_card_id=$1;", int(entity_id))
    if not role:
        return None
    card = _card_type(dict(role))
    template = await fetchrow("SELECT file_id FROM template_card_image WHERE card_type=$1;", card)
    return str(template["file_id"]) if template else None


async def player_image_bytes(kind: str, entity_id: int) -> tuple[bytes, str] | None:
    """Proxy Telegram images server-side so file URLs never expose the bot token."""
    file_id = await _get_player_image_id(kind, entity_id)
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
        content_type = response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
        return response.content, content_type


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


async def _active_matches(user_id: int, limit: int = 5) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for table, engine in MATCH_TABLES:
        try:
            if table == "match_challenges":
                # In the current schema challenges use challenge_id/format and
                # have no pitch column; treating them as match rows made the
                # whole source query fail and silently hid open challenges.
                rows = await fetch(
                    """
                    SELECT challenge_id AS match_id,challenger_id,challenger_name,
                           opponent_id,opponent_name,status,NULL::text AS pitch,
                           format AS match_type,created_at
                    FROM match_challenges
                    WHERE (challenger_id=$1 OR opponent_id=$1)
                      AND LOWER(COALESCE(status,'')) NOT IN
                          ('completed','ended','finished','complete','declined','abandoned','expired','timed_out')
                    ORDER BY created_at DESC LIMIT $2;
                    """,
                    int(user_id), int(limit),
                )
            else:
                rows = await fetch(
                    f"""
                    SELECT match_id,challenger_id,challenger_name,opponent_id,opponent_name,
                           status,pitch,created_at
                    FROM {table}
                    WHERE (challenger_id=$1 OR opponent_id=$1)
                      AND LOWER(COALESCE(status,'')) NOT IN
                          ('completed','ended','finished','complete','declined','abandoned','expired','timed_out')
                    ORDER BY created_at DESC LIMIT $2;
                    """,
                    int(user_id), int(limit),
                )
        except Exception as exc:
            print(f"[miniapp_backend] match source {table} unavailable: {exc!r}")
            continue
        for row in rows:
            item = dict(row)
            item["engine"] = engine
            item["match_id"] = int(item["match_id"])
            if item.get("match_type") is None:
                item["match_type"] = engine
            item["created_at"] = item["created_at"].isoformat() if item.get("created_at") else None
            item["is_live"] = str(item.get("status") or "").lower() in {"live", "innings_break"}
            result.append(item)
    result.sort(key=lambda item: item.get("created_at") or "", reverse=True)
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


async def build_squad_response(viewer: TelegramViewer) -> dict[str, Any]:
    squad = await _owned_squad(viewer.id)
    return {
        "count": len(squad),
        "capacity": MAX_SQUAD_SIZE,
        "players": [
            _player_payload(player, owned=True)
            | {"is_captain": int(player.get("player_id") or 0) == int(player.get("captain_player_id") or 0)}
            for player in squad
        ],
    }


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
    return {
        "recent": [_match_summary(dict(row)) for row in recent],
        "active": await _active_matches(viewer.id, limit=30),
        "note": "The persistent records available to the Mini App contain per-user quest match summaries and match setup/status. Full innings scorecards are not stored as queryable rows in this repository.",
    }


async def build_match_detail(viewer: TelegramViewer, engine: str, match_id: int) -> dict[str, Any] | None:
    summary = await fetchrow(
        """
        SELECT engine,match_id,match_type,won,pitch,target,batting_runs,bowling_wickets,
               batting,bowling,over_history,played_at
        FROM quest_match_summaries WHERE user_id=$1 AND LOWER(engine)=LOWER($2) AND match_id=$3
        ORDER BY played_at DESC LIMIT 1;
        """, int(viewer.id), str(engine), int(match_id),
    )
    if summary:
        item = _match_summary(dict(summary))
        item["batting"] = _json_value(summary["batting"], {})
        item["bowling"] = _json_value(summary["bowling"], {})
        item["over_history"] = _json_value(summary["over_history"], [])
        item["scorecard_available"] = bool(item["batting"] or item["bowling"] or item["over_history"])
        return item
    if str(engine).upper() == "PLAYSO":
        row = await fetchrow(
            "SELECT match_id,challenger_id,challenger_name,opponent_id,opponent_name,status,pitch,state,created_at FROM playso_matches WHERE match_id=$1 AND (challenger_id=$2 OR opponent_id=$2);",
            int(match_id), int(viewer.id),
        )
        if row:
            item = dict(row)
            item["state"] = _json_value(item.get("state"), {})
            item["created_at"] = item["created_at"].isoformat() if item.get("created_at") else None
            return item
    return None


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
    now = datetime.now(timezone.utc)
    last_at = last["opened_at"] if last else None
    if last_at and last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    seconds = max(0, int((last_at + timedelta(seconds=KITBAG_COOLDOWN_SECONDS) - now).total_seconds())) if last_at else 0
    return {
        "daily": home["daily_reward"],
        "kitbag": {"available": seconds == 0, "seconds_until_available": seconds, "reward_overall": 85},
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
    async with acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            if not user:
                return {"status": "user_missing"}
            squad_row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(viewer.id))
            squad = _squad(squad_row["squad"] if squad_row else None)
            if len(squad) >= MAX_SQUAD_SIZE:
                return {"status": "squad_full", "count": len(squad)}
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
            # The reward uses only a real catalogued player whose stored OVR
            # is exactly 85. It never fabricates or overwrites player stats.
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
            squad.append(player)
            await conn.execute(
                """
                INSERT INTO team_squads(user_id,squad,updated_at) VALUES($1,$2::jsonb,NOW())
                ON CONFLICT(user_id) DO UPDATE SET squad=EXCLUDED.squad,updated_at=NOW();
                """,
                int(viewer.id), json.dumps(squad, default=str),
            )
            await conn.execute(
                "INSERT INTO miniapp_kitbag_claims(claim_id,user_id,player_id,player_kind,opened_at) VALUES($1,$2,$3,'global',NOW());",
                claim_id, int(viewer.id), int(player["player_id"]),
            )
    try:
        from services.quest_engine import record_quest_event
        await record_quest_event(int(viewer.id), "KITBAG_OPEN")
    except Exception as exc:
        print(f"[miniapp_backend] kitbag quest event failed: {exc!r}")
    return {"status": "success", "player": _player_payload(player, owned=True), "claim_id": str(claim_id)}


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
