from __future__ import annotations

print("play/lineup.py loaded")

import asyncio

from handlers.registry import register_callback
from app import app
from database.play_repo import get_match, set_message_id
from engines.lineup_engine import load_current_xi
from utils.mentions import mention_html
from buttons.play_buttons import start_match_keyboard
from services.lineup_rich import build_fallback, build_rich, send_lineup
from utils.user_identity import get_user_identity
from .live import begin_match_flow
from utils.background import spawn

NO_KEYBOARD = {"inline_keyboard": []}

                                                                   
                                                                   
                                                              
                                                                 
                                                                      
_STARTING: set[int] = set()


async def _xis(match: dict) -> tuple[list[dict], list[dict]]:
    """Both Playing XIs - the auctioned ones in an auction tournament, otherwise each player's own."""
    try:
        from services.tournament_engines import custom_squads
        squads = await custom_squads(int(match["match_id"]))
    except Exception as exc:
        print(f"[play] tournament XI lookup failed: {exc!r}")
        squads = None
    if squads and int(match["challenger_id"]) in squads and int(match["opponent_id"]) in squads:
        return squads[int(match["challenger_id"])]["xi"], squads[int(match["opponent_id"])]["xi"]
    return (await load_current_xi(match["challenger_id"]) or []), (await load_current_xi(match["opponent_id"]) or [])


async def _team_card(match: dict, side: str, xi: list[dict]) -> tuple[str, str]:
    uid = int(match[f"{side}_id"])
    owner = match.get(f"{side}_name") or "Player"
    identity = await get_user_identity(uid, owner)
    owner_html = mention_html(uid, match.get(f"{side}_username"), owner)
    team = identity["team_name"]
    return build_rich(team, owner, xi), build_fallback(team, owner_html, xi)


async def send_playing_xi(chat_id, match: dict) -> None:
    challenger_xi, opponent_xi = await _xis(match)
    first_rich, first_plain = await _team_card(match, "challenger", challenger_xi)
    second_rich, second_plain = await _team_card(match, "opponent", opponent_xi)
    await send_lineup(chat_id, first_rich, first_plain)
    sent = await send_lineup(chat_id, second_rich, second_plain, reply_markup=start_match_keyboard(match["match_id"]))
    await set_message_id(match["match_id"], sent["message_id"])


async def _lock_start_button(chat_id, message_id) -> None:
    try:
        await app._call_bot_api(
            "editMessageReplyMarkup",
            json_payload={"chat_id": int(chat_id), "message_id": int(message_id), "reply_markup": NO_KEYBOARD},
        )
    except Exception as exc:
        print(f"[play] could not lock start button: {exc!r}")


@register_callback("play_start")
async def on_play_start(callback_query):
    match_id = int(callback_query["data"].split(":")[1])
    presser = callback_query["from"]

    match = await get_match(match_id)
    if not match:
        await app.answer_callback_query(callback_query["id"], "This match no longer exists.", show_alert=True)
        return

    if int(presser["id"]) not in (int(match["challenger_id"]), int(match["opponent_id"])):
        await app.answer_callback_query(
            callback_query["id"], "Only the two players in this match can start it!", show_alert=True,
        )
        return

    if match_id in _STARTING:
        await app.answer_callback_query(callback_query["id"], "The match is already starting!", show_alert=True)
        return
    _STARTING.add(match_id)

    await app.answer_callback_query(callback_query["id"], "🏁 Match starting soon!", show_alert=True)

    await _lock_start_button(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"])

    spawn(begin_match_flow(callback_query["message"]["chat"]["id"], match), name='begin-match-flow')
