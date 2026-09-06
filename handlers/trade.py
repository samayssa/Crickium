from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

from app import app
from handlers.registry import register, register_callback
from database.query import fetch, fetchrow, transaction
from database.squads_repo import get_team_squad
from database.play_repo import get_active_match_for_user as get_play_active
from database.playint_repo import get_active_match_for_user as get_playint_active
from database.playipl_repo import get_active_match_for_user as get_playipl_active
from utils.mentions import mention_html
from buttons.social_trade_buttons import squad_player_keyboard, sender_confirm_keyboard, recipient_confirm_keyboard

NO_KEYBOARD = {"inline_keyboard": []}
ACTIVE_TRADE_STATUSES = {"awaiting_sender", "awaiting_recipient"}


def _kind(player: dict[str, Any]) -> str:
    special = player.get("is_special") is True or str(player.get("is_special") or "").lower() in {"1", "true", "yes"}
    return "special" if special or int(player.get("player_id") or 0) < 0 else "global"


def _ovr(player: dict[str, Any]) -> int:
    return max(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))


def _player_label(player: dict[str, Any]) -> str:
    return f"{player.get('name') or 'Player'} • OVR {_ovr(player)}"


def _mention(user: dict) -> str:
    return mention_html(int(user["user_id"]), user.get("username"), user.get("first_name"))


def _trade_offer_text(trade: dict, sender: dict, recipient: dict, player: dict) -> str:
    return (
        "<b>╭━━━〔 🔄 TRADE OFFER 〕━━━╮</b>\n\n"
        f"<blockquote><b>🤝 {_mention(sender)}</b>\n        wants to trade with\n<b>👤 {_mention(recipient)}</b></blockquote>\n\n"
        "<b>📤 PLAYER BEING OFFERED</b>\n\n"
        f"<blockquote>🏏 <b>{html.escape(str(player.get('name') or 'Player'))}</b>\n"
        f"⭐ OVR <b>{_ovr(player)}</b>\n"
        f"{'✨ Special Edition' if _kind(player) == 'special' else '🏏 Standard Card'}</blockquote>\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<blockquote>⚖️ <b>1 Player ↔ 1 Player</b>\n"
        "⏳ Expires in : <b>5 minutes</b>\n"
        "🔐 Trade Status : <b>Ready for Confirmation</b></blockquote>\n\n"
        "<b>Select the player you want to receive in exchange.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _choose_sender_text(sender: dict, recipient: dict) -> str:
    return (
        "<b>╭━━━〔 🔄 TRADE SETUP 〕━━━╮</b>\n\n"
        f"<b>👤 {html.escape(str(sender.get('first_name') or 'Player'))}</b> wants to trade with\n"
        f"<b>🤝 {_mention(recipient)}</b>.\n\n"
        "<b>Which player do you want to trade?</b>\n\n"
        "<b>Select one player from your squad below.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _sender_confirm_text(player: dict, recipient: dict) -> str:
    return (
        "<b>╭━━━〔 ⚠️ CONFIRM TRADE PLAYER 〕━━━╮</b>\n\n"
        f"<blockquote>🏏 <b>{html.escape(str(player.get('name') or 'Player'))}</b>\n⭐ OVR : <b>{_ovr(player)}</b>\n"
        f"👤 Trading with : <b>{_mention(recipient)}</b></blockquote>\n\n"
        f"<b>Are you sure you want to trade {html.escape(str(player.get('name') or 'Player'))}?</b>\n\n"
        "<b>This player will be offered to the other user.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _recipient_choose_text(sender: dict, recipient: dict, offered: dict) -> str:
    return (
        "<b>╭━━━〔 🤝 TRADE REQUEST 〕━━━╮</b>\n\n"
        f"<blockquote><b>👤 {_mention(sender)}</b> wants to trade with you.\n\n"
        f"📤 They are offering : <b>{html.escape(str(offered.get('name') or 'Player'))}</b> • OVR <b>{_ovr(offered)}</b></blockquote>\n\n"
        f"<b>{_mention(recipient)}, which player do you want to give in return?</b>\n\n"
        "<b>Select one player from your squad below.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _recipient_confirm_text(sender: dict, recipient: dict, offered: dict, requested: dict) -> str:
    return (
        "<b>╭━━━〔 ⚠️ CONFIRM TRADE 〕━━━╮</b>\n\n"
        f"<blockquote>📤 {_mention(sender)} gives\n🏏 <b>{html.escape(str(offered.get('name') or 'Player'))}</b> • OVR <b>{_ovr(offered)}</b>\n\n"
        f"📥 {_mention(sender)} receives\n🏏 <b>{html.escape(str(requested.get('name') or 'Player'))}</b> • OVR <b>{_ovr(requested)}</b></blockquote>\n\n"
        f"<b>Are you sure you want to give {html.escape(str(requested.get('name') or 'Player'))} to {_mention(sender)}?</b>\n\n"
        "<b>Both players will be exchanged immediately after confirmation.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _complete_text(sender: dict, recipient: dict, sent: dict, received: dict) -> str:
    return (
        "<b>╭━━━〔 ✅ TRADE COMPLETED 〕━━━╮</b>\n\n"
        f"<blockquote><b>🤝 Trade successfully completed!</b>\n\n👤 <b>{_mention(sender)}</b>\n        ⇄\n👤 <b>{_mention(recipient)}</b></blockquote>\n\n"
        f"<b>📤 {_mention(sender)} GAVE</b>\n\n"
        f"<blockquote>🏏 <b>{html.escape(str(sent.get('name') or 'Player'))}</b>\n⭐ OVR : <b>{_ovr(sent)}</b>\n"
        f"{'✨ Special Edition' if _kind(sent) == 'special' else '🏏 Standard Card'}</blockquote>\n\n"
        f"<b>📥 {_mention(sender)} RECEIVED</b>\n\n"
        f"<blockquote>🏏 <b>{html.escape(str(received.get('name') or 'Player'))}</b>\n⭐ OVR : <b>{_ovr(received)}</b>\n"
        f"{'✨ Special Edition' if _kind(received) == 'special' else '🏏 Standard Card'}</blockquote>\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<blockquote>✅ <b>Status</b> ➤ Completed\n"
        "🔄 <b>Exchange</b> ➤ 1 Player ↔ 1 Player\n"
        "🔐 <b>Ownership</b> ➤ Successfully transferred</blockquote>\n\n"
        "🎉 <b>Both players have changed hands!</b>\n\n"
        "<b>Another deal closed in the Crickium Market. 🔄</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _declined_text(sender: dict, recipient: dict, offered: dict) -> str:
    return (
        "<b>╭━━━〔 ❌ TRADE DECLINED 〕━━━╮</b>\n\n"
        f"<blockquote><b>👤 {_mention(recipient)}</b> declined the trade offer from\n<b>👤 {_mention(sender)}</b>.</blockquote>\n\n"
        "<b>📤 OFFERED</b>\n\n"
        f"<blockquote>🏏 <b>{html.escape(str(offered.get('name') or 'Player'))}</b>\n⭐ OVR : <b>{_ovr(offered)}</b></blockquote>\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "🔓 <b>No players were exchanged.</b>\n\n"
        "<b>Both players remain with their original owners.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _cancelled_text(actor: dict) -> str:
    return (
        "<b>╭━━━〔 🚫 TRADE CANCELLED 〕━━━╮</b>\n\n"
        f"<blockquote><b>{_mention(actor)}</b> cancelled the pending trade.</blockquote>\n\n"
        "🔓 <b>No players were exchanged.</b>\n\n"
        "<b>The cards remain with their original owners.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


async def _active_match(user_id: int) -> bool:
    return bool(await get_play_active(int(user_id)) or await get_playint_active(int(user_id)) or await get_playipl_active(int(user_id)))

async def _used_trade_today(user_id: int) -> bool:
    value = await fetchrow(
        """SELECT 1 AS used FROM trade_requests WHERE status='completed' AND completed_at::date=CURRENT_DATE\n           AND (sender_id=$1 OR recipient_id=$1) LIMIT 1;""", int(user_id)
    )
    return bool(value)

async def _pending_for_user(user_id: int) -> bool:
    value = await fetchrow(
        "SELECT 1 AS pending FROM trade_requests WHERE status = ANY($2::text[]) AND (sender_id=$1 OR recipient_id=$1) LIMIT 1;",
        int(user_id), list(ACTIVE_TRADE_STATUSES),
    )
    return bool(value)

async def _resolve_target(message: dict) -> dict | None:
    reply = message.get("reply_to_message") or {}
    user = reply.get("from") or {}
    if user.get("id") and not user.get("is_bot"):
        row = await fetchrow("SELECT user_id, username, first_name FROM users WHERE user_id=$1;", int(user["id"]))
        return dict(row) if row else None
    parts = str(message.get("text") or "").split()
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    if arg.startswith("@"):
        row = await fetchrow("SELECT user_id, username, first_name FROM users WHERE LOWER(username)=LOWER($1) LIMIT 1;", arg[1:])
    elif arg.isdigit():
        row = await fetchrow("SELECT user_id, username, first_name FROM users WHERE user_id=$1;", int(arg))
    else:
        return None
    return dict(row) if row else None


def _player_arg(message: dict, used_target_in_arg: bool) -> str:
    parts = str(message.get("text") or "").split(maxsplit=1)
    if len(parts) < 2:
        return ""
    raw = parts[1].strip()
    if used_target_in_arg:
        pieces = raw.split(maxsplit=1)
        return pieces[1].strip() if len(pieces) == 2 else ""
    return raw


async def _find_player(user_id: int, query: str):
    squad = await get_team_squad(int(user_id)) or []
    q = " ".join((query or "").strip().lower().split())
    if not q:
        return None, squad, "none"
    exact = [p for p in squad if " ".join(str(p.get("name") or "").lower().split()) == q]
    if len(exact) == 1:
        return dict(exact[0]), squad, "ok"
    fuzzy = [p for p in squad if q in " ".join(str(p.get("name") or "").lower().split())]
    if len(fuzzy) == 1:
        return dict(fuzzy[0]), squad, "ok"
    if len(fuzzy) > 1:
        return None, squad, "multiple"
    return None, squad, "missing"


async def _create_trade(sender_id: int, recipient_id: int, chat_id: int, player: dict) -> int:
    async def _tx(conn):
        row = await conn.fetchrow(
            """INSERT INTO trade_requests(chat_id,sender_id,recipient_id,sender_player_id,sender_player_kind,sender_player_json,status,expires_at)\n               VALUES($1,$2,$3,$4,$5,$6::jsonb,'awaiting_sender',NOW()+INTERVAL '5 minutes') RETURNING trade_id;""",
            int(chat_id), int(sender_id), int(recipient_id), int(player["player_id"]), _kind(player), json.dumps(player, default=str),
        )
        return int(row["trade_id"])
    return await transaction(_tx)

async def _get_trade(trade_id: int) -> dict | None:
    row = await fetchrow("SELECT * FROM trade_requests WHERE trade_id=$1;", int(trade_id))
    return dict(row) if row else None

async def _actor_allowed(trade: dict, uid: int, actor: str) -> bool:
    return int(trade["sender_id"] if actor == "sender" else trade["recipient_id"]) == int(uid)

async def _load_users(trade: dict) -> tuple[dict, dict]:
    s = await fetchrow("SELECT user_id,username,first_name FROM users WHERE user_id=$1;", int(trade["sender_id"]))
    r = await fetchrow("SELECT user_id,username,first_name FROM users WHERE user_id=$1;", int(trade["recipient_id"]))
    return dict(s), dict(r)

async def _ensure_trade_valid_for_creation(sender_id: int, recipient_id: int, chat_id: int) -> str | None:
    if sender_id == recipient_id:
        return "<b>⚠️ You cannot trade with yourself.</b>"
    if await _active_match(sender_id) or await _active_match(recipient_id):
        return "<b>⚠️ Trade is unavailable while either player is in an active match.</b>\n\n<b>Finish the match first, then try the trade again.</b>"
    if await _used_trade_today(sender_id) or await _used_trade_today(recipient_id):
        return "<b>⚠️ Daily trade limit reached.</b>\n\n<b>Each player can complete only one trade per day.</b>"
    if await _pending_for_user(sender_id) or await _pending_for_user(recipient_id):
        return "<b>⚠️ One of these players already has a pending trade.</b>\n\n<b>Complete, cancel, decline, or let the existing trade expire first.</b>"
    return None


@register("trade")
async def trade_command(message):
    sender_id = int((message.get("from") or {}).get("id") or 0)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    target = await _resolve_target(message)
    if not target:
        await app.send_message(chat_id, "<b>⚠️ Trade target not found.</b>\n\n<b>Reply to a user, use /trade @username, or use /trade userID.</b>", parse_mode="HTML")
        return
    recipient_id = int(target["user_id"])
    guard = await _ensure_trade_valid_for_creation(sender_id, recipient_id, chat_id)
    if guard:
        await app.send_message(chat_id, guard, parse_mode="HTML")
        return
    sender_row = await fetchrow("SELECT user_id,username,first_name FROM users WHERE user_id=$1;", sender_id)
    if not sender_row:
        await app.send_message(chat_id, "<b>⚠️ Your Crickium profile is not ready yet. Use /start first.</b>", parse_mode="HTML")
        return
    query = _player_arg(message, not bool(message.get("reply_to_message")))
    player, squad, status = await _find_player(sender_id, query)
    if not squad:
        await app.send_message(chat_id, "<b>⚠️ Your squad is empty.</b>", parse_mode="HTML")
        return
    recipient_squad = await get_team_squad(recipient_id) or []
    if not recipient_squad:
        await app.send_message(
            chat_id,
            f"<b>⚠️ {_mention(target)} does not have a tradable squad yet.</b>\n\n<b>They need at least one player in their squad before a trade can be started.</b>",
            parse_mode="HTML",
        )
        return
    if query and status == "missing":
        await app.send_message(chat_id, f"<b>⚠️ I couldn't find {html.escape(query)} in your squad.</b>", parse_mode="HTML")
        return
    if query and status == "multiple":
        lines = "\n".join(f"<b>• {html.escape(str(p.get('name') or 'Player'))} • OVR {_ovr(p)}</b>" for p in squad if query.lower() in str(p.get('name') or '').lower())
        await app.send_message(chat_id, f"<b>⚠️ Multiple players match.</b>\n\n{lines}\n\n<b>Use the full player name.</b>", parse_mode="HTML")
        return
    # Create a DB-backed trade before displaying buttons, so every callback has a durable ID.
    if player is None:
        # Placeholder sender player until a card is chosen. The DB row uses a harmless sentinel and is never accepted.
        player = None
        # We need a trade id for callbacks; create only after selection would lose persistence. Instead use a temporary trade row
        # with NULL-equivalent sentinel JSON, then fill it immediately on selection.
        async def _tx(conn):
            row = await conn.fetchrow(
                """INSERT INTO trade_requests(chat_id,sender_id,recipient_id,sender_player_id,sender_player_kind,sender_player_json,status,expires_at)\n                   VALUES($1,$2,$3,0,'global','{}'::jsonb,'awaiting_sender',NOW()+INTERVAL '5 minutes') RETURNING trade_id;""",
                chat_id, sender_id, recipient_id,
            )
            return int(row["trade_id"])
        trade_id = await transaction(_tx)
        text = _choose_sender_text(dict(sender_row), target)
        sent = await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=squad_player_keyboard(trade_id, squad, "sender"))
        await _set_message_id(trade_id, int(sent["message_id"]))
        return
    trade_id = await _create_trade(sender_id, recipient_id, chat_id, player)
    sent = await app.send_message(chat_id, _trade_offer_text({"trade_id": trade_id}, dict(sender_row), target, player), parse_mode="HTML", reply_markup=sender_confirm_keyboard(trade_id))
    await _set_message_id(trade_id, int(sent["message_id"]))


async def _set_message_id(trade_id: int, message_id: int) -> None:
    from database.query import execute
    await execute("UPDATE trade_requests SET message_id=$1 WHERE trade_id=$2;", int(message_id), int(trade_id))

async def _update_trade(trade_id: int, **fields):
    from database.query import execute
    allowed = {"sender_player_id","sender_player_kind","sender_player_json","recipient_player_id","recipient_player_kind","recipient_player_json","status","message_id"}
    parts=[]; args=[]
    for k,v in fields.items():
        if k not in allowed: continue
        cast = "::jsonb" if k.endswith("_json") else ""
        parts.append(f"{k}=${len(args)+1}{cast}")
        args.append(json.dumps(v, default=str) if k.endswith("_json") else v)
    if parts:
        args.append(int(trade_id)); await execute(f"UPDATE trade_requests SET {', '.join(parts)} WHERE trade_id=${len(args)};", *args)


@register_callback("trade_select")
async def trade_select(callback_query):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 4:
        await app.answer_callback_query(callback_query["id"], "Invalid trade selection.", show_alert=True); return
    trade_id, stage, pid = int(parts[1]), parts[2], int(parts[3])
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    trade = await _get_trade(trade_id)
    if not trade or trade["status"] not in ACTIVE_TRADE_STATUSES:
        await app.answer_callback_query(callback_query["id"], "This trade is no longer active.", show_alert=True); return
    expires_at = trade.get("expires_at")
    if expires_at is not None and expires_at <= datetime.now(expires_at.tzinfo or timezone.utc):
        await _update_trade(trade_id, status="expired")
        await app.answer_callback_query(callback_query["id"], "This trade has expired.", show_alert=True); return
    expected = int(trade["sender_id"] if stage == "sender" else trade["recipient_id"])
    if uid != expected:
        await app.answer_callback_query(callback_query["id"], "This trade selection belongs to another player.", show_alert=True); return
    squad = await get_team_squad(uid) or []
    player = next((dict(p) for p in squad if int(p.get("player_id") or 0) == pid), None)
    if not player:
        await app.answer_callback_query(callback_query["id"], "That player is no longer in the squad.", show_alert=True); return
    if stage == "sender":
        if trade["status"] != "awaiting_sender":
            await app.answer_callback_query(callback_query["id"], "The sender selection is no longer active.", show_alert=True); return
        recipient = (await _load_users(trade))[1]
        await _update_trade(trade_id, sender_player_id=pid, sender_player_kind=_kind(player), sender_player_json=player)
        await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], _sender_confirm_text(player, recipient), parse_mode="HTML", reply_markup=sender_confirm_keyboard(trade_id))
    else:
        if trade["status"] != "awaiting_recipient":
            await app.answer_callback_query(callback_query["id"], "The recipient selection is no longer active.", show_alert=True); return
        sender, recipient = await _load_users(trade)
        offered = json.loads(trade["sender_player_json"] or "{}")
        # Never accept a phantom or stale offered card.
        offered_now = next((dict(p) for p in (await get_team_squad(int(sender["user_id"])) or []) if int(p.get("player_id") or 0) == int(trade["sender_player_id"]) and _kind(p) == trade["sender_player_kind"]), None)
        if not offered_now:
            await _update_trade(trade_id, status="expired")
            await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], "<b>⚠️ TRADE NO LONGER VALID</b>\n\n<blockquote>The offered player is no longer available. No players were exchanged.</blockquote>", parse_mode="HTML", reply_markup=NO_KEYBOARD)
            await app.answer_callback_query(callback_query["id"], "Trade is no longer valid.", show_alert=True); return
        await _update_trade(trade_id, recipient_player_id=pid, recipient_player_kind=_kind(player), recipient_player_json=player, status="awaiting_recipient")
        await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], _recipient_confirm_text(sender, recipient, offered_now, player), parse_mode="HTML", reply_markup=recipient_confirm_keyboard(trade_id))
    await app.answer_callback_query(callback_query["id"])


@register_callback("trade_sender_yes")
async def trade_sender_yes(callback_query):
    await _sender_yes_or_cancel(callback_query, True)

@register_callback("trade_sender_cancel")
async def trade_sender_cancel(callback_query):
    await _sender_yes_or_cancel(callback_query, False)

async def _sender_yes_or_cancel(callback_query, yes: bool):
    trade_id=int(str(callback_query["data"]).split(":")[1]); uid=int(callback_query["from"]["id"]); trade=await _get_trade(trade_id)
    if not trade or trade["status"] != "awaiting_sender":
        await app.answer_callback_query(callback_query["id"], "This trade is no longer active.", show_alert=True); return
    if int(trade["sender_id"]) != uid:
        await app.answer_callback_query(callback_query["id"], "Only the trade sender can do this.", show_alert=True); return
    if not yes:
        await _update_trade(trade_id,status="cancelled")
        actor=(await _load_users(trade))[0]
        await app.edit_message_text(callback_query["message"]["chat"]["id"],callback_query["message"]["message_id"],_cancelled_text(actor),parse_mode="HTML",reply_markup=NO_KEYBOARD)
        await app.answer_callback_query(callback_query["id"],"Trade cancelled."); return
    # Validate current ownership before opening the recipient thread.
    squad=await get_team_squad(uid) or []
    player=next((dict(p) for p in squad if int(p.get("player_id") or 0)==int(trade["sender_player_id"]) and _kind(p)==trade["sender_player_kind"]),None)
    if not player:
        await _update_trade(trade_id,status="expired")
        await app.edit_message_text(callback_query["message"]["chat"]["id"],callback_query["message"]["message_id"],"<b>⚠️ TRADE NO LONGER VALID</b>\n\n<blockquote>The selected player is no longer in your squad.</blockquote>",parse_mode="HTML",reply_markup=NO_KEYBOARD)
        await app.answer_callback_query(callback_query["id"],"Selected player is no longer available.",show_alert=True); return
    recipient=(await _load_users(trade))[1]
    await _update_trade(trade_id,sender_player_json=player,status="awaiting_recipient")
    await app.delete_message(callback_query["message"]["chat"]["id"],callback_query["message"]["message_id"])
    recipient_squad=await get_team_squad(int(recipient["user_id"])) or []
    text=_recipient_choose_text((await _load_users(trade))[0],recipient,player)
    sent=await app.send_message(callback_query["message"]["chat"]["id"],text,parse_mode="HTML",reply_markup=squad_player_keyboard(trade_id,recipient_squad,"recipient"))
    await _set_message_id(trade_id,int(sent["message_id"]))
    await app.answer_callback_query(callback_query["id"],"Trade request sent.")

@register_callback("trade_sender_back")
async def trade_sender_back(callback_query):
    trade_id=int(str(callback_query["data"]).split(":")[1]); uid=int(callback_query["from"]["id"]); trade=await _get_trade(trade_id)
    if not trade or int(trade["sender_id"])!=uid or trade["status"] not in {"awaiting_sender"}:
        await app.answer_callback_query(callback_query["id"],"This trade step is no longer active.",show_alert=True); return
    sender,recipient=await _load_users(trade); squad=await get_team_squad(uid) or []
    await app.edit_message_text(callback_query["message"]["chat"]["id"],callback_query["message"]["message_id"],_choose_sender_text(sender,recipient),parse_mode="HTML",reply_markup=squad_player_keyboard(trade_id,squad,"sender"))
    await app.answer_callback_query(callback_query["id"])

@register_callback("trade_recipient_yes")
async def trade_recipient_yes(callback_query):
    trade_id=int(str(callback_query["data"]).split(":")[1]); uid=int(callback_query["from"]["id"]); trade=await _get_trade(trade_id)
    if not trade or trade["status"]!="awaiting_recipient" or int(trade["recipient_id"])!=uid:
        await app.answer_callback_query(callback_query["id"],"This trade is no longer active.",show_alert=True); return
    if await _active_match(int(trade["sender_id"])) or await _active_match(int(trade["recipient_id"])):
        await app.answer_callback_query(callback_query["id"],"Trade blocked because a player is in an active match.",show_alert=True); return
    if await _used_trade_today(int(trade["sender_id"])) or await _used_trade_today(int(trade["recipient_id"])):
        await app.answer_callback_query(callback_query["id"],"Daily trade limit has been reached.",show_alert=True); return
    sender,recipient=await _load_users(trade)
    async def _tx(conn):
        row=await conn.fetchrow("SELECT * FROM trade_requests WHERE trade_id=$1 FOR UPDATE;",trade_id)
        expires_at = row["expires_at"] if row else None
        expired = bool(expires_at is not None and expires_at <= datetime.now(expires_at.tzinfo or timezone.utc))
        if not row or row["status"] != "awaiting_recipient" or expired:
            return "stale"
        # Lock squads in a stable user-id order to prevent cross-trade deadlocks.
        first,second=sorted((int(row["sender_id"]),int(row["recipient_id"])))
        sr=await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;",first)
        rr=await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;",second)
        if not sr or not rr: return "squad_missing"
        ss=sr["squad"]; rs=rr["squad"]
        if isinstance(ss,str): ss=json.loads(ss)
        if isinstance(rs,str): rs=json.loads(rs)
        sk=row["sender_player_kind"]; rk=row["recipient_player_kind"]
        sp=next((dict(p) for p in ss if int(p.get("player_id") or 0)==int(row["sender_player_id"]) and _kind(p)==sk),None) if int(row["sender_id"])==first else next((dict(p) for p in rs if int(p.get("player_id") or 0)==int(row["sender_player_id"]) and _kind(p)==sk),None)
        rp=next((dict(p) for p in ss if int(p.get("player_id") or 0)==int(row["recipient_player_id"]) and _kind(p)==rk),None) if int(row["recipient_id"])==first else next((dict(p) for p in rs if int(p.get("player_id") or 0)==int(row["recipient_player_id"]) and _kind(p)==rk),None)
        if not sp or not rp: return "card_missing"
        sender_squad=ss if int(row["sender_id"])==first else rs
        recipient_squad=ss if int(row["recipient_id"])==first else rs
        sender_squad=[p for p in sender_squad if not (int(p.get("player_id") or 0)==int(row["sender_player_id"]) and _kind(p)==sk)]
        recipient_squad=[p for p in recipient_squad if not (int(p.get("player_id") or 0)==int(row["recipient_player_id"]) and _kind(p)==rk)]
        sender_squad.append(rp); recipient_squad.append(sp)
        if int(row["sender_id"])==first: ss,rs=sender_squad,recipient_squad
        else: rs,ss=sender_squad,recipient_squad
        await conn.execute("UPDATE team_squads SET squad=$1::jsonb, updated_at=NOW() WHERE user_id=$2;",json.dumps(ss,default=str),first)
        await conn.execute("UPDATE team_squads SET squad=$1::jsonb, updated_at=NOW() WHERE user_id=$2;",json.dumps(rs,default=str),second)
        await conn.execute("DELETE FROM user_player_loadouts WHERE user_id=$1 AND player_id=$2 AND player_kind=$3;",int(row["sender_id"]),int(row["sender_player_id"]),sk)
        await conn.execute("DELETE FROM user_player_loadouts WHERE user_id=$1 AND player_id=$2 AND player_kind=$3;",int(row["recipient_id"]),int(row["recipient_player_id"]),rk)
        await conn.execute("UPDATE trade_requests SET status='completed',completed_at=NOW(),sender_player_json=$1::jsonb,recipient_player_json=$2::jsonb WHERE trade_id=$3;",json.dumps(sp,default=str),json.dumps(rp,default=str),trade_id)
        return {"sent":sp,"received":rp}
    result=await transaction(_tx)
    if result=="stale":
        await app.answer_callback_query(callback_query["id"],"This trade is no longer active.",show_alert=True); return
    if result=="squad_missing":
        await app.answer_callback_query(callback_query["id"],"Trade failed safely because a squad is unavailable.",show_alert=True); return
    if result=="card_missing":
        await _update_trade(trade_id,status="expired")
        await app.edit_message_text(callback_query["message"]["chat"]["id"],callback_query["message"]["message_id"],"<b>⚠️ TRADE FAILED SAFELY</b>\n\n<blockquote>One of the selected players is no longer available. No cards were exchanged.</blockquote>",parse_mode="HTML",reply_markup=NO_KEYBOARD)
        await app.answer_callback_query(callback_query["id"],"One player is no longer available.",show_alert=True); return
    await app.delete_message(callback_query["message"]["chat"]["id"],callback_query["message"]["message_id"])
    await app.send_message(callback_query["message"]["chat"]["id"],_complete_text(sender,recipient,result["sent"],result["received"]),parse_mode="HTML",reply_markup=NO_KEYBOARD)
    await app.answer_callback_query(callback_query["id"],"Trade completed successfully.")

@register_callback("trade_recipient_cancel")
async def trade_recipient_cancel(callback_query):
    trade_id=int(str(callback_query["data"]).split(":")[1]); uid=int(callback_query["from"]["id"]); trade=await _get_trade(trade_id)
    if not trade or int(trade["recipient_id"])!=uid or trade["status"]!="awaiting_recipient":
        await app.answer_callback_query(callback_query["id"],"This trade is no longer active.",show_alert=True); return
    sender,recipient=await _load_users(trade); offered=json.loads(trade["sender_player_json"] or "{}")
    await _update_trade(trade_id,status="declined")
    await app.edit_message_text(callback_query["message"]["chat"]["id"],callback_query["message"]["message_id"],_declined_text(sender,recipient,offered),parse_mode="HTML",reply_markup=NO_KEYBOARD)
    await app.answer_callback_query(callback_query["id"],"Trade declined.")

@register_callback("trade_recipient_back")
async def trade_recipient_back(callback_query):
    trade_id=int(str(callback_query["data"]).split(":")[1]); uid=int(callback_query["from"]["id"]); trade=await _get_trade(trade_id)
    if not trade or int(trade["recipient_id"])!=uid or trade["status"]!="awaiting_recipient":
        await app.answer_callback_query(callback_query["id"],"This trade step is no longer active.",show_alert=True); return
    sender,recipient=await _load_users(trade); offered=json.loads(trade["sender_player_json"] or "{}")
    await app.edit_message_text(callback_query["message"]["chat"]["id"],callback_query["message"]["message_id"],_recipient_choose_text(sender,recipient,offered),parse_mode="HTML",reply_markup=squad_player_keyboard(trade_id,await get_team_squad(uid) or [],"recipient"))
    await app.answer_callback_query(callback_query["id"])
