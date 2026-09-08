from __future__ import annotations

import asyncio
import html
import time
from typing import Any

from app import app
from database.playso_repo import get_match, set_message_id, set_state, update_locked
from database.squads_repo import get_team_squad
from engines.lineup_engine import load_current_xi
from services.match_summary import send_match_summary
from utils.mentions import mention_html, mention_name_only_html
from utils.stadium import random_stadium
from utils.temperature import random_weather
from database.stadium_images_repo import get_stadium_image, save_stadium_image
from services.search import find_stadium_image_url
from engines.play_engine import pitch_label

NO_KEYBOARD = {"inline_keyboard": []}
_LOCKS: dict[int, asyncio.Lock] = {}
_DEDUP: dict[tuple[int, str, int, int], float] = {}


def match_lock(match_id: int) -> asyncio.Lock:
    return _LOCKS.setdefault(int(match_id), asyncio.Lock())


def member(match: dict[str, Any], user_id: int) -> dict[str, Any]:
    if int(user_id) == int(match["challenger_id"]):
        return {"id": int(match["challenger_id"]), "username": match.get("challenger_username"), "name": match.get("challenger_name")}
    return {"id": int(match["opponent_id"]), "username": match.get("opponent_username"), "name": match.get("opponent_name")}


def other_user_id(match: dict[str, Any], user_id: int) -> int:
    return int(match["opponent_id"] if int(user_id) == int(match["challenger_id"]) else match["challenger_id"])


def html_user(match: dict[str, Any], user_id: int) -> str:
    m = member(match, user_id)
    return mention_html(m["id"], m.get("username"), m.get("name"))



def callback_message_is_current(match: dict[str, Any], callback_query: dict[str, Any]) -> bool:
    msg = callback_query.get("message") or {}
    try:
        return int(msg.get("message_id") or 0) == int(match.get("message_id") or 0)
    except Exception:
        return False


def dedup_selection_click(match_id: int, stage: str, user_id: int, choice_id: int, window: float = 0.8) -> bool:
    """Return True only for the first rapid tap of the same selection button."""
    now = time.monotonic()
    key = (int(match_id), str(stage), int(user_id), int(choice_id))
    previous = _DEDUP.get(key)
    _DEDUP[key] = now
    # Cheap cleanup for the in-process map.
    cutoff = now - max(2.0, window * 4)
    for k, stamp in list(_DEDUP.items()):
        if stamp < cutoff:
            _DEDUP.pop(k, None)
    return previous is None or (now - previous) >= window


def role_emoji(role: str) -> str:
    return {"Batsman": "🏏", "Wicketkeeper": "🧤", "AllRounder": "🔄", "Bowler": "🎯"}.get(str(role or ""), "🏏")


def bowling_family(player: dict[str, Any]) -> str:
    style = str(player.get("bowling_hand") or "").upper()
    if style.endswith("O"):
        return "offspin"
    if style.endswith("L"):
        return "legspin"
    return "pace"


def bowling_family_label(player: dict[str, Any]) -> str:
    return {"pace": "PACE", "offspin": "OFF-SPIN", "legspin": "LEG-SPIN"}.get(bowling_family(player), "PACE")


def player_line(player: dict[str, Any], *, bowler: bool = False, locked: bool = False) -> str:
    role = str(player.get("role") or "")
    emoji = role_emoji(role)
    if bowler:
        return f"{emoji} <b>{html.escape(str(player.get('name') or 'Player'))}</b> • OVR {int(player.get('bowl_level') or 0)}"
    return f"{emoji} <b>{html.escape(str(player.get('name') or 'Player'))}</b> • OVR {int(player.get('bat_level') or 0)}"


async def current_xi(user_id: int) -> list[dict[str, Any]]:
    return list(await load_current_xi(int(user_id)) or [])[:11]


async def active_external_match(chat_id: int, user_id: int) -> tuple[bool, str]:
    from database.play_repo import get_active_match_in_chat as play_chat, get_active_match_for_user as play_user
    from database.playint_repo import get_active_match_in_chat as int_chat, get_active_match_for_user as int_user
    from database.playipl_repo import get_active_match_in_chat as ipl_chat, get_active_match_for_user as ipl_user
    checks = [(play_chat, "PLAY"), (int_chat, "PLAYINT"), (ipl_chat, "PLAYIPL")]
    for fn, name in checks:
        try:
            if await fn(chat_id):
                return True, name
        except Exception:
            pass
    for fn, name in [(play_user, "PLAY"), (int_user, "PLAYINT"), (ipl_user, "PLAYIPL")]:
        try:
            if await fn(user_id):
                return True, name
        except Exception:
            pass
    return False, ""


async def send_match_ready(chat_id: int, match: dict[str, Any]) -> dict:
    stadium = match.get("stadium") or random_stadium()
    weather = match.get("weather") or random_weather().format()
    challenger = mention_html(match["challenger_id"], match.get("challenger_username"), match.get("challenger_name"))
    opponent = mention_html(match["opponent_id"], match.get("opponent_username"), match.get("opponent_name"))
    winner = challenger if int(match.get("toss_winner_id") or 0) == int(match["challenger_id"]) else opponent
    decision = "BAT" if str(match.get("decision") or "").lower() == "bat" else "BOWL"
    text = (
        "<b>╭━━〔 ⚡ PLAYSO • MATCH READY 〕━━╮</b>\n\n"
        "<b>⚡ SUPER OVER • 6 LEGAL BALLS</b>\n\n"
        f"<b>🏏 {challenger}</b>\n<b>⚔️</b>\n<b>🎯 {opponent}</b>\n\n"
        f"<b>{pitch_label(str(match.get('pitch') or 'green'))} Pitch</b>\n"
        f"<b>🏟️ {html.escape(str(stadium))}</b>\n"
        f"<b>🌡️ {html.escape(str(weather))}</b>\n\n"
        f"<b>🪙 Toss ➤ {winner}</b>\n<b>🎯 Chose to {decision}</b>\n\n"
        "<b>⚡ One over. Six legal balls. No room for mistakes.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )
    await set_state(match["match_id"], {**(match.get("state") or {}), "stadium": stadium, "weather": weather}, status="lineup")
    cached_file_id = await get_stadium_image(stadium)
    if cached_file_id:
        try:
            sent = await app.send_photo(chat_id, photo=cached_file_id, caption=text, parse_mode="HTML")
            return {"stadium": stadium, "weather": weather, "message_id": int(sent.get("message_id") or 0)}
        except Exception as exc:
            print(f"[playso] Cached stadium image send failed: {exc!r}")
    try:
        image_url = await find_stadium_image_url(stadium)
        if image_url:
            sent = await app.send_photo(chat_id, photo=image_url, caption=text, parse_mode="HTML")
            file_id = (sent.get("photo") or {}).get("file_id")
            if file_id:
                await save_stadium_image(stadium, file_id)
            return {"stadium": stadium, "weather": weather, "message_id": int(sent.get("message_id") or 0)}
    except Exception as exc:
        print(f"[playso] Stadium image lookup/send failed: {exc!r}")
    sent = await app.send_message(chat_id, text, parse_mode="HTML")
    return {"stadium": stadium, "weather": weather, "message_id": int(sent.get("message_id") or 0)}
