from __future__ import annotations

import html
import re
from handlers.registry import register, register_callback
from app import app
from buttons.playso_buttons import challenge_keyboard
from database.query import fetchrow
from database.playso_repo import create_match, get_match, set_message_id, update_locked, get_active_match_in_chat, get_active_match_for_user
from database.squads_repo import get_team_squad
from engines.lineup_engine import load_current_xi
from handlers.playso.common import active_external_match, callback_message_is_current, match_lock
from utils.mentions import mention_html
from utils.timers import start_timer, cancel_timer

NO_KEYBOARD = {"inline_keyboard": []}


def _target(text: str, reply: dict | None) -> dict | None:
    opponent = (reply or {}).get("from")
    if opponent and opponent.get("id"):
        return opponent
    parts = (text or "").split()
    for part in parts[1:]:
        token = part.lstrip("@")
        if token.isdigit():
            return {"id": int(token)}
        if part.startswith("@"):
            return {"username": token}
    return None


async def _resolve_target(message: dict) -> dict | None:
    target = _target(message.get("text", ""), message.get("reply_to_message"))
    if not target:
        return None
    if target.get("id") and target.get("first_name") is not None:
        return target
    if target.get("id"):
        row = await fetchrow("SELECT user_id, username, first_name FROM users WHERE user_id=$1;", int(target["id"]))
    else:
        row = await fetchrow("SELECT user_id, username, first_name FROM users WHERE LOWER(username)=LOWER($1) LIMIT 1;", str(target["username"]))
    if not row:
        return None
    return {"id": int(row["user_id"]), "username": row["username"], "first_name": row["first_name"]}


def _challenge_text(a: str, b: str) -> str:
    return (
        "<b>╭━━〔 ⚡ PLAYSO • SUPER OVER 〕━━╮\n\n"
        f"⚔️  {a}\n             VS\n🔥  {b}\n\n"
        "┌─ MATCH DETAILS ─────┐</b>\n"
        "<blockquote><b>│ ⚡ Format ➤ Super Over\n│ 🎮 Mode   ➤ 1v1\n│ 🏏 Innings ➤ 1 Over / Side\n│ 👥 Teams ➤ Playing XI</b></blockquote>\n"
        "<b>└─────────────────┘\n"
        "💬 Six legal balls. One bowler. Three batters.\n\n"
        "Ready for the shortest battle? ⚡\n\n"
        "╰━━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


@register("playso")
async def playso_command(message):
    chat_id = int(message["chat"]["id"])
    sender = message.get("from", {})
    challenger_id = int(sender.get("id") or 0)
    opponent = await _resolve_target(message)
    if not opponent:
        await app.send_message(chat_id, "<b>⚠️ Reply to a user, add @username, or provide a user ID.</b>", parse_mode="HTML"); return
    if opponent.get("is_bot"):
        await app.send_message(chat_id, "<b>⚠️ You can't challenge a bot.</b>", parse_mode="HTML"); return
    if int(opponent["id"]) == challenger_id:
        await app.send_message(chat_id, "<b>⚠️ You can't challenge yourself.</b>", parse_mode="HTML"); return
    for uid, label in [(challenger_id, "Your"), (int(opponent["id"]), "Your opponent's")]:
        xi = await load_current_xi(uid)
        if len(xi) < 11:
            await app.send_message(chat_id, f"<b>⚠️ {label} Playing XI must contain 11 players for PLAYSO.</b>", parse_mode="HTML"); return
        busy, mode = await active_external_match(chat_id, uid)
        if busy:
            await app.send_message(chat_id, f"<b>⚠️ {mention_html(uid, sender.get('username') if uid == challenger_id else opponent.get('username'), sender.get('first_name') if uid == challenger_id else opponent.get('first_name'))} is already in {mode}.</b>", parse_mode="HTML"); return
        active = await get_active_match_for_user(uid)
        if active:
            await app.send_message(chat_id, "<b>⚠️ One of these players is already in a PLAYSO match.</b>", parse_mode="HTML"); return
    active_chat = await get_active_match_in_chat(chat_id)
    if active_chat:
        await app.send_message(chat_id, "<b>⚠️ A PLAYSO match is already active in this chat.</b>", parse_mode="HTML"); return
    challenger = {"id": challenger_id, "username": sender.get("username"), "first_name": sender.get("first_name")}
    match = await create_match(chat_id, challenger, opponent)
    a = mention_html(challenger_id, challenger["username"], challenger["first_name"])
    b = mention_html(int(opponent["id"]), opponent.get("username"), opponent.get("first_name"))
    sent = await app.send_message(chat_id, _challenge_text(a, b), parse_mode="HTML", reply_markup=challenge_keyboard(int(match["match_id"])))
    await set_message_id(int(match["match_id"]), int(sent["message_id"]))

    async def timeout():
        cur = await get_match(int(match["match_id"]))
        if not cur or cur["status"] != "pending": return
        await update_locked(int(match["match_id"]), {"pending"}, lambda d,s: ({"expired": True}, "expired"))
        try:
            await app.edit_message_text(chat_id, int(cur["message_id"]), "<b>⏳ PLAYSO challenge expired.\n\nNo Super Over was started.</b>", parse_mode="HTML", reply_markup=NO_KEYBOARD)
        except Exception: pass
    start_timer("playso_challenge", int(match["match_id"]), lambda remaining: None, timeout, total_seconds=60)


@register_callback("playso_accept")
async def playso_accept(callback_query):
    match_id = int(callback_query["data"].split(":")[1]); uid = int(callback_query["from"]["id"])
    chat_id = int(callback_query["message"]["chat"]["id"]); msg_id = int(callback_query["message"]["message_id"])
    async with match_lock(match_id):
        match = await get_match(match_id)
        if not match or match["status"] != "pending":
            await app.answer_callback_query(callback_query["id"], "This challenge is no longer active.", show_alert=True); return
        if not callback_message_is_current(dict(match), callback_query):
            await app.answer_callback_query(callback_query["id"], "This action is no longer active.", show_alert=True); return
        if uid != int(match["opponent_id"]):
            await app.answer_callback_query(callback_query["id"], "This challenge is not for you.", show_alert=True); return
        if match.get("expires_at") and match["expires_at"] <= __import__("datetime").datetime.now(__import__("datetime").timezone.utc).replace(tzinfo=None):
            await app.answer_callback_query(callback_query["id"], "This challenge is no longer active.", show_alert=True); return
        try:
            await update_locked(match_id, {"pending"}, lambda d,s: ({"accepted": True}, "accepted"))
        except Exception:
            await app.answer_callback_query(callback_query["id"], "Unable to accept this challenge right now.", show_alert=True); return
        cancel_timer("playso_challenge", match_id)
        await app.answer_callback_query(callback_query["id"], "Challenge accepted!")
        try: await app.delete_message(chat_id, msg_id)
        except Exception: pass
        a = mention_html(match["challenger_id"], match.get("challenger_username"), match.get("challenger_name"))
        b = mention_html(match["opponent_id"], match.get("opponent_username"), match.get("opponent_name"))
        await app.send_message(chat_id, f"<b>✅ Challenge Accepted!\n\n{a} 🆚 {b}\n\n⚡ Your Super Over is about to begin.</b>", parse_mode="HTML")
        from .pitch import send_pitch_selection
        await send_pitch_selection(chat_id, dict(match))


@register_callback("playso_decline")
async def playso_decline(callback_query):
    match_id = int(callback_query["data"].split(":")[1]); uid = int(callback_query["from"]["id"])
    match = await get_match(match_id); chat_id = int(callback_query["message"]["chat"]["id"]); msg_id = int(callback_query["message"]["message_id"])
    if not match or match["status"] != "pending":
        await app.answer_callback_query(callback_query["id"], "This challenge is no longer active.", show_alert=True); return
    if not callback_message_is_current(dict(match), callback_query):
        await app.answer_callback_query(callback_query["id"], "This action is no longer active.", show_alert=True); return
    if uid != int(match["opponent_id"]):
        await app.answer_callback_query(callback_query["id"], "This challenge is not for you.", show_alert=True); return
    await update_locked(match_id, {"pending"}, lambda d,s: ({"declined": True}, "declined"))
    cancel_timer("playso_challenge", match_id)
    await app.answer_callback_query(callback_query["id"], "Challenge declined.")
    try: await app.edit_message_text(chat_id, msg_id, "<b>❌ PLAYSO challenge declined.</b>", parse_mode="HTML", reply_markup=NO_KEYBOARD)
    except Exception: pass
