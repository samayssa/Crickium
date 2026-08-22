from __future__ import annotations

import uuid

from handlers.registry import register, register_callback
from app import app
from config import ADMIN_USER_ID
from database.access_repo import has_upload_access
from database.players_repo import parse_player_line
from database.special_players_repo import split_player_edition, get_delete_targets, delete_global_player, delete_special_player
from buttons.delp_buttons import delete_confirm_keyboard

_PENDING: dict[str, dict] = {}


async def _allowed(user_id: int) -> bool:
    return int(user_id or 0) == int(ADMIN_USER_ID) or await has_upload_access(int(user_id or 0))


def _parse_rows(raw_text: str) -> list[dict]:
    return [
        {"line": line.strip()}
        for line in (raw_text or "").splitlines()
        if line.strip()
    ]


@register("delp")
async def delp_command(message):
    chat_id = message["chat"]["id"]
    user_id = int((message.get("from") or {}).get("id") or 0)
    if not await _allowed(user_id):
        await app.send_message(chat_id, "🚫 This command is restricted to the bot owner or users granted admin player access via /access.")
        return

    reply_to = message.get("reply_to_message")
    if not reply_to or not reply_to.get("text"):
        await app.send_message(chat_id, "⚠️ Please use /delp as a reply to the player list message you want to delete.")
        return

    found = []
    missing = []
    invalid = []
    for line in _parse_rows(reply_to["text"]):
        player, parse_error = parse_player_line(line["line"])
        if parse_error:
            invalid.append(parse_error)
            continue
        target = await get_delete_targets(player)
        target["line"] = line["line"]
        if target["player"]:
            found.append(target)
        else:
            missing.append(target)

    if not found:
        text = "⚠️ No matching players were found in the database."
        if missing:
            text += f"\n\n⚠️ {len(missing)} player(s) are not in the database."
        if invalid:
            text += f"\n\n❌ {len(invalid)} invalid line(s)."
        await app.send_message(chat_id, text)
        return

    global_count = sum(1 for x in found if x["kind"] == "global")
    special_count = sum(1 for x in found if x["kind"] == "special")
    token = uuid.uuid4().hex[:12]
    _PENDING[token] = {"owner_id": user_id, "targets": found}

    text = (
        f"⚠️ <b>DELETE PLAYERS</b>\n\n"
        f"Are you sure you want to delete <b>{len(found)} player(s)</b> from the database?\n\n"
        f"🌍 Global Pool ➤ <b>{global_count}</b>\n"
        f"✨ Special Edition ➤ <b>{special_count}</b>"
    )
    if missing:
        text += f"\n⚠️ Not in Database ➤ <b>{len(missing)}</b>"
    if invalid:
        text += f"\n❌ Invalid Lines ➤ <b>{len(invalid)}</b>"
    text += "\n\nThis action cannot be undone."
    await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=delete_confirm_keyboard(token))


@register_callback("delp_confirm")
async def delp_confirm(callback_query):
    token = (callback_query.get("data") or "").split(":", 1)[-1]
    state = _PENDING.get(token)
    user_id = int((callback_query.get("from") or {}).get("id") or 0)
    if not state or user_id != int(state.get("owner_id") or 0):
        await app.answer_callback_query(callback_query["id"], "This delete request is no longer valid.", show_alert=True)
        return
    if not await _allowed(user_id):
        await app.answer_callback_query(callback_query["id"], "Admin access required.", show_alert=True)
        return

    deleted_global = 0
    deleted_special = 0
    missing_now = 0
    for target in state["targets"]:
        try:
            if target["kind"] == "global":
                current = await get_delete_targets({"name": target["name"]})
                player = current.get("player")
                if not player:
                    missing_now += 1
                    continue
                if await delete_global_player(int(player["player_id"])):
                    deleted_global += 1
            else:
                current = await get_delete_targets({"name": f"{target['name']} ({target['edition']})"})
                player = current.get("player")
                if not player:
                    missing_now += 1
                    continue
                if await delete_special_player(int(player["special_edition_id"])):
                    deleted_special += 1
        except Exception as exc:
            print(f"[delp] deletion failed for {target}: {exc!r}")

    _PENDING.pop(token, None)
    await app.answer_callback_query(callback_query["id"], "Players deleted.")
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    text = (
        f"✅ <b>Players Deleted</b>\n\n"
        f"🌍 Global Pool ➤ <b>{deleted_global}</b>\n"
        f"✨ Special Edition ➤ <b>{deleted_special}</b>"
    )
    if missing_now:
        text += f"\n⚠️ Already missing ➤ <b>{missing_now}</b>"
    await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML")


@register_callback("delp_cancel")
async def delp_cancel(callback_query):
    token = (callback_query.get("data") or "").split(":", 1)[-1]
    state = _PENDING.get(token)
    user_id = int((callback_query.get("from") or {}).get("id") or 0)
    if not state or user_id != int(state.get("owner_id") or 0):
        await app.answer_callback_query(callback_query["id"], "This request is no longer valid.", show_alert=True)
        return
    _PENDING.pop(token, None)
    await app.answer_callback_query(callback_query["id"], "Delete cancelled.")
    await app.edit_message_text(callback_query["message"]["chat"]["id"], callback_query["message"]["message_id"], "❌ <b>Delete cancelled.</b>", parse_mode="HTML")
