"""Giveaway core logic shared by the bot and the Mini App.

This module is deliberately free of the Telegram client (no ``from app import``)
so the Mini App process can import it too. Anything that needs the Telegram
Bot API goes through :func:`tg_call` (plain HTTPS).

Rules in one place
------------------
* Only members of the main group may enter (checked when entering AND again at
  the draw - someone who left the group in the meantime is skipped).
* A card can only be credited to a player who has a squad, does not already own
  that exact card and has a free squad slot (max 25). The same checks apply at
  entry time and at the draw, so a winner can always really receive the card.
* The winner is chosen by weighted random draw: more bot activity (matches
  played AND won, claims, daily rewards, quests, packs, Mini App games and
  recent activity) = higher chance. Weights are compressed a little so a new
  player still has a fair shot.
"""

from __future__ import annotations

import html
import json
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from config import BOT_TOKEN, MAIN_GROUP_ID, MAIN_GROUP_LINK
from database import giveaway_repo as repo
from database.query import fetchrow, transaction
from services.card_identity import card_entity_id, card_identity_key, owned_same_card, player_kind, showcase_squad_player_id
from utils.country_flags import flag_for
from utils.price_chart import format_price, get_price
from utils.rarity import get_rarity
from utils.style import batting_style_text, bowling_style_text

IST = timezone(timedelta(hours=5, minutes=30))
MAX_SQUAD_SIZE = 25

MIN_DURATION_S = 5 * 60
MAX_DURATION_S = 30 * 24 * 3600
MAX_START_DELAY_S = 30 * 24 * 3600

PUNCHLINES = (
    "Fortune favours the active - and you played like a champion!",
    "Every match, every claim, every over - it all led to this moment.",
    "The dressing room just got stronger. Take a bow!",
    "Hard work on the field, luck in the draw - a perfect innings!",
    "A new star walks into your squad. Time to dominate!",
    "Activity pays off. Welcome to the winners' circle!",
)

# --------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------
_UNIT_SECONDS = {
    "w": 604800, "wk": 604800, "wks": 604800, "week": 604800, "weeks": 604800,
    "d": 86400, "day": 86400, "days": 86400,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
}
_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)")


def parse_duration(text: str | None) -> int | None:
    """'90m', '2h 30m', '1d12h', '1.5 hours' -> seconds. 'now' -> 0. Unknown -> None."""
    raw = str(text or "").strip().lower()
    if not raw:
        return None
    if raw in {"now", "0", "immediately", "abhi"}:
        return 0
    rest = _TOKEN_RE.sub("", raw)
    if re.sub(r"[\s,+&]|and", "", rest):
        return None  # leftover text we could not understand
    total = 0.0
    found = False
    for number, unit in _TOKEN_RE.findall(raw):
        seconds = _UNIT_SECONDS.get(unit)
        if seconds is None:
            return None
        total += float(number) * seconds
        found = True
    if not found:
        return None
    return int(round(total))


def fmt_duration(seconds: int | float) -> str:
    s = max(0, int(seconds))
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}m")
    return " ".join(parts) or "0m"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_ist(dt: datetime | None) -> str:
    dt = _aware(dt)
    if dt is None:
        return "-"
    d = dt.astimezone(IST)
    return f"{d.day} {d:%b %Y}, {d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'} IST"


def seconds_left(dt: datetime | None) -> int:
    dt = _aware(dt)
    if dt is None:
        return 0
    return max(0, int((dt - now_utc()).total_seconds()))


def iso(dt: datetime | None) -> str | None:
    dt = _aware(dt)
    return dt.isoformat() if dt else None


# --------------------------------------------------------------------------
# Telegram Bot API over plain HTTPS (works in the bot AND the Mini App process)
# --------------------------------------------------------------------------
class TgError(RuntimeError):
    def __init__(self, description: str, code: int | None = None):
        super().__init__(description)
        self.description = description or ""
        self.code = code


async def tg_call(method: str, payload: dict | None = None, timeout: float = 15.0) -> Any:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        import httpx  # Mini App process
    except ImportError:  # pragma: no cover - bot process without httpx
        httpx = None
    if httpx is not None:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload or {})
            data = response.json()
    else:
        import aiohttp  # bot process
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.post(url, json=payload or {}) as response:
                data = await response.json()
    if not data.get("ok"):
        raise TgError(str(data.get("description") or "Telegram error"), data.get("error_code"))
    return data["result"]


_MEMBER_CACHE: dict[int, float] = {}   # user_id -> expires (monotonic) for CONFIRMED members only
_MEMBER_TTL = 60.0


async def is_main_member(user_id: int, *, use_cache: bool = True) -> bool | None:
    """True / False when Telegram answers, None when it could not be verified."""
    uid = int(user_id)
    now = time.monotonic()
    if use_cache and _MEMBER_CACHE.get(uid, 0) > now:
        return True
    try:
        result = await tg_call("getChatMember", {"chat_id": MAIN_GROUP_ID, "user_id": uid})
    except TgError as exc:
        text = exc.description.lower()
        if "user not found" in text or "participant_id_invalid" in text:
            return False
        print(f"[giveaway] membership check unavailable: {exc.description}")
        return None
    except Exception as exc:
        print(f"[giveaway] membership check failed: {type(exc).__name__}: {exc}")
        return None
    status = str(result.get("status") or "")
    if status in {"creator", "administrator", "member"}:
        member = True
    elif status == "restricted":
        member = bool(result.get("is_member", True))
    else:  # left / kicked
        member = False
    if member:
        _MEMBER_CACHE[uid] = now + _MEMBER_TTL
        if len(_MEMBER_CACHE) > 5000:
            for key in [k for k, v in _MEMBER_CACHE.items() if v < now]:
                _MEMBER_CACHE.pop(key, None)
    else:
        _MEMBER_CACHE.pop(uid, None)
    return member


_LINK_CACHE: dict[str, Any] = {"link": None, "at": 0.0}
_LINK_KV_KEY = "giveaway_main_group_link"


async def main_group_link() -> str | None:
    """Link people can use to join the main group (config > public username > saved/created invite link)."""
    configured = str(MAIN_GROUP_LINK or "").strip()
    if configured:
        if configured.startswith("t.me/") or configured.startswith("telegram.me/"):
            configured = "https://" + configured
        elif configured.startswith("@"):
            configured = "https://t.me/" + configured[1:]
        return configured
    if _LINK_CACHE["link"] and time.monotonic() - _LINK_CACHE["at"] < 6 * 3600:
        return _LINK_CACHE["link"]
    link = None
    try:
        chat = await tg_call("getChat", {"chat_id": MAIN_GROUP_ID})
        if chat.get("username"):
            link = f"https://t.me/{chat['username']}"
        elif chat.get("invite_link"):
            link = chat["invite_link"]
    except Exception as exc:
        print(f"[giveaway] getChat for main group failed: {type(exc).__name__}: {exc}")
    if not link:
        link = await repo.kv_get(_LINK_KV_KEY)
    if not link:
        try:
            created = await tg_call("createChatInviteLink", {"chat_id": MAIN_GROUP_ID, "name": "Crickium giveaway"})
            link = created.get("invite_link")
            if link:
                await repo.kv_set(_LINK_KV_KEY, link)
        except Exception as exc:
            print(f"[giveaway] could not create an invite link for the main group: {type(exc).__name__}: {exc}")
    if link:
        _LINK_CACHE.update({"link": link, "at": time.monotonic()})
    return link


# --------------------------------------------------------------------------
# cards
# --------------------------------------------------------------------------
def squad_entry(card: dict) -> dict:
    """JSON-safe copy of a card in exactly the shape stored in team_squads."""
    entry = json.loads(json.dumps(dict(card), default=str))
    kind = player_kind(entry)
    if kind == "special":
        sid = int(entry.get("special_edition_id") or abs(int(entry.get("player_id") or 0)))
        entry.update({"player_id": -sid, "special_edition_id": sid, "player_kind": "special", "is_special": True,
                      "is_showcase": False, "showcase_card_id": None, "showcase_set_id": None, "showcase_name": None})
    elif kind == "showcase":
        cid = int(entry.get("showcase_card_id") or 0)
        entry.update({"player_id": showcase_squad_player_id(cid), "player_kind": "showcase", "is_showcase": True,
                      "is_special": False, "edition": None, "special_edition_id": None})
    else:
        entry.update({"player_id": int(entry.get("player_id") or 0), "player_kind": "global", "is_special": False,
                      "is_showcase": False, "edition": None, "special_edition_id": None, "showcase_card_id": None,
                      "showcase_set_id": None, "showcase_name": None})
    return entry


async def resolve_live_card(card: dict) -> dict | None:
    """Fresh copy of the prize from its source table (None if it was deleted meanwhile)."""
    kind = player_kind(card)
    entity = card_entity_id(card)
    if entity <= 0:
        return None
    try:
        if kind == "special":
            from database.special_players_repo import get_special_player_by_id
            return await get_special_player_by_id(entity)
        if kind == "showcase":
            from database.showcase_players_repo import get_showcase_player
            return await get_showcase_player(entity)
        row = await fetchrow("SELECT * FROM players WHERE player_id=$1;", entity)
        if not row:
            return None
        live = dict(row)
        live.update({"player_kind": "global", "is_special": False, "is_showcase": False, "edition": None,
                     "special_edition_id": None})
        return live
    except Exception as exc:
        print(f"[giveaway] resolve_live_card failed: {type(exc).__name__}: {exc}")
        return dict(card)  # DB hiccup: fall back to the saved snapshot rather than losing the giveaway


_ROLE_ICON = {"batsman": "🏏", "bowler": "🎯", "allrounder": "🔄", "wicketkeeper": "🧤"}


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def card_info(card: dict) -> dict:
    bat = int(card.get("bat_level") or 0)
    bowl = int(card.get("bowl_level") or 0)
    ovr = max(bat, bowl)
    kind = player_kind(card)
    version = None
    if kind == "special" and card.get("edition"):
        version = str(card.get("edition"))
    elif kind == "showcase":
        version = str(card.get("showcase_name") or card.get("set_name") or "Showcase")
    name = str(card.get("name") or "Player")
    role = str(card.get("role") or "Player")
    buy, sell = get_price(ovr)
    return {
        "name": name,
        "full_name": f"{name} ({version})" if version else name,
        "version": version,
        "kind": kind,
        "entity_id": card_entity_id(card),
        "country": card.get("country"),
        "flag": flag_for(card.get("country")),
        "role": role,
        "role_icon": _ROLE_ICON.get(role.strip().lower().replace(" ", "").replace("-", ""), "🏏"),
        "bat": bat, "bowl": bowl, "ovr": ovr,
        "rarity": get_rarity(ovr),
        "bat_style": batting_style_text(card.get("batting_hand")),
        "bowl_style": bowling_style_text(card.get("bowling_hand")),
        "buy_price": int(buy), "sell_price": int(sell),
    }


def prize_html(card: dict) -> str:
    i = card_info(card)
    version = f"\n✨ <b>Edition</b> ➤ {esc(i['version'])}" if i["version"] else ""
    return (
        f"<blockquote>👤 <b>{esc(i['name'])}</b> {i['flag']}</blockquote>"
        f"<blockquote>💎 <b>Rarity</b> ➤ {esc(i['rarity'])}\n"
        f"⭐ <b>Overall</b> ➤ {i['ovr']}\n"
        f"🏏 <b>Bat Lv.</b> ➤ {i['bat']}   🎯 <b>Bowl Lv.</b> ➤ {i['bowl']}\n"
        f"{i['role_icon']} <b>Role</b> ➤ {esc(i['role'])}\n"
        f"🧤 <b>Bat Style</b> ➤ {esc(i['bat_style'])}\n"
        f"⚡ <b>Bowl Style</b> ➤ {esc(i['bowl_style'])}\n"
        f"💰 <b>Card Value</b> ➤ {esc(format_price(i['buy_price']))} coins{version}</blockquote>"
    )


# --------------------------------------------------------------------------
# activity score + weighted draw
# --------------------------------------------------------------------------
SCORE_WEIGHTS = {
    "matches": 2.0,    # matches played (win OR lose)
    "wins": 3.0,       # matches won
    "claims": 1.5,     # /claim attempts
    "daily": 2.0,      # daily rewards collected
    "quests": 3.0,     # quests completed
    "packs": 1.0,      # packs opened
    "g_games": 1.0,    # Mini App "Play Game" matches
    "g_wins": 2.0,     # Mini App wins
    "recent": 0.5,     # bot actions in the last 14 days
}
RECENT_CAP = 400
CHANCE_EXPONENT = 0.85


def score_from(metrics: dict[str, float]) -> float:
    total = 0.0
    for key, weight in SCORE_WEIGHTS.items():
        value = float(metrics.get(key) or 0)
        if key == "recent":
            value = min(value, RECENT_CAP)
        total += max(0.0, value) * weight
    return round(total, 1)


def chance_weight(score: float) -> float:
    return 1.0 + max(0.0, float(score)) ** CHANCE_EXPONENT


async def scores_for(user_ids: list[int]) -> dict[int, float]:
    metrics = await repo.activity_metrics(user_ids)
    return {int(uid): score_from(m) for uid, m in metrics.items()}


def weighted_order(user_ids: list[int], scores: dict[int, float]) -> list[int]:
    """Weighted random order without replacement (Efraimidis-Spirakis): the first
    element is exactly a draw proportional to each user's chance weight."""
    rng = random.SystemRandom()
    keyed = []
    for uid in user_ids:
        weight = chance_weight(scores.get(int(uid), 0.0))
        keyed.append((rng.random() ** (1.0 / weight), int(uid)))
    keyed.sort(reverse=True)
    return [uid for _key, uid in keyed]


_POOL_CACHE: dict[int, dict[str, Any]] = {}
_POOL_TTL = 60.0
_POOL_MAX_ENTRIES = 5000


async def pool_stats(giveaway_id: int) -> dict[str, Any]:
    """Total chance weight of everyone currently entered (cached for a minute)."""
    gid = int(giveaway_id)
    cached = _POOL_CACHE.get(gid)
    if cached and time.monotonic() - cached["at"] < _POOL_TTL:
        return cached
    entries = await repo.entries(gid)
    ids = [int(e["user_id"]) for e in entries]
    exact = len(ids) <= _POOL_MAX_ENTRIES
    weights: dict[int, float] = {}
    if exact and ids:
        scores = await scores_for(ids)
        weights = {uid: chance_weight(scores.get(uid, 0.0)) for uid in ids}
    stats = {"at": time.monotonic(), "count": len(ids), "ids": set(ids), "weights": weights,
             "total": sum(weights.values()), "exact": exact}
    _POOL_CACHE[gid] = stats
    if len(_POOL_CACHE) > 200:
        for key in sorted(_POOL_CACHE, key=lambda k: _POOL_CACHE[k]["at"])[:50]:
            _POOL_CACHE.pop(key, None)
    return stats


def invalidate_pool(giveaway_id: int) -> None:
    _POOL_CACHE.pop(int(giveaway_id), None)


async def my_standing(giveaway_id: int, user_id: int) -> dict[str, Any]:
    """Activity score and winning chance of one user inside one giveaway."""
    uid = int(user_id)
    stats = await pool_stats(giveaway_id)
    score = (await scores_for([uid])).get(uid, 0.0)
    mine = chance_weight(score)
    total = stats["total"] if stats["exact"] else 0.0
    if uid not in stats["ids"]:
        total += mine
    count = stats["count"] + (0 if uid in stats["ids"] else 1)
    chance = (mine / total * 100.0) if total > 0 else None
    return {"score": score, "chance": chance, "entries": count}


# --------------------------------------------------------------------------
# eligibility
# --------------------------------------------------------------------------
def _load_squad(raw: Any) -> list[dict]:
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    return [dict(p) for p in (raw or []) if isinstance(p, dict)]


async def receive_status(user_id: int, card: dict) -> str:
    """'ok' | 'no_account' | 'owns' | 'full' - can this user be handed this card right now?"""
    user = await fetchrow("SELECT user_id FROM users WHERE user_id=$1;", int(user_id))
    if not user:
        return "no_account"
    row = await fetchrow("SELECT squad FROM team_squads WHERE user_id=$1;", int(user_id))
    if not row:
        return "no_account"
    squad = _load_squad(row["squad"])
    if any(owned_same_card(p, card) for p in squad):
        return "owns"
    if len(squad) >= MAX_SQUAD_SIZE:
        return "full"
    return "ok"


REASON_TEXT = {
    "not_member": "Join the main group first, then tap Participate again.",
    "unverified": "I couldn't verify your main-group membership right now. Please try again in a minute.",
    "no_account": "Create your team first: send /start and /debut to the bot, then come back.",
    "owns": "You already own this card, so you can't win it. Pick the next giveaway!",
    "full": "Your squad is full (25/25). Sell a player to free a slot, then participate.",
    "ended": "This giveaway has ended.",
    "missing": "This giveaway is no longer available.",
}


async def requirements(user_id: int, card: dict, *, member: bool | None = None) -> dict[str, Any]:
    """Everything the UI needs to show a checklist for one user."""
    if member is None:
        member = await is_main_member(user_id)
    status = await receive_status(user_id, card)
    reason = None
    if member is False:
        reason = "not_member"
    elif member is None:
        reason = "unverified"
    elif status != "ok":
        reason = status
    return {
        "member": member,
        "account": status != "no_account",
        "owns": status == "owns",
        "squad_free": status != "full",
        "ok": reason is None,
        "reason": reason,
    }


# --------------------------------------------------------------------------
# joining
# --------------------------------------------------------------------------
async def join(giveaway_id: int, user_id: int, username: str | None, first_name: str | None) -> dict[str, Any]:
    """Try to enter a user. Returns {'status': 'ok'|'already'|<reason>, ...standing}."""
    g = await repo.get(giveaway_id)
    if not g or g["status"] in {"draft", "cancelled"}:
        return {"status": "missing", "message": REASON_TEXT["missing"]}
    if g["status"] == "scheduled":
        return {"status": "not_started", "start_at": g["start_at"],
                "message": f"This giveaway starts on {fmt_ist(g['start_at'])}."}
    end_at = _aware(g.get("end_at"))
    if g["status"] != "active" or (end_at and end_at <= now_utc()):
        return {"status": "ended", "message": REASON_TEXT["ended"]}

    card = g["player"]
    req = await requirements(user_id, card)
    already = await repo.has_entry(giveaway_id, user_id)
    if not req["ok"]:
        if already and req["reason"] in {"owns", "full", "not_member"}:
            # keep the old entry; the draw re-checks, but tell them what is wrong now
            return {"status": req["reason"], "already": True, "message": REASON_TEXT[req["reason"]]}
        return {"status": req["reason"], "message": REASON_TEXT[req["reason"]]}

    added = await repo.add_entry(giveaway_id, user_id, username, first_name)
    if added:
        invalidate_pool(giveaway_id)
    standing = await my_standing(giveaway_id, user_id)
    return {"status": "ok" if added else "already", **standing}


# --------------------------------------------------------------------------
# the draw
# --------------------------------------------------------------------------
async def _credit_and_finish(g: dict, entry: dict, card: dict, score: float, entries_total: int) -> str:
    """Hand the card to one entrant AND close the giveaway in ONE transaction."""
    gid = int(g["giveaway_id"])
    uid = int(entry["user_id"])
    new_card = squad_entry(card)

    async def _tx(conn):
        state = await conn.fetchval("SELECT status FROM giveaways WHERE giveaway_id=$1 FOR UPDATE;", gid)
        if state != "ending":
            return "stale"
        if not await conn.fetchval("SELECT 1 FROM users WHERE user_id=$1 FOR UPDATE;", uid):
            return "no_account"
        row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", uid)
        if not row:
            return "no_account"
        squad = _load_squad(row["squad"])
        if any(owned_same_card(p, new_card) for p in squad):
            return "owns"
        if len(squad) >= MAX_SQUAD_SIZE:
            return "full"
        squad.append(new_card)
        await conn.execute(
            "UPDATE team_squads SET squad=$1::jsonb, updated_at=NOW() WHERE user_id=$2;",
            json.dumps(squad, default=str), uid,
        )
        # a re-obtained card starts with a clean per-user stat line (same as referral rewards)
        await conn.execute(
            "DELETE FROM player_user_match_stats WHERE user_id=$1 AND player_id=$2;",
            uid, int(new_card["player_id"]),
        )
        await conn.execute(
            """
            UPDATE giveaways
            SET status='ended', ended_at=NOW(), winner_id=$2, winner_username=$3, winner_name=$4,
                winner_score=$5, entries_final=$6, result_note='Winner credited'
            WHERE giveaway_id=$1;
            """,
            gid, uid, entry.get("username"), entry.get("first_name"), float(score), int(entries_total),
        )
        return "ok"

    return await transaction(_tx)


async def run_draw(g: dict) -> dict[str, Any]:
    """Pick the winner of a giveaway that the worker already moved to 'ending'.

    Returns {'status': 'won', 'giveaway': row} or {'status': 'no_winner', 'giveaway': row}.
    """
    gid = int(g["giveaway_id"])
    entries = await repo.entries(gid)
    card = await resolve_live_card(g["player"])
    if card is None:
        row = await repo.mark_ended_without_winner(gid, "The prize card no longer exists", len(entries))
        return {"status": "no_winner", "giveaway": row or await repo.get(gid)}
    if not entries:
        row = await repo.mark_ended_without_winner(gid, "No participants joined", 0)
        return {"status": "no_winner", "giveaway": row or await repo.get(gid)}

    ids = [int(e["user_id"]) for e in entries]
    by_id = {int(e["user_id"]): e for e in entries}
    scores = await scores_for(ids)
    skipped = {"left": 0, "owns": 0, "full": 0, "no_account": 0}
    for uid in weighted_order(ids, scores):
        member = await is_main_member(uid, use_cache=False)
        if member is False:      # unknown (None) is trusted: they passed the check when entering
            skipped["left"] += 1
            continue
        result = await _credit_and_finish(g, by_id[uid], card, scores.get(uid, 0.0), len(ids))
        if result == "ok":
            return {"status": "won", "giveaway": await repo.get(gid)}
        if result == "stale":
            return {"status": "stale", "giveaway": await repo.get(gid)}
        skipped[result] = skipped.get(result, 0) + 1

    note = "No eligible participant left (" + ", ".join(f"{v} {k}" for k, v in skipped.items() if v) + ")"
    row = await repo.mark_ended_without_winner(gid, note, len(ids))
    return {"status": "no_winner", "giveaway": row or await repo.get(gid)}
