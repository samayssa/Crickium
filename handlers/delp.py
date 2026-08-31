from __future__ import annotations

import html
import re
import uuid

from handlers.registry import register, register_callback
from app import app
from database.access_repo import has_upload_access
from database.players_repo import parse_player_line
from database.playint_repo import get_engine_team_player, delete_engine_team_player
from database.playint_teams_repo import normalize_team_keyword as normalize_t20i_team, team_name as t20i_team_name
from database.playipl_teams_repo import normalize_team_keyword as normalize_ipl_team, team_name as ipl_team_name
from database.special_players_repo import (
    split_player_edition,
    get_delete_targets,
    delete_global_player,
    delete_special_player,
    search_delete_candidates,
    get_delete_target_by_player_id,
)
from buttons.delp_buttons import delete_confirm_keyboard
from config import ADMIN_USER_ID

_PENDING: dict[str, dict] = {}


async def _allowed(user_id: int) -> bool:
    return int(user_id or 0) == int(ADMIN_USER_ID) or await has_upload_access(int(user_id or 0))


def _parse_rows(raw_text: str) -> list[dict]:
    return [
        {"line": line.strip()}
        for line in (raw_text or "").splitlines()
        if line.strip()
    ]


def _candidate_label(player: dict) -> str:
    name = str(player.get("name") or "Unknown").strip()
    if player.get("is_special") and player.get("edition"):
        return f"{name} ({player['edition']})"
    return name


def _candidate_id(player: dict) -> str:
    if player.get("is_special"):
        return str(int(player.get("player_id") or 0))
    return str(int(player.get("player_id") or 0))


def _candidate_target(player: dict) -> dict:
    if player.get("is_special"):
        return {
            "kind": "special",
            "player": player,
            "name": str(player.get("name") or "").strip(),
            "edition": str(player.get("edition") or "").strip(),
        }
    return {
        "kind": "global",
        "player": player,
        "name": str(player.get("name") or "").strip(),
        "edition": None,
    }


async def _delete_candidates_from_name(query: str) -> list[dict]:
    query = str(query or "").strip()
    if not query:
        return []
    base_name, edition = split_player_edition(query)
    if edition:
        # Resolve special syntax first, but allow legacy global records whose
        # actual stored name contains the same parenthesized text.
        target = await get_delete_targets({"name": query})
        return [target] if target.get("player") else []
    rows = await search_delete_candidates(base_name)
    return [_candidate_target(row) for row in rows]


async def _show_single_confirmation(chat_id: int, user_id: int, target: dict):
    player = target["player"]
    label = _candidate_label(player)
    if target["kind"] == "special":
        scope = "special edition"
    elif target["kind"] == "engine_team":
        scope = f"{target.get('engine')} • {target.get('team_code')} squad"
    else:
        scope = "global pool"
    token = uuid.uuid4().hex[:12]
    _PENDING[token] = {"owner_id": user_id, "targets": [target]}
    text = (
        f"⚠️ <b>DELETE PLAYER</b>\n\n"
        f"Player ➤ <b>{html.escape(label)}</b>\n"
        f"Player ID ➤ <code>{html.escape(_candidate_id(player))}</code>\n\n"
        f"Are you sure you want to delete <b>{html.escape(label)}</b> from the database, "
        f"from the <b>{html.escape(scope)}</b>?\n\n"
        f"This action cannot be undone."
    )
    await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=delete_confirm_keyboard(token))


async def _show_multiple_matches(chat_id: int, query: str, targets: list[dict]):
    label = html.escape(query)
    lines = [f"⚠️ <b>There are multiple players matching {label}</b>.", "", "Select the exact player by copying one of these commands:", ""]
    for target in targets:
        player = target.get("player")
        if not player:
            continue
        exact = _candidate_label(player)
        pid = _candidate_id(player)
        lines.append(
            f"• <b>{html.escape(exact)}</b>\n"
            f"  ID ➤ <code>{html.escape(pid)}</code>\n"
            f"  <code>/delp {html.escape(exact)}</code>"
        )
    lines.extend(["", "Special edition players must include their edition in brackets."])
    await app.send_message(chat_id, "\n".join(lines), parse_mode="HTML")


def _engine_team_arg(text: str):
    parts = str(text or "").split()
    if len(parts) < 2:
        return None
    raw = parts[1].strip()
    if raw.upper().startswith("T20I-"):
        code = normalize_t20i_team(raw)
        return ("T20I", code, t20i_team_name(code)) if code else None
    if raw.upper().startswith("IPL-"):
        code = normalize_ipl_team(raw)
        return ("IPL", code, ipl_team_name(code)) if code else None
    return None


def _plain_player_name(line: str) -> str:
    text = str(line or "").strip()
    bracketed = re.findall(r"\[([^\[\]]*)\]", text)
    if bracketed:
        return str(bracketed[0]).strip()
    text = re.sub(r"^\s*(?:[-•]\s*)?\d+\.\s*", "", text)
    text = re.sub(r"^\s*[•-]\s*", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    # Team reports use 'Name • Role • BAT x • BOWL y'.
    return text.split(" • ", 1)[0].strip()


async def _show_engine_team_confirmation(chat_id: int, user_id: int, engine: str, code: str, full_name: str, targets: list[dict]):
    token = uuid.uuid4().hex[:12]
    _PENDING[token] = {"owner_id": user_id, "targets": targets}
    lines = [
        "⚠️ <b>DELETE PLAYERS</b>",
        "",
        f"🎮 Engine ➤ <b>{html.escape(engine)}</b>",
        f"🏏 Team ➤ <b>{html.escape(full_name)} ({html.escape(code)})</b>",
        "",
        f"Are you sure you want to delete <b>{len(targets)} player(s)</b> from this squad?",
        "",
        "This action cannot be undone.",
    ]
    await app.send_message(chat_id, "\n".join(lines), parse_mode="HTML", reply_markup=delete_confirm_keyboard(token))


async def _del_engine_team(message, engine: str, team_code: str, full_name: str) -> bool:
    chat_id = int(message["chat"]["id"])
    user_id = int((message.get("from") or {}).get("id") or 0)
    reply_to = message.get("reply_to_message")
    if not reply_to or not reply_to.get("text"):
        await app.send_message(chat_id, "⚠️ Please use /delp ENGINE-TEAM as a reply to the player list you want to delete.", parse_mode="HTML")
        return True
    found, missing = [], []
    for raw_line in str(reply_to["text"]).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("🏏") or line.startswith("<"):
            continue
        name = _plain_player_name(line)
        if not name or name.lower() in {"squad", "engine"}:
            continue
        row = await get_engine_team_player(engine, team_code, name)
        if row:
            found.append({"kind": "engine_team", "engine": engine, "team_code": team_code, "team_name": full_name, "player": dict(row), "name": str(row["name"]), "edition": None})
        else:
            missing.append(name)
    if not found:
        await app.send_message(chat_id, "⚠️ No matching squad players were found in the database.", parse_mode="HTML")
        return True
    if len(found) == 1 and not missing:
        await _show_single_confirmation(chat_id, user_id, found[0])
        return True
    if missing:
        # Keep the same confirmation workflow and simply report the skipped rows.
        pass
    await _show_engine_team_confirmation(chat_id, user_id, engine, team_code, full_name, found)
    return True


@register("delp")
async def delp_command(message):
    chat_id = message["chat"]["id"]
    user_id = int((message.get("from") or {}).get("id") or 0)
    if not await _allowed(user_id):
        await app.send_message(chat_id, "🚫 This command is restricted to the bot owner or users granted admin player access via /access.")
        return

    text = str(message.get("text") or "").strip()
    engine_team = _engine_team_arg(text)
    if engine_team:
        return await _del_engine_team(message, *engine_team)
    parts = text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    reply_to = message.get("reply_to_message")

    # Existing reply-to-list workflow remains unchanged.
    if reply_to and reply_to.get("text") and not arg:
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
        return

    if not arg:
        await app.send_message(chat_id, "⚠️ Use /delp <player name> or reply to the player list you want to delete.")
        return

    # Direct workflow: exact player-id lookup, then name / special-edition lookup.
    # Positive IDs address global players. Negative IDs address special editions
    # through the existing command-facing namespace.
    try:
        numeric_id = int(arg)
    except (TypeError, ValueError):
        numeric_id = None

    if numeric_id is not None:
        target = await get_delete_target_by_player_id(numeric_id)
        if not target:
            await app.send_message(
                chat_id,
                f"⚠️ No player with ID <code>{html.escape(str(numeric_id))}</code> was found in the database.",
                parse_mode="HTML",
            )
            return
        await _show_single_confirmation(chat_id, user_id, target)
        return

    # Name workflow: exact special syntax first; otherwise flexible substring matching.
    targets = await _delete_candidates_from_name(arg)
    if not targets:
        await app.send_message(
            chat_id,
            f"⚠️ No player matching <b>{html.escape(arg)}</b> was found in the database.\n\n"
            f"Use <code>/delp {html.escape(arg)}</code> with a valid player name or player ID.",
            parse_mode="HTML",
        )
        return

    if len(targets) == 1:
        await _show_single_confirmation(chat_id, user_id, targets[0])
        return

    await _show_multiple_matches(chat_id, arg, targets)


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
    deleted_engine = 0
    missing_now = 0
    for target in state["targets"]:
        try:
            if target["kind"] == "engine_team":
                if await delete_engine_team_player(target["engine"], target["team_code"], int(target["player"]["player_id"])):
                    deleted_engine += 1
                else:
                    missing_now += 1
            elif target["kind"] == "global":
                player = await get_delete_targets({"name": target["name"]})
                current = player.get("player")
                if not current:
                    missing_now += 1
                    continue
                if await delete_global_player(int(current["player_id"])):
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
        f"✨ Special Edition ➤ <b>{deleted_special}</b>\n"
        f"🎮 Engine Squad ➤ <b>{deleted_engine}</b>"
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
