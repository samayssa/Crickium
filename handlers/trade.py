from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

from app import app
from handlers.registry import register, register_callback
from database.query import fetchrow, transaction
from database.squads_repo import get_team_squad
from database.play_repo import get_active_match_for_user as get_play_active
from database.playint_repo import get_active_match_for_user as get_playint_active
from database.playipl_repo import get_active_match_for_user as get_playipl_active
from utils.mentions import mention_html
from buttons.social_trade_buttons import (
    squad_player_keyboard,
    confirm_selected_player_keyboard,
    direct_offer_keyboard,
    selected_player_keyboard,
    recipient_request_keyboard,
    recipient_confirm_keyboard,
)

NO_KEYBOARD = {"inline_keyboard": []}
ACTIVE_TRADE_STATUSES = {"awaiting_sender", "awaiting_recipient"}
# TEMPORARY TEST SWITCH: change False to True to restore the one-trade-per-day limit.
DAILY_TRADE_LIMIT_ENABLED = False


def _kind(player: dict[str, Any]) -> str:
    special = player.get("is_special") is True or str(player.get("is_special") or "").lower() in {"1", "true", "yes"}
    return "special" if special or int(player.get("player_id") or 0) < 0 else "global"


def _ovr(player: dict[str, Any]) -> int:
    return max(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))


def _mention(user: dict) -> str:
    return mention_html(int(user["user_id"]), user.get("username"), user.get("first_name"))


def _pname(player: dict) -> str:
    return html.escape(str(player.get("name") or "Player"))


def _decode_callback_data(callback_query: dict) -> str:
    value = callback_query.get("data") or ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _callback_parts(callback_query: dict) -> list[str]:
    return _decode_callback_data(callback_query).split(":")


def _chat_message(callback_query: dict) -> tuple[int, int]:
    msg = callback_query.get("message") or {}
    chat = msg.get("chat") or {}
    return int(chat.get("id") or 0), int(msg.get("message_id") or 0)


def _choose_sender_text(sender: dict, recipient: dict, *, desired: str | None = None) -> str:
    suffix = f"\n\n<b>Requested in return : {html.escape(desired)}</b>" if desired else ""
    return (
        "<b>╭━━━〔 🔄 TRADE SETUP 〕━━━╮</b>\n\n"
        f"<b>👤 {_mention(sender)}</b> wants to trade with\n"
        f"<b>🤝 {_mention(recipient)}</b>.\n\n"
        "<b>Which player do you want to trade?</b>\n\n"
        "<b>Select one player from your squad below.</b>"
        f"{suffix}\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _multiple_sender_text(sender: dict, recipient: dict, query: str) -> str:
    return (
        "<b>╭━━━〔 🔎 SELECT PLAYER 〕━━━╮</b>\n\n"
        f"<b>👤 {_mention(sender)}</b> has multiple players matching <b>{html.escape(query)}</b>.\n\n"
        "<b>Which one would you like to trade with "
        f"{_mention(recipient)}?</b>\n\n"
        "<b>Select a player below, then press Confirm Player.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _sender_confirm_text(player: dict, recipient: dict, desired: str | None = None) -> str:
    requested = f"\n📥 Requested in return : <b>{html.escape(desired)}</b>" if desired else ""
    question = (
        f"Are you sure you want to trade {_pname(player)} with {_mention(recipient)}?"
        if desired else
        f"Are you sure you want to trade {_pname(player)}?"
    )
    return (
        "<b>╭━━━〔 ⚠️ CONFIRM TRADE 〕━━━╮</b>\n\n"
        f"<blockquote>📤 You are offering : <b>{_pname(player)}</b>\n"
        f"⭐ OVR : <b>{_ovr(player)}</b>"
        f"{requested}\n"
        f"🤝 Trading with : <b>{_mention(recipient)}</b></blockquote>\n\n"
        f"<b>{question}</b>\n\n"
        "<b>Your player will only be transferred after both sides confirm the final trade.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _recipient_choose_text(sender: dict, recipient: dict, offered: dict, desired: str | None = None) -> str:
    requested_note = f"\n\n<b>📥 Requested player : {html.escape(desired)}</b>" if desired else ""
    return (
        "<b>╭━━━〔 🤝 TRADE REQUEST 〕━━━╮</b>\n\n"
        f"<blockquote><b>👤 {_mention(sender)}</b> wants to trade with you.\n\n"
        f"📤 Offering : <b>{_pname(offered)}</b> • OVR <b>{_ovr(offered)}</b></blockquote>\n\n"
        f"<b>{_mention(recipient)}, which player do you want to give in return?</b>"
        f"{requested_note}\n\n"
        "<b>Select one player from your squad below.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _multiple_recipient_text(sender: dict, recipient: dict, offered: dict, query: str) -> str:
    return (
        "<b>╭━━━〔 🔎 SELECT RETURN PLAYER 〕━━━╮</b>\n\n"
        f"<blockquote>📤 {_mention(sender)} is offering : <b>{_pname(offered)}</b> • OVR <b>{_ovr(offered)}</b></blockquote>\n\n"
        f"<b>{_mention(recipient)}, you have multiple players matching {html.escape(query)}.</b>\n\n"
        "<b>Which one do you want in exchange?</b>\n\n"
        "<b>Select a player below, then press Confirm Player.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _recipient_confirm_text(sender: dict, recipient: dict, offered: dict, requested: dict) -> str:
    return (
        "<b>╭━━━〔 ⚠️ CONFIRM TRADE 〕━━━╮</b>\n\n"
        f"<blockquote>📤 {_mention(sender)} gives\n🏏 <b>{_pname(offered)}</b> • OVR <b>{_ovr(offered)}</b>\n\n"
        f"📥 {_mention(recipient)} gives\n🏏 <b>{_pname(requested)}</b> • OVR <b>{_ovr(requested)}</b></blockquote>\n\n"
        f"<b>Are you sure you want to give {_pname(requested)} to {_mention(sender)}?</b>\n\n"
        "<b>Press Yes, Confirm to send the final trade request.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _request_text(sender: dict, recipient: dict, offered: dict, requested: dict) -> str:
    return (
        "<b>╭━━━〔 🔄 TRADE OFFER 〕━━━╮</b>\n\n"
        f"<blockquote><b>{_mention(sender)}</b> is offering\n"
        f"🏏 <b>{_pname(offered)}</b> • OVR <b>{_ovr(offered)}</b>\n\n"
        f"<b>in exchange for</b>\n🏏 <b>{_pname(requested)}</b> • OVR <b>{_ovr(requested)}</b>\n\n"
        f"to <b>{_mention(recipient)}</b>.</blockquote>\n\n"
        "<b>⚖️ 1 Player ↔ 1 Player</b>\n"
        "<b>⏳ This offer expires in 5 minutes.</b>\n\n"
        f"<b>{_mention(recipient)}, would you like to accept this trade?</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _complete_text(sender: dict, recipient: dict, sent: dict, received: dict) -> str:
    return (
        "<b>╭━━━〔 ✅ TRADE COMPLETED 〕━━━╮</b>\n\n"
        f"<blockquote><b>🤝 Trade successfully completed!</b>\n\n"
        f"👤 <b>{_mention(sender)}</b>\n        ⇄\n👤 <b>{_mention(recipient)}</b></blockquote>\n\n"
        f"<b>📤 {_mention(sender)} GAVE</b>\n\n"
        f"<blockquote>🏏 <b>{_pname(sent)}</b>\n⭐ OVR : <b>{_ovr(sent)}</b>\n"
        f"{'✨ Special Edition' if _kind(sent) == 'special' else '🏏 Standard Card'}</blockquote>\n\n"
        f"<b>📥 {_mention(sender)} RECEIVED</b>\n\n"
        f"<blockquote>🏏 <b>{_pname(received)}</b>\n⭐ OVR : <b>{_ovr(received)}</b>\n"
        f"{'✨ Special Edition' if _kind(received) == 'special' else '🏏 Standard Card'}</blockquote>\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<blockquote>✅ <b>Status</b> ➤ Completed\n"
        "🔄 <b>Exchange</b> ➤ 1 Player ↔ 1 Player\n"
        "🔐 <b>Ownership</b> ➤ Successfully transferred</blockquote>\n\n"
        "<b>🎉 Both players have changed hands!</b>\n\n"
        "<b>Another deal closed in the Crickium Market. 🔄</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _declined_text(sender: dict, recipient: dict, offered: dict, requested: dict | None = None) -> str:
    requested_block = f"\n\n<b>📥 REQUESTED</b>\n\n<blockquote>🏏 <b>{_pname(requested)}</b>\n⭐ OVR : <b>{_ovr(requested)}</b></blockquote>" if requested else ""
    return (
        "<b>╭━━━〔 ❌ TRADE DECLINED 〕━━━╮</b>\n\n"
        f"<blockquote><b>👤 {_mention(recipient)}</b> declined the trade offer from\n<b>👤 {_mention(sender)}</b>.</blockquote>\n\n"
        "<b>📤 OFFERED</b>\n\n"
        f"<blockquote>🏏 <b>{_pname(offered)}</b>\n⭐ OVR : <b>{_ovr(offered)}</b></blockquote>"
        f"{requested_block}\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<b>🔓 No players were exchanged.</b>\n\n"
        "<b>Both players remain with their original owners.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _cancelled_text(actor: dict) -> str:
    return (
        "<b>╭━━━〔 🚫 TRADE CANCELLED 〕━━━╮</b>\n\n"
        f"<blockquote><b>{_mention(actor)}</b> cancelled the pending trade.</blockquote>\n\n"
        "<b>🔓 No players were exchanged.</b>\n\n"
        "<b>The cards remain with their original owners.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _expired_text(reason: str = "The trade offer has expired or is no longer valid.") -> str:
    return (
        "<b>╭━━━〔 ⏳ TRADE EXPIRED 〕━━━╮</b>\n\n"
        f"<blockquote>⚠️ {html.escape(reason)}</blockquote>\n\n"
        "<b>🔓 No players were exchanged.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _failure_text(reason: str) -> str:
    return (
        "<b>╭━━━〔 ⚠️ TRADE FAILED SAFELY 〕━━━╮</b>\n\n"
        f"<blockquote>{html.escape(reason)}</blockquote>\n\n"
        "<b>🔓 No players were exchanged.</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


async def _active_match(user_id: int) -> bool:
    return bool(
        await get_play_active(int(user_id))
        or await get_playint_active(int(user_id))
        or await get_playipl_active(int(user_id))
    )


async def _used_trade_today(user_id: int) -> bool:
    row = await fetchrow(
        """SELECT 1 FROM trade_requests
           WHERE status='completed' AND completed_at::date=CURRENT_DATE
             AND (sender_id=$1 OR recipient_id=$1) LIMIT 1;""",
        int(user_id),
    )
    return bool(row)


async def _pending_for_user(user_id: int) -> bool:
    row = await fetchrow(
        """SELECT 1 FROM trade_requests
           WHERE status = ANY($2::text[]) AND (sender_id=$1 OR recipient_id=$1)
           LIMIT 1;""",
        int(user_id), list(ACTIVE_TRADE_STATUSES),
    )
    return bool(row)


async def expire_all_active_trades_for_refresh() -> int:
    """Expire every live trade session.

    /refresh is the owner-only operational reset. The trade table is the
    source of truth for all pending UI sessions, so one DB operation clears
    the entire trade state without touching squads, matches, or other
    commands.
    """
    from database.query import execute
    result = await execute(
        """UPDATE trade_requests
           SET status='expired'
           WHERE status = ANY($1::text[]);""",
        list(ACTIVE_TRADE_STATUSES),
    )
    try:
        return int(str(result).split()[-1])
    except Exception:
        return 0


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


def _command_argument_text(message: dict) -> str:
    text = str(message.get("text") or "")
    parts = text.split()
    if len(parts) <= 1:
        return ""
    reply = bool(message.get("reply_to_message"))
    first = parts[1]
    if not reply and (first.startswith("@") or first.isdigit()):
        return " ".join(parts[2:]).strip()
    return text.split(None, 1)[1].strip()


def _split_for(argument: str) -> tuple[str, str | None]:
    normalized = " ".join((argument or "").split())
    tokens = normalized.lower().split()
    for i, token in enumerate(tokens):
        if token == "for":
            left_words = normalized.split()[:i]
            right_words = normalized.split()[i + 1 :]
            if left_words and right_words:
                return " ".join(left_words), " ".join(right_words)
    return normalized, None


async def _find_player_matches(user_id: int, query: str) -> tuple[list[dict], list[dict]]:
    squad = [dict(p) for p in (await get_team_squad(int(user_id)) or [])]
    q = " ".join((query or "").strip().lower().split())
    if not q:
        return [], squad
    exact = [p for p in squad if " ".join(str(p.get("name") or "").lower().split()) == q]
    if exact:
        return exact, squad
    return [p for p in squad if q in " ".join(str(p.get("name") or "").lower().split())], squad


def _find_exact_identity(squad: list[dict], player_id: int, kind: str) -> dict | None:
    return next(
        (dict(p) for p in squad if int(p.get("player_id") or 0) == int(player_id) and _kind(p) == kind),
        None,
    )


def _callback_actor_status(trade: dict | None, uid: int) -> str:
    """Return an actor-first callback decision.

    The actor check intentionally happens before the trade-status check so a
    third party never gets a misleading "trade is no longer active" alert.
    """
    if not trade:
        return "missing"
    if uid not in {int(trade["sender_id"]), int(trade["recipient_id"])}:
        return "not_for_user"
    return "ok"


async def _get_trade_for_callback(trade_id: int, uid: int, allowed_statuses: set[str] | None = None):
    """Fetch the latest persisted trade and classify callback ownership/state."""
    trade = await _get_trade(trade_id)
    actor_status = _callback_actor_status(trade, uid)
    if actor_status != "ok":
        return trade, actor_status
    if allowed_statuses is not None and trade.get("status") not in allowed_statuses:
        return trade, "inactive"
    expires_at = trade.get("expires_at")
    if expires_at is not None:
        expiry = expires_at if getattr(expires_at, "tzinfo", None) else expires_at.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            await _update_trade(trade_id, status="expired")
            return trade, "expired"
    return trade, "ok"


async def _get_trade(trade_id: int):
    row = await fetchrow("SELECT * FROM trade_requests WHERE trade_id=$1;", int(trade_id))
    return dict(row) if row else None


async def _load_users(trade: dict) -> tuple[dict, dict]:
    sender = await fetchrow("SELECT user_id,username,first_name FROM users WHERE user_id=$1;", int(trade["sender_id"]))
    recipient = await fetchrow("SELECT user_id,username,first_name FROM users WHERE user_id=$1;", int(trade["recipient_id"]))
    return dict(sender), dict(recipient)


async def _update_trade(trade_id: int, **fields):
    from database.query import execute

    allowed = {
        "sender_player_id", "sender_player_kind", "sender_player_json",
        "recipient_player_id", "recipient_player_kind", "recipient_player_json",
        "status", "message_id",
    }
    parts: list[str] = []
    args: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        cast = "::jsonb" if key.endswith("_json") else ""
        parts.append(f"{key}=${len(args)+1}{cast}")
        args.append(json.dumps(value, default=str) if key.endswith("_json") else value)
    if parts:
        args.append(int(trade_id))
        await execute(
            f"UPDATE trade_requests SET {', '.join(parts)} WHERE trade_id=${len(args)};",
            *args,
        )


async def _set_message_id(trade_id: int, message_id: int) -> None:
    await _update_trade(trade_id, message_id=int(message_id))


async def _create_trade(sender_id: int, recipient_id: int, chat_id: int, player: dict, requested: dict | None = None) -> int:
    async def _tx(conn):
        row = await conn.fetchrow(
            """INSERT INTO trade_requests(
                 chat_id,sender_id,recipient_id,sender_player_id,sender_player_kind,
                 recipient_player_id,recipient_player_kind,sender_player_json,recipient_player_json,
                 status,expires_at
               )
               VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,'awaiting_sender',NOW()+INTERVAL '5 minutes')
               RETURNING trade_id;""",
            int(chat_id), int(sender_id), int(recipient_id), int(player["player_id"]), _kind(player),
            int(requested["player_id"]) if requested else None,
            _kind(requested) if requested else None,
            json.dumps(player, default=str),
            json.dumps(requested, default=str) if requested else None,
        )
        return int(row["trade_id"])
    return await transaction(_tx)


async def _ensure_creation_guards(sender_id: int, recipient_id: int) -> str | None:
    if sender_id == recipient_id:
        return "You cannot trade with yourself."
    if await _active_match(sender_id) or await _active_match(recipient_id):
        return "Trade is unavailable while either user is in an active match. Finish the match first."
    if DAILY_TRADE_LIMIT_ENABLED and (await _used_trade_today(sender_id) or await _used_trade_today(recipient_id)):
        return "Daily trade limit reached. Each user can complete only one trade per day."
    if await _pending_for_user(sender_id) or await _pending_for_user(recipient_id):
        return "One of these users already has a pending trade. Finish it before starting another."
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
    guard = await _ensure_creation_guards(sender_id, recipient_id)
    if guard:
        await app.send_message(chat_id, f"<b>⚠️ {html.escape(guard)}</b>", parse_mode="HTML")
        return

    sender_row = await fetchrow("SELECT user_id,username,first_name FROM users WHERE user_id=$1;", sender_id)
    if not sender_row:
        await app.send_message(chat_id, "<b>⚠️ Your Crickium profile is not ready yet. Use /start first.</b>", parse_mode="HTML")
        return

    recipient_squad = await get_team_squad(recipient_id) or []
    sender_squad = await get_team_squad(sender_id) or []
    if not sender_squad:
        await app.send_message(chat_id, "<b>⚠️ Your squad is empty. Add a player before trading.</b>", parse_mode="HTML")
        return
    if not recipient_squad:
        await app.send_message(chat_id, f"<b>⚠️ {_mention(target)} does not have a tradable squad yet.</b>", parse_mode="HTML")
        return

    argument = _command_argument_text(message)
    offered_query, requested_query = _split_for(argument)

    # No player specified: open the full sender squad picker.
    if not offered_query:
        trade_id = await _create_trade(sender_id, recipient_id, chat_id, {"player_id": 0, "name": "Pending Selection", "bat_level": 0, "bowl_level": 0})
        sent = await app.send_message(
            chat_id,
            _choose_sender_text(dict(sender_row), target),
            parse_mode="HTML",
            reply_markup=squad_player_keyboard(trade_id, sender_squad, "s"),
        )
        await _set_message_id(trade_id, int(sent["message_id"]))
        return

    offered_matches, _ = await _find_player_matches(sender_id, offered_query)
    if not offered_matches:
        await app.send_message(chat_id, f"<b>⚠️ I couldn't find {html.escape(offered_query)} in your squad.</b>", parse_mode="HTML")
        return

    # Direct "Player A for Player B" path. Validate BOTH owners before showing
    # the sender's final confirmation.
    if requested_query:
        requested_matches, _ = await _find_player_matches(recipient_id, requested_query)
        if not requested_matches:
            await app.send_message(chat_id, f"<b>⚠️ {_mention(target)} does not have {html.escape(requested_query)} in their squad.</b>", parse_mode="HTML")
            return
        if len(offered_matches) > 1:
            trade_id = await _create_trade(sender_id, recipient_id, chat_id, {"player_id": 0, "name": "Pending Selection", "bat_level": 0, "bowl_level": 0})
            await _update_trade(trade_id, sender_player_json={"pending_query": offered_query})
            sent = await app.send_message(
                chat_id,
                _multiple_sender_text(dict(sender_row), target, offered_query),
                parse_mode="HTML",
                reply_markup=squad_player_keyboard(trade_id, offered_matches, "s", confirm=True),
            )
            await _set_message_id(trade_id, int(sent["message_id"]))
            # Requested query is stored inside the placeholder JSON.
            await _update_trade(trade_id, sender_player_json={"pending_query": offered_query, "selection_pending_confirm": True}, recipient_player_json={"pending_query": requested_query})
            return
        offered = offered_matches[0]
        if len(requested_matches) > 1:
            # Let recipient choose the ambiguous return card after sender confirms.
            requested = None
        else:
            requested = requested_matches[0]
        trade_id = await _create_trade(sender_id, recipient_id, chat_id, offered, requested)
        sent = await app.send_message(
            chat_id,
            _sender_confirm_text(offered, target, requested_query if requested else requested_query),
            parse_mode="HTML",
            reply_markup=direct_offer_keyboard(trade_id),
        )
        await _set_message_id(trade_id, int(sent["message_id"]))
        # Preserve ambiguous requested query for recipient selection.
        if requested is None:
            await _update_trade(trade_id, recipient_player_json={"pending_query": requested_query})
        return

    # Player-only path: select sender card first.
    if len(offered_matches) > 1:
        trade_id = await _create_trade(sender_id, recipient_id, chat_id, {"player_id": 0, "name": "Pending Selection", "bat_level": 0, "bowl_level": 0})
        await _update_trade(trade_id, sender_player_json={"pending_query": offered_query, "selection_pending_confirm": True}, recipient_player_json=( {"pending_query": requested_query} if requested_query else None ))
        sent = await app.send_message(
            chat_id,
            _multiple_sender_text(dict(sender_row), target, offered_query),
            parse_mode="HTML",
            reply_markup=squad_player_keyboard(trade_id, offered_matches, "s", confirm=True),
        )
        await _set_message_id(trade_id, int(sent["message_id"]))
        return

    offered = offered_matches[0]
    trade_id = await _create_trade(sender_id, recipient_id, chat_id, offered)
    sent = await app.send_message(
        chat_id,
        _sender_confirm_text(offered, target),
        parse_mode="HTML",
        reply_markup=direct_offer_keyboard(trade_id),
    )
    await _set_message_id(trade_id, int(sent["message_id"]))


@register_callback("trade_pick")
async def trade_pick(callback_query):
    parts = _callback_parts(callback_query)
    if len(parts) != 4 or parts[0] != "trade_pick" or parts[2] not in {"s", "r"}:
        await app.answer_callback_query(callback_query["id"], "Invalid trade selection.", show_alert=True)
        return
    try:
        trade_id = int(parts[1]); pid = int(parts[3])
    except ValueError:
        await app.answer_callback_query(callback_query["id"], "Invalid player selection.", show_alert=True)
        return

    uid = int((callback_query.get("from") or {}).get("id") or 0)
    trade = await _get_trade(trade_id)
    actor_status = _callback_actor_status(trade, uid)
    if actor_status == "missing":
        await app.answer_callback_query(callback_query["id"], "This trade no longer exists.", show_alert=True)
        return
    if actor_status == "not_for_user":
        await app.answer_callback_query(callback_query["id"], "This trade is not for you.", show_alert=True)
        return

    stage = parts[2]
    expected = int(trade["sender_id"] if stage == "s" else trade["recipient_id"])
    if uid != expected:
        await app.answer_callback_query(callback_query["id"], "This trade step is not for you.", show_alert=True)
        return

    trade, state = await _get_trade_for_callback(trade_id, uid, ACTIVE_TRADE_STATUSES)
    if state == "expired":
        chat_id, message_id = _chat_message(callback_query)
        await app.edit_message_text(chat_id, message_id, _expired_text(), parse_mode="HTML", reply_markup=NO_KEYBOARD)
        await app.answer_callback_query(callback_query["id"], "This trade has expired.", show_alert=True)
        return
    if state != "ok":
        await app.answer_callback_query(callback_query["id"], "This trade is no longer active.", show_alert=True)
        return

    squad = await get_team_squad(uid) or []
    player = next((dict(p) for p in squad if int(p.get("player_id") or 0) == pid), None)
    if not player:
        await app.answer_callback_query(callback_query["id"], "That player is no longer in the squad.", show_alert=True)
        return

    if stage == "s":
        if trade["status"] != "awaiting_sender":
            await app.answer_callback_query(callback_query["id"], "Sender selection is no longer active.", show_alert=True)
            return
        recipient = (await _load_users(trade))[1]
        pending = {}
        try:
            pending = json.loads(trade.get("sender_player_json") or "{}") or {}
        except Exception:
            pending = {}
        selected_from_query = bool(pending.get("pending_query"))
        requested_query = None
        try:
            recipient_meta = json.loads(trade.get("recipient_player_json") or "{}") or {}
            requested_query = recipient_meta.get("pending_query")
        except Exception:
            requested_query = None
        await _update_trade(
            trade_id,
            sender_player_id=int(player["player_id"]),
            sender_player_kind=_kind(player),
            sender_player_json=player,
        )
        chat_id, message_id = _chat_message(callback_query)
        if selected_from_query:
            confirm_text = (
                "<b>╭━━━〔 ✅ PLAYER SELECTED 〕━━━╮</b>\n\n"
                f"<blockquote>🏏 <b>{_pname(player)}</b>\n⭐ OVR : <b>{_ovr(player)}</b>\n🤝 Trading with : <b>{_mention(recipient)}</b></blockquote>\n\n"
                f"<b>You selected {_pname(player)}. Press Confirm Player to continue.</b>\n\n"
                "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
            )
            await app.edit_message_text(chat_id, message_id, confirm_text, parse_mode="HTML", reply_markup=selected_player_keyboard(trade_id, "s"))
        else:
            text = _sender_confirm_text(player, recipient, requested_query)
            await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=confirm_selected_player_keyboard(trade_id))
        await app.answer_callback_query(callback_query["id"], f"Selected {player.get('name') or 'player'}.")
        return

    if trade["status"] != "awaiting_recipient":
        await app.answer_callback_query(callback_query["id"], "Recipient selection is no longer active.", show_alert=True)
        return
    sender, recipient = await _load_users(trade)
    try:
        offered = json.loads(trade.get("sender_player_json") or "{}")
    except Exception:
        offered = {}
    offered_now = _find_exact_identity(await get_team_squad(int(sender["user_id"])) or [], int(trade["sender_player_id"]), trade["sender_player_kind"])
    if not offered_now:
        await _update_trade(trade_id, status="expired")
        chat_id, message_id = _chat_message(callback_query)
        await app.edit_message_text(chat_id, message_id, _expired_text("The offered player is no longer available."), parse_mode="HTML", reply_markup=NO_KEYBOARD)
        await app.answer_callback_query(callback_query["id"], "The offered player is no longer available.", show_alert=True)
        return
    requested_meta = {}
    try:
        requested_meta = json.loads(trade.get("recipient_player_json") or "{}") or {}
    except Exception:
        requested_meta = {}
    pending_recipient_query = requested_meta.get("pending_query") if isinstance(requested_meta, dict) else None
    recipient_json = player
    if pending_recipient_query:
        recipient_json = {"selected_player": player, "pending_query": pending_recipient_query, "selection_pending_confirm": True}
    await _update_trade(
        trade_id,
        recipient_player_id=int(player["player_id"]),
        recipient_player_kind=_kind(player),
        recipient_player_json=recipient_json,
    )
    chat_id, message_id = _chat_message(callback_query)
    if pending_recipient_query:
        confirm_text = (
            "<b>╭━━━〔 ✅ PLAYER SELECTED 〕━━━╮</b>\n\n"
            f"<blockquote>📤 {_mention(sender)} offers : <b>{_pname(offered_now)}</b> • OVR <b>{_ovr(offered_now)}</b>\n\n"
            f"📥 You selected : <b>{_pname(player)}</b> • OVR <b>{_ovr(player)}</b></blockquote>\n\n"
            f"<b>You selected {_pname(player)}. Press Confirm Player to continue.</b>\n\n"
            "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
        )
        await app.edit_message_text(chat_id, message_id, confirm_text, parse_mode="HTML", reply_markup=selected_player_keyboard(trade_id, "r"))
    else:
        await app.edit_message_text(chat_id, message_id, _recipient_confirm_text(sender, recipient, offered_now, player), parse_mode="HTML", reply_markup=recipient_confirm_keyboard(trade_id))
    await app.answer_callback_query(callback_query["id"], f"Selected {player.get('name') or 'player'}.")


@register_callback("trade_confirm_pick")
async def trade_confirm_pick(callback_query):
    parts = _callback_parts(callback_query)
    if len(parts) != 3 or parts[0] != "trade_confirm_pick" or parts[2] not in {"s", "r"}:
        await app.answer_callback_query(callback_query["id"], "Invalid confirmation.", show_alert=True)
        return
    trade_id = int(parts[1])
    stage = parts[2]
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    trade = await _get_trade(trade_id)
    actor_status = _callback_actor_status(trade, uid)
    if actor_status == "missing":
        await app.answer_callback_query(callback_query["id"], "This trade no longer exists.", show_alert=True)
        return
    if actor_status == "not_for_user":
        await app.answer_callback_query(callback_query["id"], "This trade is not for you.", show_alert=True)
        return
    expected = int(trade["sender_id"] if stage == "s" else trade["recipient_id"])
    if uid != expected:
        await app.answer_callback_query(callback_query["id"], "This trade step is not for you.", show_alert=True)
        return
    trade, state = await _get_trade_for_callback(trade_id, uid, ACTIVE_TRADE_STATUSES)
    if state == "expired":
        chat_id, message_id = _chat_message(callback_query)
        await app.edit_message_text(chat_id, message_id, _expired_text(), parse_mode="HTML", reply_markup=NO_KEYBOARD)
        await app.answer_callback_query(callback_query["id"], "This trade has expired.", show_alert=True)
        return
    if state != "ok":
        await app.answer_callback_query(callback_query["id"], "This trade is no longer active.", show_alert=True)
        return
    if stage == "s":
        sender, recipient = await _load_users(trade)
        if int(trade["sender_player_id"] or 0) == 0:
            await app.answer_callback_query(callback_query["id"], "Please select a player first.", show_alert=True)
            return
        player = _find_exact_identity(await get_team_squad(uid) or [], int(trade["sender_player_id"]), trade["sender_player_kind"])
        if not player:
            await app.answer_callback_query(callback_query["id"], "That player is no longer in your squad.", show_alert=True)
            return
        requested_query = None
        try:
            payload = json.loads(trade.get("recipient_player_json") or "{}") or {}
            requested_query = payload.get("pending_query")
        except Exception:
            requested_query = None
        chat_id, message_id = _chat_message(callback_query)
        await app.edit_message_text(chat_id, message_id, _sender_confirm_text(player, recipient, requested_query), parse_mode="HTML", reply_markup=confirm_selected_player_keyboard(trade_id))
        await app.answer_callback_query(callback_query["id"], "Player confirmed.")
        return

    # Recipient's Confirm Player is only reached when recipient selected a card.
    if int(trade["recipient_player_id"] or 0) == 0:
        await app.answer_callback_query(callback_query["id"], "Please select a return player first.", show_alert=True)
        return
    sender, recipient = await _load_users(trade)
    offered = _find_exact_identity(await get_team_squad(int(sender["user_id"])) or [], int(trade["sender_player_id"]), trade["sender_player_kind"])
    requested = _find_exact_identity(await get_team_squad(uid) or [], int(trade["recipient_player_id"]), trade["recipient_player_kind"])
    if not offered or not requested:
        await app.answer_callback_query(callback_query["id"], "One of the selected players is no longer available.", show_alert=True)
        return
    await _update_trade(trade_id, recipient_player_json=requested)
    chat_id, message_id = _chat_message(callback_query)
    await app.edit_message_text(chat_id, message_id, _recipient_confirm_text(sender, recipient, offered, requested), parse_mode="HTML", reply_markup=recipient_confirm_keyboard(trade_id))
    await app.answer_callback_query(callback_query["id"], "Player confirmed.")


@register_callback("trade_sender_yes")
async def trade_sender_yes(callback_query):
    parts = _callback_parts(callback_query)
    if len(parts) != 2:
        await app.answer_callback_query(callback_query["id"], "Invalid trade action.", show_alert=True)
        return
    trade_id = int(parts[1]); uid = int((callback_query.get("from") or {}).get("id") or 0)
    trade = await _get_trade(trade_id)
    actor_status = _callback_actor_status(trade, uid)
    if actor_status == "missing":
        await app.answer_callback_query(callback_query["id"], "This trade no longer exists.", show_alert=True)
        return
    if actor_status == "not_for_user":
        await app.answer_callback_query(callback_query["id"], "This trade is not for you.", show_alert=True)
        return
    if trade["status"] != "awaiting_sender" or int(trade["sender_id"]) != uid:
        await app.answer_callback_query(callback_query["id"], "This trade is no longer active.", show_alert=True)
        return

    sender, recipient = await _load_users(trade)
    offered = _find_exact_identity(await get_team_squad(uid) or [], int(trade["sender_player_id"]), trade["sender_player_kind"])
    if not offered:
        await _update_trade(trade_id, status="expired")
        chat_id, message_id = _chat_message(callback_query)
        await app.edit_message_text(chat_id, message_id, _expired_text("The selected player is no longer in your squad."), parse_mode="HTML", reply_markup=NO_KEYBOARD)
        await app.answer_callback_query(callback_query["id"], "Selected player is no longer available.", show_alert=True)
        return

    # Direct requested player, if supplied, has already been validated at command time.
    requested = None
    if trade.get("recipient_player_id"):
        requested = _find_exact_identity(await get_team_squad(int(recipient["user_id"])) or [], int(trade["recipient_player_id"]), trade["recipient_player_kind"])
        if not requested:
            await _update_trade(trade_id, status="expired")
            chat_id, message_id = _chat_message(callback_query)
            await app.edit_message_text(chat_id, message_id, _expired_text("The requested player is no longer in the recipient's squad."), parse_mode="HTML", reply_markup=NO_KEYBOARD)
            await app.answer_callback_query(callback_query["id"], "Requested player is no longer available.", show_alert=True)
            return

    requested_query = None
    try:
        recipient_meta = json.loads(trade.get("recipient_player_json") or "{}") or {}
        requested_query = recipient_meta.get("pending_query")
    except Exception:
        requested_query = None

    if requested is None and requested_query:
        matches, _ = await _find_player_matches(int(recipient["user_id"]), requested_query)
        if not matches:
            await _update_trade(trade_id, status="expired")
            chat_id, message_id = _chat_message(callback_query)
            await app.edit_message_text(chat_id, message_id, _expired_text(f"{_mention(recipient)} no longer has {requested_query}."), parse_mode="HTML", reply_markup=NO_KEYBOARD)
            await app.answer_callback_query(callback_query["id"], "Requested player is unavailable.", show_alert=True)
            return
        if len(matches) == 1:
            requested = matches[0]
            await _update_trade(trade_id, recipient_player_id=int(requested["player_id"]), recipient_player_kind=_kind(requested), recipient_player_json=requested)
        else:
            # Keep the request query in the persisted trade row so the next
            # screen contains only matching recipient cards.
            await _update_trade(trade_id, recipient_player_json={"pending_query": requested_query})
            await _update_trade(trade_id, status="awaiting_recipient")
            chat_id, message_id = _chat_message(callback_query)
            try:
                await app.delete_message(chat_id, message_id)
            except Exception:
                pass
            sent = await app.send_message(
                chat_id,
                _multiple_recipient_text(sender, recipient, offered, requested_query),
                parse_mode="HTML",
                reply_markup=squad_player_keyboard(trade_id, matches, "r", confirm=True),
            )
            await _set_message_id(trade_id, int(sent["message_id"]))
            await app.answer_callback_query(callback_query["id"], "Choose the return player.")
            return

    if requested is not None:
        await _update_trade(trade_id, status="awaiting_recipient")
        chat_id, message_id = _chat_message(callback_query)
        # Delete sender's setup message and create a fresh recipient request.
        try:
            await app.delete_message(chat_id, message_id)
        except Exception:
            pass
        sent = await app.send_message(chat_id, _request_text(sender, recipient, offered, requested), parse_mode="HTML", reply_markup=recipient_request_keyboard(trade_id))
        await _set_message_id(trade_id, int(sent["message_id"]))
        await app.answer_callback_query(callback_query["id"], "Trade request sent.")
        return

    await _update_trade(trade_id, status="awaiting_recipient")
    chat_id, message_id = _chat_message(callback_query)
    try:
        await app.delete_message(chat_id, message_id)
    except Exception:
        pass
    recipient_squad = await get_team_squad(int(recipient["user_id"])) or []
    if requested_query:
        # This branch is defensive. A multi-match requested query is normally
        # handled above; never show a misleading full-squad list here.
        recipient_squad, _ = await _find_player_matches(int(recipient["user_id"]), requested_query)
    sent = await app.send_message(
        chat_id,
        _recipient_choose_text(sender, recipient, offered, requested_query),
        parse_mode="HTML",
        reply_markup=squad_player_keyboard(trade_id, recipient_squad, "r", confirm=bool(requested_query)),
    )
    await _set_message_id(trade_id, int(sent["message_id"]))
    await app.answer_callback_query(callback_query["id"], "Trade request sent.")


@register_callback("trade_sender_cancel")
async def trade_sender_cancel(callback_query):
    parts = _callback_parts(callback_query)
    if len(parts) != 2:
        await app.answer_callback_query(callback_query["id"], "Invalid trade action.", show_alert=True)
        return
    trade_id = int(parts[1]); uid = int((callback_query.get("from") or {}).get("id") or 0); trade = await _get_trade(trade_id)
    actor_status = _callback_actor_status(trade, uid)
    if actor_status == "missing":
        await app.answer_callback_query(callback_query["id"], "This trade no longer exists.", show_alert=True)
        return
    if actor_status == "not_for_user":
        await app.answer_callback_query(callback_query["id"], "This trade is not for you.", show_alert=True)
        return
    if trade["status"] != "awaiting_sender" or int(trade["sender_id"]) != uid:
        await app.answer_callback_query(callback_query["id"], "This trade is no longer active.", show_alert=True)
        return
    await _update_trade(trade_id, status="cancelled")
    actor = (await _load_users(trade))[0]
    chat_id, message_id = _chat_message(callback_query)
    await app.edit_message_text(chat_id, message_id, _cancelled_text(actor), parse_mode="HTML", reply_markup=NO_KEYBOARD)
    await app.answer_callback_query(callback_query["id"], "Trade cancelled.")


@register_callback("trade_sender_back")
async def trade_sender_back(callback_query):
    parts = _callback_parts(callback_query)
    if len(parts) != 2:
        await app.answer_callback_query(callback_query["id"], "Invalid trade action.", show_alert=True)
        return
    trade_id = int(parts[1]); uid = int((callback_query.get("from") or {}).get("id") or 0); trade = await _get_trade(trade_id)
    actor_status = _callback_actor_status(trade, uid)
    if actor_status == "missing":
        await app.answer_callback_query(callback_query["id"], "This trade no longer exists.", show_alert=True)
        return
    if actor_status == "not_for_user":
        await app.answer_callback_query(callback_query["id"], "This trade is not for you.", show_alert=True)
        return
    if int(trade["sender_id"]) != uid or trade["status"] != "awaiting_sender":
        await app.answer_callback_query(callback_query["id"], "This trade step is no longer active.", show_alert=True)
        return
    sender, recipient = await _load_users(trade)
    squad = await get_team_squad(uid) or []
    pending = {}
    try:
        payload = json.loads(trade.get("sender_player_json") or "{}") or {}
        pending = payload if isinstance(payload, dict) else {}
    except Exception:
        pass
    desired = pending.get("requested_query")
    if not desired:
        try:
            recipient_meta = json.loads(trade.get("recipient_player_json") or "{}") or {}
            desired = recipient_meta.get("pending_query")
        except Exception:
            desired = None
    chat_id, message_id = _chat_message(callback_query)
    await app.edit_message_text(chat_id, message_id, _choose_sender_text(sender, recipient, desired=desired), parse_mode="HTML", reply_markup=squad_player_keyboard(trade_id, squad, "s"))
    await app.answer_callback_query(callback_query["id"], "Back to player selection.")


@register_callback("trade_recipient_accept")
async def trade_recipient_accept(callback_query):
    parts = _callback_parts(callback_query)
    if len(parts) != 2:
        await app.answer_callback_query(callback_query["id"], "Invalid trade action.", show_alert=True)
        return
    trade_id = int(parts[1]); uid = int((callback_query.get("from") or {}).get("id") or 0); trade = await _get_trade(trade_id)
    actor_status = _callback_actor_status(trade, uid)
    if actor_status == "missing":
        await app.answer_callback_query(callback_query["id"], "This trade no longer exists.", show_alert=True)
        return
    if actor_status == "not_for_user":
        await app.answer_callback_query(callback_query["id"], "This trade is not for you.", show_alert=True)
        return
    if trade["status"] != "awaiting_recipient" or int(trade["recipient_id"]) != uid:
        await app.answer_callback_query(callback_query["id"], "This trade is no longer active.", show_alert=True)
        return
    # A fixed return card has already been selected. "Accept Trade" still
    # advances to the recipient's final confirmation step; the actual swap is
    # performed only by the explicit "Yes, Confirm" action.
    if trade.get("recipient_player_id"):
        sender, recipient = await _load_users(trade)
        offered = _find_exact_identity(
            await get_team_squad(int(sender["user_id"])) or [],
            int(trade["sender_player_id"]),
            trade["sender_player_kind"],
        )
        requested = _find_exact_identity(
            await get_team_squad(uid) or [],
            int(trade["recipient_player_id"]),
            trade["recipient_player_kind"],
        )
        if not offered or not requested:
            await app.answer_callback_query(callback_query["id"], "One of the selected players is no longer available.", show_alert=True)
            return
        chat_id, message_id = _chat_message(callback_query)
        await app.edit_message_text(
            chat_id,
            message_id,
            _recipient_confirm_text(sender, recipient, offered, requested),
            parse_mode="HTML",
            reply_markup=recipient_confirm_keyboard(trade_id),
        )
        await app.answer_callback_query(callback_query["id"], "Review the trade and confirm it.")
        return
    sender, recipient = await _load_users(trade)
    offered = _find_exact_identity(await get_team_squad(int(sender["user_id"])) or [], int(trade["sender_player_id"]), trade["sender_player_kind"])
    if not offered:
        await _update_trade(trade_id, status="expired")
        chat_id, message_id = _chat_message(callback_query)
        await app.edit_message_text(chat_id, message_id, _expired_text("The offered player is no longer available."), parse_mode="HTML", reply_markup=NO_KEYBOARD)
        await app.answer_callback_query(callback_query["id"], "The offered player is unavailable.", show_alert=True)
        return
    chat_id, message_id = _chat_message(callback_query)
    await app.edit_message_text(chat_id, message_id, _recipient_choose_text(sender, recipient, offered), parse_mode="HTML", reply_markup=squad_player_keyboard(trade_id, await get_team_squad(uid) or [], "r"))
    await app.answer_callback_query(callback_query["id"], "Choose the player you want to trade.")


async def _finalize_trade_message(callback_query: dict, text: str) -> bool:
    """Show the final trade result even when Telegram refuses an edit.

    The database exchange is already committed before this helper runs. An
    edit failure must therefore never leave the user with the old
    confirmation screen and must never trigger a second exchange. We first
    try to edit the callback message, then fall back to a fresh message in
    the same chat.
    """
    chat_id, message_id = _chat_message(callback_query)
    try:
        await app.edit_message_text(
            chat_id, message_id, text, parse_mode="HTML", reply_markup=NO_KEYBOARD
        )
        return True
    except Exception as exc:
        print(f"[trade] final result edit failed for trade message {message_id}: {exc!r}")
        try:
            await app.send_message(
                chat_id, text, parse_mode="HTML", reply_markup=NO_KEYBOARD
            )
            return True
        except Exception as send_exc:
            print(f"[trade] final result fallback send failed for trade message {message_id}: {send_exc!r}")
            return False


@register_callback("trade_recipient_yes")
async def trade_recipient_yes(callback_query):
    parts = _callback_parts(callback_query)
    if len(parts) != 2:
        await app.answer_callback_query(callback_query["id"], "Invalid trade action.", show_alert=True)
        return
    trade_id = int(parts[1]); uid = int((callback_query.get("from") or {}).get("id") or 0); trade = await _get_trade(trade_id)
    actor_status = _callback_actor_status(trade, uid)
    if actor_status == "missing":
        await app.answer_callback_query(callback_query["id"], "This trade no longer exists.", show_alert=True)
        return
    if actor_status == "not_for_user":
        await app.answer_callback_query(callback_query["id"], "This trade is not for you.", show_alert=True)
        return
    if trade["status"] != "awaiting_recipient" or int(trade["recipient_id"]) != uid:
        await app.answer_callback_query(callback_query["id"], "This trade is no longer active.", show_alert=True)
        return
    if not trade.get("recipient_player_id"):
        await app.answer_callback_query(callback_query["id"], "Select your return player first.", show_alert=True)
        return
    if await _active_match(int(trade["sender_id"])) or await _active_match(int(trade["recipient_id"])):
        await app.answer_callback_query(callback_query["id"], "Trade blocked because a participant is in an active match.", show_alert=True)
        return
    if DAILY_TRADE_LIMIT_ENABLED and (await _used_trade_today(int(trade["sender_id"])) or await _used_trade_today(int(trade["recipient_id"]))):
        await app.answer_callback_query(callback_query["id"], "Daily trade limit has been reached.", show_alert=True)
        return

    sender, recipient = await _load_users(trade)

    async def _tx(conn):
        row = await conn.fetchrow("SELECT * FROM trade_requests WHERE trade_id=$1 FOR UPDATE;", trade_id)
        if not row or row["status"] != "awaiting_recipient":
            return "stale"
        expires_at = row["expires_at"]
        expiry = expires_at if getattr(expires_at, "tzinfo", None) else expires_at.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            return "expired"

        first_id, second_id = sorted((int(row["sender_id"]), int(row["recipient_id"])))
        first = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", first_id)
        second = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", second_id)
        if not first or not second:
            return "squad_missing"

        first_squad = first["squad"]
        second_squad = second["squad"]
        if isinstance(first_squad, str): first_squad = json.loads(first_squad)
        if isinstance(second_squad, str): second_squad = json.loads(second_squad)

        sender_id = int(row["sender_id"]); recipient_id = int(row["recipient_id"])
        sender_squad = first_squad if sender_id == first_id else second_squad
        recipient_squad = first_squad if recipient_id == first_id else second_squad

        skind = row["sender_player_kind"]; rkind = row["recipient_player_kind"]
        spid = int(row["sender_player_id"]); rpid = int(row["recipient_player_id"])
        sent = next((dict(p) for p in sender_squad if int(p.get("player_id") or 0) == spid and _kind(p) == skind), None)
        received = next((dict(p) for p in recipient_squad if int(p.get("player_id") or 0) == rpid and _kind(p) == rkind), None)
        if not sent or not received:
            return "card_missing"

        sender_squad = [p for p in sender_squad if not (int(p.get("player_id") or 0) == spid and _kind(p) == skind)]
        recipient_squad = [p for p in recipient_squad if not (int(p.get("player_id") or 0) == rpid and _kind(p) == rkind)]
        sender_squad.append(received)
        recipient_squad.append(sent)

        if sender_id == first_id:
            first_squad, second_squad = sender_squad, recipient_squad
        else:
            second_squad, first_squad = sender_squad, recipient_squad

        await conn.execute("UPDATE team_squads SET squad=$1::jsonb, updated_at=NOW() WHERE user_id=$2;", json.dumps(first_squad, default=str), first_id)
        await conn.execute("UPDATE team_squads SET squad=$1::jsonb, updated_at=NOW() WHERE user_id=$2;", json.dumps(second_squad, default=str), second_id)
        await conn.execute("DELETE FROM user_player_loadouts WHERE user_id=$1 AND player_id=$2 AND player_kind=$3;", sender_id, spid, skind)
        await conn.execute("DELETE FROM user_player_loadouts WHERE user_id=$1 AND player_id=$2 AND player_kind=$3;", recipient_id, rpid, rkind)
        await conn.execute(
            "UPDATE trade_requests SET status='completed', completed_at=NOW(), sender_player_json=$1::jsonb, recipient_player_json=$2::jsonb WHERE trade_id=$3;",
            json.dumps(sent, default=str), json.dumps(received, default=str), trade_id,
        )
        return {"sent": sent, "received": received}

    try:
        result = await transaction(_tx)
    except Exception:
        # The DB transaction wrapper rolls everything back on error.
        chat_id, message_id = _chat_message(callback_query)
        await app.edit_message_text(chat_id, message_id, _failure_text("The trade could not be completed safely. Please try again."), parse_mode="HTML", reply_markup=NO_KEYBOARD)
        await app.answer_callback_query(callback_query["id"], "Trade failed safely. No cards were exchanged.", show_alert=True)
        return

    chat_id, message_id = _chat_message(callback_query)
    if isinstance(result, str) and result in {"stale", "expired", "squad_missing", "card_missing"}:
        reason = {
            "stale": "This trade has already been processed.",
            "expired": "The trade offer has expired.",
            "squad_missing": "A required squad is unavailable.",
            "card_missing": "One of the selected players is no longer available.",
        }[result]
        await app.edit_message_text(chat_id, message_id, _failure_text(reason), parse_mode="HTML", reply_markup=NO_KEYBOARD)
        await app.answer_callback_query(callback_query["id"], reason, show_alert=True)
        return

    final_text = _complete_text(sender, recipient, result["sent"], result["received"])
    displayed = await _finalize_trade_message(callback_query, final_text)
    if displayed:
        await app.answer_callback_query(callback_query["id"], "Trade completed successfully.")
    else:
        await app.answer_callback_query(
            callback_query["id"],
            "Trade completed. The result message could not be displayed in this chat.",
            show_alert=True,
        )


@register_callback("trade_recipient_cancel")
async def trade_recipient_cancel(callback_query):
    parts = _callback_parts(callback_query)
    if len(parts) != 2:
        await app.answer_callback_query(callback_query["id"], "Invalid trade action.", show_alert=True)
        return
    trade_id = int(parts[1]); uid = int((callback_query.get("from") or {}).get("id") or 0); trade = await _get_trade(trade_id)
    actor_status = _callback_actor_status(trade, uid)
    if actor_status == "missing":
        await app.answer_callback_query(callback_query["id"], "This trade no longer exists.", show_alert=True)
        return
    if actor_status == "not_for_user":
        await app.answer_callback_query(callback_query["id"], "This trade is not for you.", show_alert=True)
        return
    if int(trade["recipient_id"]) != uid or trade["status"] != "awaiting_recipient":
        await app.answer_callback_query(callback_query["id"], "This trade is no longer active.", show_alert=True)
        return
    sender, recipient = await _load_users(trade)
    try:
        offered = json.loads(trade.get("sender_player_json") or "{}")
    except Exception:
        offered = {}
    try:
        requested = json.loads(trade.get("recipient_player_json") or "{}") if trade.get("recipient_player_json") else None
    except Exception:
        requested = None
    await _update_trade(trade_id, status="declined")
    chat_id, message_id = _chat_message(callback_query)
    await app.edit_message_text(chat_id, message_id, _declined_text(sender, recipient, offered, requested if requested and requested.get("player_id") else None), parse_mode="HTML", reply_markup=NO_KEYBOARD)
    await app.answer_callback_query(callback_query["id"], "Trade declined.")


@register_callback("trade_recipient_back")
async def trade_recipient_back(callback_query):
    parts = _callback_parts(callback_query)
    if len(parts) != 2:
        await app.answer_callback_query(callback_query["id"], "Invalid trade action.", show_alert=True)
        return
    trade_id = int(parts[1]); uid = int((callback_query.get("from") or {}).get("id") or 0); trade = await _get_trade(trade_id)
    if not trade or int(trade["recipient_id"]) != uid or trade["status"] != "awaiting_recipient":
        await app.answer_callback_query(callback_query["id"], "This trade step is no longer active.", show_alert=True)
        return
    sender, recipient = await _load_users(trade)
    offered = _find_exact_identity(await get_team_squad(int(sender["user_id"])) or [], int(trade["sender_player_id"]), trade["sender_player_kind"])
    if not offered:
        await app.answer_callback_query(callback_query["id"], "The offered player is no longer available.", show_alert=True)
        return
    chat_id, message_id = _chat_message(callback_query)
    await app.edit_message_text(chat_id, message_id, _recipient_choose_text(sender, recipient, offered), parse_mode="HTML", reply_markup=squad_player_keyboard(trade_id, await get_team_squad(uid) or [], "r"))
    await app.answer_callback_query(callback_query["id"], "Back to return-player selection.")
