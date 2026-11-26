from __future__ import annotations

import asyncio
import functools
import html
import time
import traceback
from typing import Any

from app import app
from database.playso_repo import get_match, set_message_id, set_state, update_locked
from database.squads_repo import get_team_squad
from engines.lineup_engine import load_current_xi
from services.match_summary import send_match_summary
from utils.mentions import mention_html, mention_name_only_html
from utils.stadium import random_stadium
from utils.match_scoring_link import match_scoring_keyboard
from utils.temperature import random_weather
from database.stadium_images_repo import get_stadium_image, save_stadium_image
from services.search import find_stadium_image_url
from engines.play_engine import pitch_label
from services.card_identity import display_card_name

NO_KEYBOARD = {"inline_keyboard": []}
_LOCKS: dict[int, asyncio.Lock] = {}
_DEDUP: dict[tuple[int, str, int, int], float] = {}


PERSIST_KEYS = ("origin_engine", "origin_match_id", "origin_chat_id", "origin_innings", "origin_pitch",
                "origin_xp_awarded", "super_over_rounds", "stadium", "weather", "origin_squads")


def persist_keys(state: dict[str, Any] | None) -> dict[str, Any]:
    """State that must survive every innings / round reset of a Super Over.

    ``set_state`` REPLACES the whole JSON, so without this the link to the original match (origin_*) was lost
    after the first Super Over innings and the original match could never be finished (stuck game).
    """
    state = state or {}
    return {k: state[k] for k in PERSIST_KEYS if k in state}


def mode_title(state: dict[str, Any] | None) -> str:
    origin = str((state or {}).get("origin_engine") or "PLAYSO")
    return origin if origin in {"PLAY", "PLAYINT", "PLAYIPL", "PLAYWPL", "PLAYWBBL", "PLAYBBL", "PLAYILT"} else "PLAYSO"


def _innings_score(state: dict[str, Any]) -> str:
    """Score line shown on every Super Over prompt (also imported by /resume)."""
    balls = int(state.get("legal_balls") or 0)
    return f"{int(state.get('runs') or 0)}/{int(state.get('wickets') or 0)}  ({balls // 6}.{balls % 6} ov)"


# ---------------------------------------------------------------------------------------------------------------------
# Self-healing prompts.
#
# Every Super Over step first saves the NEXT stage to the database and only then edits / sends the Telegram message
# that carries the next buttons.  If that single Telegram call failed (timeout, flood-wait, a deleted message ...) the
# match stayed in the new stage with no live buttons - the "Super Over got stuck in between" bug.  The helpers below
# retry the call, fall back to a brand-new prompt, and as a last resort re-send the current stage prompt.
# ---------------------------------------------------------------------------------------------------------------------
async def _retry(factory, *, attempts: int = 3, base_delay: float = 0.7):
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await factory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                      # noqa: BLE001 - any Telegram/network failure
            last = exc
            if i + 1 < attempts:
                await asyncio.sleep(base_delay * (i + 1))
    assert last is not None
    raise last


async def send_prompt(chat_id: int, match_id: int, text: str, markup) -> int:
    """Send a NEW prompt message (with retries) and make it the live message of this Super Over."""
    sent = await _retry(lambda: app.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup), attempts=4)
    new_id = int((sent or {}).get("message_id") or 0)
    if not new_id:
        raise RuntimeError("Telegram returned no message id for the Super Over prompt")
    await set_message_id(int(match_id), new_id)
    return new_id


async def edit_prompt(chat_id: int, message_id: int, match_id: int, text: str, markup) -> int:
    """Edit the live prompt in place.  If Telegram refuses, replace it with a fresh prompt so buttons never go dead."""
    try:
        await _retry(lambda: app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=markup),
                     attempts=2, base_delay=0.5)
        return int(message_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                          # noqa: BLE001
        print(f"[playso] could not edit prompt {message_id} of match {match_id} ({exc!r}); sending a fresh prompt instead")
    try:
        await app.delete_message(chat_id, message_id)
    except Exception:                                                 # noqa: BLE001
        pass
    return await send_prompt(chat_id, match_id, text, markup)


async def recover_prompt(match_id: int) -> bool:
    """Re-send whatever the Super Over is waiting for.  Used by the callback safety net and by /resume."""
    async with match_lock(int(match_id)):
        match = await get_match(int(match_id))
        if not match:
            return False
        status = str(match.get("status") or "")
        state = match.get("state") or {}
        chat_id = int(match["chat_id"])
        from handlers.playso.live import advance_after_ball, advance_from_break
        if status == "innings_break":
            await advance_from_break(chat_id, dict(match))
            return True
        if status == "live" and str(state.get("stage") or "") == "result":
            await advance_after_ball(chat_id, dict(match), dict(state))
            return True
        if status not in {"lineup", "live"}:
            return False
        from handlers.resume import _render_resume_scorecard, _resume_markup
        old_id = match.get("message_id")
        if old_id:
            try:
                await app.delete_message(chat_id, int(old_id))
            except Exception:                                         # noqa: BLE001
                pass
        await send_prompt(chat_id, int(match["match_id"]), _render_resume_scorecard("PLAYSO", match), _resume_markup("PLAYSO", match))
        await sync_clock(int(match["match_id"]))
        return True


def self_heal(fn):
    """Decorator for Super Over callbacks: a half-finished step re-sends the current prompt instead of leaving the
    match without buttons.  The traceback is still printed so the real error stays visible in the logs."""
    @functools.wraps(fn)
    async def wrapper(callback_query, *args, **kwargs):
        try:
            return await fn(callback_query, *args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                      # noqa: BLE001
            print(f"[playso] callback {callback_query.get('data')!r} failed half-way: {exc!r}")
            traceback.print_exc()
            try:
                mid = int(str(callback_query.get("data") or "").split(":")[1])
                await recover_prompt(mid)
            except Exception as exc2:                                 # noqa: BLE001
                print(f"[playso] could not re-send the Super Over prompt: {exc2!r}")
    return wrapper


async def sync_clock(match_id: int, actor_id: int | None = None) -> None:
    """(Re)start the 3-minute inactivity clock for whoever is expected to act in this Super Over."""
    try:
        from utils.game_inactivity import sync_after_change
        await sync_after_change("PLAYSO", int(match_id), actor_id)
    except Exception as exc:
        print(f"[playso] inactivity sync failed for {match_id}: {exc!r}")


def cancel_inactivity_match(engine: str, match_id: int) -> None:
    try:
        from utils.game_inactivity import cancel_match
        cancel_match(engine, int(match_id))
    except Exception:
        pass


def eligible_bowlers(xi: list[dict[str, Any]], locked: int | None = None) -> list[int]:
    """Player ids that may bowl. A locked bowler is released if nobody else could bowl (no dead end)."""
    roles = [int(p.get("player_id") or 0) for p in xi if str(p.get("role") or "") in {"Bowler", "AllRounder"}]
    if not roles:                                                                                    
        roles = [int(p.get("player_id") or 0) for p in xi]
    free = [pid for pid in roles if locked is None or pid != int(locked)]
    return free or roles


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
        return f"{emoji} <b>{html.escape(display_card_name(player))}</b> • OVR {int(player.get('bowl_level') or 0)}"
    return f"{emoji} <b>{html.escape(display_card_name(player))}</b> • OVR {int(player.get('bat_level') or 0)}"


async def current_xi(user_id: int) -> list[dict[str, Any]]:
    return list(await load_current_xi(int(user_id)) or [])[:11]


async def active_external_match(chat_id: int, user_id: int) -> tuple[bool, str]:
    from database.play_repo import get_active_match_in_chat as play_chat, get_active_match_for_user as play_user
    from database.playint_repo import get_active_match_in_chat as int_chat, get_active_match_for_user as int_user
    from database.playipl_repo import get_active_match_in_chat as ipl_chat, get_active_match_for_user as ipl_user
    from database.playwpl_repo import get_active_match_in_chat as wpl_chat, get_active_match_for_user as wpl_user
    from database.playwbbl_repo import get_active_match_in_chat as wbbl_chat, get_active_match_for_user as wbbl_user
    from database.playbbl_repo import get_active_match_in_chat as bbl_chat, get_active_match_for_user as bbl_user
    from database.playilt_repo import get_active_match_in_chat as ilt_chat, get_active_match_for_user as ilt_user
    checks = [(play_chat, "PLAY"), (int_chat, "PLAYINT"), (ipl_chat, "PLAYIPL"), (wpl_chat, "PLAYWPL"), (wbbl_chat, "PLAYWBBL"), (bbl_chat, "PLAYBBL"), (ilt_chat, "PLAYILT")]
    for fn, name in checks:
        try:
            if await fn(chat_id):
                return True, name
        except Exception:
            pass
    for fn, name in [(play_user, "PLAY"), (int_user, "PLAYINT"), (ipl_user, "PLAYIPL"), (wpl_user, "PLAYWPL"), (wbbl_user, "PLAYWBBL"), (bbl_user, "PLAYBBL"), (ilt_user, "PLAYILT")]:
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
    score_keyboard = match_scoring_keyboard("PLAYSO", int(match["match_id"]))
    cached_file_id = await get_stadium_image(stadium)
    if cached_file_id:
        try:
            sent = await app.send_photo(chat_id, photo=cached_file_id, caption=text, parse_mode="HTML", reply_markup=score_keyboard)
            return {"stadium": stadium, "weather": weather, "message_id": int(sent.get("message_id") or 0)}
        except Exception as exc:
            print(f"[playso] Cached stadium image send failed: {exc!r}")
    try:
        image_url = await find_stadium_image_url(stadium)
        if image_url:
            sent = await app.send_photo(chat_id, photo=image_url, caption=text, parse_mode="HTML", reply_markup=score_keyboard)
            file_id = (sent.get("photo") or {}).get("file_id")
            if file_id:
                await save_stadium_image(stadium, file_id)
            return {"stadium": stadium, "weather": weather, "message_id": int(sent.get("message_id") or 0)}
    except Exception as exc:
        print(f"[playso] Stadium image lookup/send failed: {exc!r}")
    sent = await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=score_keyboard)
    return {"stadium": stadium, "weather": weather, "message_id": int(sent.get("message_id") or 0)}
