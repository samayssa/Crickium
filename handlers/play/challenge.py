from __future__ import annotations

print("play/challenge.py loaded")

import html

from handlers.registry import register, register_callback
from app import app
from database.query import fetchrow
from database.play_repo import (
    create_match, get_match, set_message_id, update_status,
    get_active_match_in_chat, get_active_match_for_user,
)
from database.playint_repo import get_active_match_in_chat as get_playint_match_in_chat, get_active_match_for_user as get_playint_match_for_user
from utils.mentions import mention_html
from buttons.play_buttons import challenge_keyboard
from utils.debut_gate import has_minimum_team, get_playing_xi_status

from .pitch import send_pitch_selection

NO_KEYBOARD = {"inline_keyboard": []}


def _challenge_text(challenger_mention: str, opponent_mention: str) -> str:
    return (
        "<b>╭━━〔 🏏 PLAY MATCH 〕━━╮\n\n"
        f"⚔️  {challenger_mention}\n"
        "             VS\n"
        f"🔥  {opponent_mention}\n\n"
        "┌─ MATCH DETAILS ─────┐</b>\n"
        "<blockquote><b>"
        "│ 🏆 Format ➤  T20\n"
        "│ 🎮 Mode   ➤  1v1\n"
        "│ 👥 Teams  ➤  Playing XI"
        "</b></blockquote>\n"
        "<b>└─────────────────┘\n"
        "💬 The challenge is set.\n"
        "One match. Two squads. One winner. 🏆\n\n"
        "Ready to take the field?\n\n"
        "╰━━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def parse_username_arg(text: str) -> str | None:
    """Pulls an @username out of the /play command text itself, e.g.
    '/play @someone'. This is the second way to challenge someone -
    replying to their message is the first."""
    parts = (text or "").split()
    for part in parts[1:]:
        if part.startswith("@") and len(part) > 1:
            return part[1:]
    return None


@register("play")
async def play_command(message):
    chat_id = message["chat"]["id"]
    chat_title = message["chat"].get("title") or "this group"
    from_user = message.get("from", {})
    challenger_id = from_user.get("id")
    challenger_username = from_user.get("username")
    challenger_name = from_user.get("first_name")

    if not await has_minimum_team(int(challenger_id)):
        await app.send_message(
            chat_id,
            "<b>⚠️ You need a minimum 11 players team to challenge.</b>",
            parse_mode="HTML",
        )
        return

    xi_ok, _xi_reason = await get_playing_xi_status(int(challenger_id))
    if not xi_ok:
        await app.send_message(
            chat_id,
            "<b>⚠️ Your Playing XI is not perfect to challenge this game.</b>\n\n"
            "You need min 3 batsman, 1 wicket-keeper, 3 all-rounder and 3 bowlers.",
            parse_mode="HTML",
        )
        return

    # --- Rule 1: only one match can be live in a group at a time. ---
    active_in_chat = await get_active_match_in_chat(chat_id)
    if active_in_chat:
        await app.send_message(
            chat_id,
            (
                f"<b>⚠️ A game is already going on in {html.escape(str(chat_title))}.\n"
                "You need to wait for the current game to finish before starting a new one.</b>"
            ),
            parse_mode="HTML",
        )
        return

    active_playint_in_chat = await get_playint_match_in_chat(chat_id)
    if active_playint_in_chat:
        await app.send_message(chat_id, f"<b>⚠️ A PlayInt game is already going on in {html.escape(str(chat_title))}. Finish it before starting a new game.</b>", parse_mode="HTML")
        return

    # --- Rule 2: the person sending /play can't already be mid-game. ---
    active_for_challenger = await get_active_match_for_user(challenger_id)
    if active_for_challenger:
        await app.send_message(
            chat_id,
            "<b>⚠️ You're already in a game.\nPlease complete your previous one to start a new one.</b>",
            parse_mode="HTML",
        )
        return

    active_playint_for_challenger = await get_playint_match_for_user(challenger_id)
    if active_playint_for_challenger:
        await app.send_message(chat_id, "<b>⚠️ You're already in a PlayInt game. Please complete it before starting a new game.</b>", parse_mode="HTML")
        return

    # --- Resolve the opponent: reply-to-message OR /play @username. ---
    reply_to = message.get("reply_to_message")
    opponent = (reply_to or {}).get("from")

    opponent_id = None
    opponent_username = None
    opponent_name = None

    if reply_to and opponent and opponent.get("id"):
        if opponent.get("is_bot"):
            await app.send_message(chat_id, "<b>⚠️ You can't challenge a bot!</b>", parse_mode="HTML")
            return
        opponent_id = opponent.get("id")
        opponent_username = opponent.get("username")
        opponent_name = opponent.get("first_name")
    else:
        username_arg = parse_username_arg(message.get("text", ""))
        if not username_arg:
            await app.send_message(
                chat_id,
                (
                    "<b>⚠️ To challenge someone, either:\n"
                    "• Reply to their message with /play, or\n"
                    "• Use /play @username</b>"
                ),
                parse_mode="HTML",
            )
            return

        row = await fetchrow(
            "SELECT user_id, username, first_name FROM users WHERE username = $1;",
            username_arg,
        )
        if not row:
            await app.send_message(
                chat_id,
                (
                    f"<b>⚠️ I don't have @{html.escape(username_arg)} on record yet.\n"
                    "Ask them to send a message in this group first, or reply to their message with /play instead.</b>"
                ),
                parse_mode="HTML",
            )
            return

        opponent_id = row["user_id"]
        opponent_username = row["username"] or username_arg
        opponent_name = row["first_name"]

    if int(opponent_id) == int(challenger_id):
        await app.send_message(chat_id, "<b>⚠️ You can't challenge yourself!</b>", parse_mode="HTML")
        return

    if not await has_minimum_team(int(opponent_id)):
        await app.send_message(
            chat_id,
            "<b>⚠️ You need a minimum 11 players team to join this game.</b>",
            parse_mode="HTML",
        )
        return

    xi_ok, _xi_reason = await get_playing_xi_status(int(opponent_id))
    if not xi_ok:
        await app.send_message(
            chat_id,
            "<b>⚠️ Your opponent's Playing XI is not perfect to play this game.</b>\n\n"
            "They need min 3 batsman, 1 wicket-keeper, 3 all-rounder and 3 bowlers.",
            parse_mode="HTML",
        )
        return

    active_playint_for_opponent = await get_playint_match_for_user(opponent_id)
    if active_playint_for_opponent:
        opponent_mention = mention_html(opponent_id, opponent_username, opponent_name)
        await app.send_message(chat_id, f"<b>⚠️ {opponent_mention} is already in another game. Try again once that game finishes.</b>", parse_mode="HTML")
        return

    # The opponent can't be mid-game elsewhere either.
    active_for_opponent = await get_active_match_for_user(opponent_id)
    if active_for_opponent:
        opponent_mention = mention_html(opponent_id, opponent_username, opponent_name)
        await app.send_message(
            chat_id,
            f"<b>⚠️ {opponent_mention} is already in another game.\nTry challenging them again once that one finishes.</b>",
            parse_mode="HTML",
        )
        return

    match = await create_match(
        chat_id, challenger_id, challenger_username, challenger_name,
        opponent_id, opponent_username, opponent_name,
    )

    challenger_mention = mention_html(challenger_id, challenger_username, challenger_name)
    opponent_mention = mention_html(opponent_id, opponent_username, opponent_name)

    text = _challenge_text(challenger_mention, opponent_mention)
    keyboard = challenge_keyboard(match["match_id"])
    sent = await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)
    await set_message_id(match["match_id"], sent["message_id"])

    print(f"[play] match_id={match['match_id']} challenger={challenger_id} opponent={opponent_id}")


@register_callback("play_accept")
async def on_play_accept(callback_query):
    match_id = int(callback_query["data"].split(":")[1])
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]

    match = await get_match(match_id)
    if not match or match["status"] != "pending":
        await app.answer_callback_query(callback_query["id"], "This challenge is no longer active.", show_alert=True)
        return

    if int(presser["id"]) != int(match["opponent_id"]):
        await app.answer_callback_query(callback_query["id"], "This challenge isn't for you!", show_alert=True)
        return

    if not await has_minimum_team(int(presser["id"])):
        await app.answer_callback_query(
            callback_query["id"],
            "You need a minimum 11 players team to join this game.",
            show_alert=True,
        )
        return

    xi_ok, _xi_reason = await get_playing_xi_status(int(presser["id"]))
    if not xi_ok:
        await app.answer_callback_query(
            callback_query["id"],
            "⚠️ Your Playing XI is not perfect. Need min 3 batsman, 1 wicket-keeper, 3 all-rounder and 3 bowlers.",
            show_alert=True,
        )
        return

    if await get_playint_match_for_user(int(presser["id"])):
        await app.answer_callback_query(callback_query["id"], "You're already in a PlayInt game. Finish it first.", show_alert=True)
        return

    await update_status(match_id, "accepted")
    await app.answer_callback_query(callback_query["id"], "Challenge accepted!")

    # The original challenge message is deleted; a fresh confirmation
    # message takes its place instead of just editing it in place.
    try:
        await app.delete_message(chat_id, message_id)
    except Exception as exc:
        print(f"[play] Failed to delete challenge message: {exc!r}")

    challenger_mention = mention_html(match["challenger_id"], match["challenger_username"], match["challenger_name"])
    opponent_mention = mention_html(presser["id"], presser.get("username"), presser.get("first_name"))
    await app.send_message(
        chat_id,
        (
            "<b>✅ Challenge Accepted!\n\n"
            f"{challenger_mention} 🆚 {opponent_mention}\n"
            "Get ready — the match begins now! 🏏</b>"
        ),
        parse_mode="HTML",
    )

    await send_pitch_selection(chat_id, match)


@register_callback("play_decline")
async def on_play_decline(callback_query):
    match_id = int(callback_query["data"].split(":")[1])
    presser = callback_query["from"]
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]

    match = await get_match(match_id)
    if not match or match["status"] != "pending":
        await app.answer_callback_query(callback_query["id"], "This challenge is no longer active.", show_alert=True)
        return

    if int(presser["id"]) != int(match["opponent_id"]):
        await app.answer_callback_query(callback_query["id"], "This challenge isn't for you!", show_alert=True)
        return

    await update_status(match_id, "declined")
    await app.answer_callback_query(callback_query["id"], "Challenge declined.")

    try:
        await app.delete_message(chat_id, message_id)
    except Exception as exc:
        print(f"[play] Failed to delete challenge message: {exc!r}")

    await app.send_message(chat_id, "<b>❌ Challenge Declined.</b>", parse_mode="HTML")
