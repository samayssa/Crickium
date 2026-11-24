from __future__ import annotations

import html
from datetime import datetime, timezone

from app import app
from handlers.registry import register, register_callback
from buttons.play_buttons import _styled_button
from pyrogram.types import InlineKeyboardMarkup
from database import game_pause_repo as repo
from services import game_pause as flow

NO_KEYBOARD = {"inline_keyboard": []}


def _request_keyboard(engine: str, match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        _styled_button("⏸ Yes, Pause", f"pause_yes:{match_id}:{engine}", "success"),
        _styled_button("✖️ Cancel", f"pause_no:{match_id}:{engine}", "danger"),
    ]])


def _request_text(engine: str, requester: str, opponent: str) -> str:
    return (
        "<b>⏸ PAUSE REQUEST</b>\n\n"
        f"{opponent}, {requester} wants to pause this game.\n"
        "Do you also want to pause it?\n"
        "<blockquote>"
        f"🎮 Game engine ➤ {html.escape(flow.ENGINE_LABELS.get(engine, engine))}\n"
        "The match freezes exactly where it is and the inactivity timer stops. "
        "Send /resume whenever you are both ready to continue."
        "</blockquote>"
    )


def _is_group(message: dict) -> bool:
    return "group" in str((message.get("chat") or {}).get("type") or "").lower()


def _side_tags(match: dict, user_id: int) -> tuple[str, str]:
    challenger, opponent = flow.player_tags(match)
    if int(match.get("challenger_id") or 0) == int(user_id):
        return challenger, opponent
    return opponent, challenger


@register("pause")
async def pause_command(message):
    chat = message.get("chat") or {}
    chat_id = int(chat.get("id") or 0)
    user_id = int((message.get("from") or {}).get("id") or 0)

    if not _is_group(message):
        await app.send_message(chat_id, "<b>⚠️ /pause works only inside the group where your game is running.</b>", parse_mode="HTML")
        return

    engine, match = await flow.find_user_game(user_id, chat_id)
    if not match:
        await app.send_message(chat_id, "<b>⚠️ You are not playing any game in this group right now.</b>", parse_mode="HTML")
        return

    match_id = int(match["match_id"])
    existing = await repo.get(engine, match_id)
    if existing and existing["state"] != "requested":
        await app.send_message(chat_id, "<b>⏸ This game is already paused.\nSend /resume when you are ready to continue.</b>", parse_mode="HTML")
        return

    if await flow.is_tournament_match(engine, match_id):
        await app.send_message(chat_id, "<b>⚠️ Tournament matches cannot be paused.</b>", parse_mode="HTML")
        return

    row = await repo.open_request(
        engine, match_id, chat_id, int(match["challenger_id"]), int(match["opponent_id"]), user_id,
    )
    if not row:
        await app.send_message(chat_id, "<b>⏸ This game is already paused.\nSend /resume when you are ready to continue.</b>", parse_mode="HTML")
        return

    requester, opponent = _side_tags(match, user_id)
    sent = await app.send_message(
        chat_id,
        _request_text(engine, requester, opponent),
        parse_mode="HTML",
        reply_markup=_request_keyboard(engine, match_id),
    )
    if sent and sent.get("message_id"):
        await repo.set_request_message(engine, match_id, int(sent["message_id"]))


def _parse(callback_query: dict) -> tuple[str, int] | None:
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) < 3 or not parts[1].isdigit() or parts[2].upper() not in flow.ENGINE_LABELS:
        return None
    return parts[2].upper(), int(parts[1])


async def _alert(callback_query: dict, text: str) -> None:
    await app.answer_callback_query(callback_query["id"], text, show_alert=True)


async def _edit(callback_query: dict, text: str) -> None:
    message = callback_query.get("message") or {}
    try:
        await app.edit_message_text(
            int(message["chat"]["id"]), int(message["message_id"]), text,
            parse_mode="HTML", reply_markup=NO_KEYBOARD,
        )
    except Exception as exc:
        print(f"[pause] could not edit request message: {exc!r}")


def _request_expired(row: dict) -> bool:
    created = row.get("created_at")
    if not created:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds() > flow.REQUEST_MAX_AGE_SECONDS


@register_callback("pause_yes")
async def on_pause_yes(callback_query):
    parsed = _parse(callback_query)
    if not parsed:
        await _alert(callback_query, "This request is no longer valid.")
        return
    engine, match_id = parsed
    presser = int((callback_query.get("from") or {}).get("id") or 0)

    row = await repo.get(engine, match_id)
    if not row or row["state"] != "requested":
        await _alert(callback_query, "This pause request is no longer active.")
        return
    if presser not in {int(row["challenger_id"]), int(row["opponent_id"])}:
        await _alert(callback_query, "Only the two players of this game can respond.")
        return
    if presser == int(row["requested_by"]):
        await _alert(callback_query, "You already asked for the pause. Waiting for your opponent.")
        return

    message_id = int((callback_query.get("message") or {}).get("message_id") or 0)
    if row.get("request_message_id") and message_id and int(row["request_message_id"]) != message_id:
        await _alert(callback_query, "This pause request is outdated.")
        return

    match = await flow.load_match(engine, match_id)
    if not flow.is_live_status(match):
        await repo.drop_request(engine, match_id)
        await _edit(callback_query, "<b>⚠️ This game has already ended, so there is nothing to pause.</b>")
        await _alert(callback_query, "This game has already ended.")
        return
    if _request_expired(row):
        await repo.drop_request(engine, match_id)
        await _edit(callback_query, "<b>⌛ This pause request expired. Send /pause again if you still want to pause.</b>")
        await _alert(callback_query, "This pause request expired.")
        return

    confirmed = await repo.confirm(engine, match_id)
    if not confirmed:
        await _alert(callback_query, "This pause request is no longer active.")
        return
    await flow.apply_pause(confirmed, match)

    first, second = flow.player_tags(match)
    await _edit(
        callback_query,
        "<b>⏸ GAME PAUSED</b>\n\n"
        f"{first} ⚔️ {second}\n"
        "<blockquote>"
        f"🎮 Game engine ➤ {html.escape(flow.ENGINE_LABELS.get(engine, engine))}\n"
        "Both players agreed. The match is saved exactly where it stands and the inactivity timer is stopped."
        "</blockquote>\n"
        "<b>Send /resume when you are ready to continue.</b>",
    )
    await app.answer_callback_query(callback_query["id"], "Game paused.")


@register_callback("pause_no")
async def on_pause_no(callback_query):
    parsed = _parse(callback_query)
    if not parsed:
        await _alert(callback_query, "This request is no longer valid.")
        return
    engine, match_id = parsed
    presser = int((callback_query.get("from") or {}).get("id") or 0)

    row = await repo.get(engine, match_id)
    if not row or row["state"] != "requested":
        await _alert(callback_query, "This pause request is no longer active.")
        return
    if presser not in {int(row["challenger_id"]), int(row["opponent_id"])}:
        await _alert(callback_query, "Only the two players of this game can respond.")
        return

    dropped = await repo.drop_request(engine, match_id)
    if not dropped:
        await _alert(callback_query, "This pause request is no longer active.")
        return
    if presser == int(row["requested_by"]):
        await _edit(callback_query, "<b>✖️ Pause request withdrawn. The game goes on.</b>")
    else:
        await _edit(callback_query, "<b>✖️ Pause request declined. The game goes on.</b>")
    await app.answer_callback_query(callback_query["id"], "Request cancelled.")
