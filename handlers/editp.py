from __future__ import annotations

import html
import re

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID
from database.access_repo import has_upload_access
from database.players_repo import normalize_role
from database.squads_repo import sync_player_snapshot
from database.playint_repo import get_engine_team_player, update_engine_team_player
from database.playint_teams_repo import normalize_team_keyword as normalize_t20i_team
from database.playipl_teams_repo import normalize_team_keyword as normalize_ipl_team
from database.special_players_repo import (
    split_player_edition,
    get_special_player,
    get_special_player_by_id,
    update_global_player,
    update_special_player,
)

_KEY_MAP = {
    "n": "name",
    "name": "name",
    "sp": "edition",
    "c": "country",
    "country": "country",
    "r": "role",
    "role": "role",
    "batlv": "bat_level",
    "batlevel": "bat_level",
    "balllv": "bowl_level",
    "bowllv": "bowl_level",
    "bowllevel": "bowl_level",
    "bats": "batting_hand",
    "batstyle": "batting_hand",
    "balls": "bowling_hand",
    "ballstyle": "bowling_hand",
}
BATTING_HANDS = {"RH", "LH"}
BOWLING_STYLES = {"RAF", "LAF", "RAM", "LAM", "RAO", "LAO", "RAL", "LAL"}


def _allowed(user_id: int) -> bool:
    return int(user_id or 0) == int(ADMIN_USER_ID)


async def _has_access(user_id: int) -> bool:
    return _allowed(user_id) or await has_upload_access(user_id)


def _parse_line(line: str):
    fields = re.findall(r"\[([^\[\]]*)\]", line or "")
    if len(fields) < 2:
        return None, "Use [Player Name] plus one or more [key=value] fields."
    identity = fields[0].strip()
    changes = {}
    for raw in fields[1:]:
        if "=" not in raw:
            return None, f"Invalid field {raw!r}; expected key=value."
        key, value = raw.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        mapped = _KEY_MAP.get(key)
        if not mapped:
            return None, f"Unknown edit key: {key}"
        if not value:
            return None, f"Value for {key} cannot be empty."
        changes[mapped] = value
    return {"identity": identity, "changes": changes}, None


def _normalize_value(field: str, value: str):
    if field in {"bat_level", "bowl_level"}:
        if not value.isdigit() or not 0 <= int(value) <= 100:
            raise ValueError(f"{field} must be 0-100")
        return int(value)
    if field == "role":
        role = normalize_role(value)
        if not role:
            raise ValueError("role must be Batsman, Bowler, AllRounder or Wicketkeeper")
        return role
    if field == "batting_hand":
        value = value.upper()
        if value not in BATTING_HANDS:
            raise ValueError("batting style must be RH or LH")
        return value
    if field == "bowling_hand":
        value = value.upper()
        if value not in BOWLING_STYLES:
            raise ValueError("bowling style must be RAF/LAF/RAM/LAM/RAO/LAO/RAL/LAL")
        return value
    return value


async def _resolve_target(identity: str, changes: dict):
    base_name, embedded_edition = split_player_edition(identity)
    requested_target_edition = embedded_edition
    # If no edition is embedded, `sp=` can select an existing special edition to edit.
    if not requested_target_edition and "edition" in changes:
        requested_target_edition = changes["edition"]
        # When it points to an existing special, treat sp= as selector unless
        # the identity itself already names a special edition.
        special = await get_special_player(base_name, requested_target_edition)
        if special:
            return "special", special, {k: v for k, v in changes.items() if k != "edition"}
        # A bare global identity cannot silently be converted into a special player.
        raise ValueError(f"No special edition player named {base_name} ({requested_target_edition}) exists.")

    if requested_target_edition:
        special = await get_special_player(base_name, requested_target_edition)
        if not special:
            raise ValueError(f"Special edition player {base_name} ({requested_target_edition}) was not found in the special database.")
        return "special", special, changes

    from database.players_repo import get_player
    global_player = await get_player(base_name)
    if not global_player:
        raise ValueError(f"Global player {base_name} was not found in the database.")
    if "edition" in changes:
        raise ValueError("Use [Player Name (Current Edition)] when changing a special player's edition.")
    return "global", global_player, changes


def _engine_team_arg(text: str):
    parts = str(text or "").split()
    if len(parts) < 2:
        return None
    raw = parts[1].strip()
    if raw.upper().startswith("T20I-"):
        code = normalize_t20i_team(raw)
        return ("T20I", code) if code else None
    if raw.upper().startswith("IPL-"):
        code = normalize_ipl_team(raw)
        return ("IPL", code) if code else None
    return None


async def _edit_engine_team(message, engine: str, team_code: str) -> bool:
    chat_id = int(message["chat"]["id"])
    reply_to = message.get("reply_to_message")
    if not reply_to or not reply_to.get("text"):
        await app.send_message(chat_id, "⚠️ Please use /editp ENGINE-TEAM as a reply to the player edit line(s).", parse_mode="HTML")
        return True
    errors, success = [], []
    for line in [x for x in str(reply_to["text"]).splitlines() if x.strip()]:
        parsed, parse_error = _parse_line(line)
        if parse_error:
            errors.append(parse_error)
            continue
        try:
            player = await get_engine_team_player(engine, team_code, parsed["identity"])
            if not player:
                raise ValueError(f"{parsed['identity']}: player was not found in {engine}-{team_code}.")
            if "edition" in parsed["changes"]:
                raise ValueError("edition is only available for special/global players, not engine squad players.")
            normalized = {field: _normalize_value(field, value) for field, value in parsed["changes"].items()}
            updated = await update_engine_team_player(engine, team_code, int(player["player_id"]), normalized)
            if not updated:
                raise ValueError(f"{parsed['identity']}: player disappeared before update.")
            success.append((parsed["identity"], player, updated, normalized))
        except Exception as exc:
            errors.append(str(exc))
    lines_out = ["✅ <b>Player Edit Complete</b>", "", f"🎮 Engine ➤ <b>{html.escape(engine)}</b>", f"🏏 Team ➤ <b>{html.escape(team_code)}</b>"]
    for identity, old_player, updated, changed in success:
        lines_out.append(f"\n✅ <b>{html.escape(identity)}</b>")
        for field, value in changed.items():
            lines_out.append(f"• {field} ➤ <code>{html.escape(str(old_player.get(field)))}</code> → <code>{html.escape(str(value))}</code>")
    if errors:
        lines_out.extend(["", "⚠️ <b>Not changed</b>"] + [f"• {html.escape(e)}" for e in errors[:20]])
    await app.send_message(chat_id, "\n".join(lines_out), parse_mode="HTML")
    return True


@register("editp")
async def editp_command(message):
    chat_id = message["chat"]["id"]
    user_id = int((message.get("from") or {}).get("id") or 0)
    if not await _has_access(user_id):
        await app.send_message(chat_id, "🚫 This command is restricted to the bot owner or users granted admin player access via /access.")
        return
    text = str(message.get("text") or "").strip()
    engine_team = _engine_team_arg(text)
    if engine_team:
        return await _edit_engine_team(message, *engine_team)
    reply_to = message.get("reply_to_message")
    if not reply_to or not reply_to.get("text"):
        await app.send_message(chat_id, "⚠️ Please use /editp as a reply to the player edit line(s).")
        return

    success = []
    errors = []
    for line in [x for x in reply_to["text"].splitlines() if x.strip()]:
        parsed, parse_error = _parse_line(line)
        if parse_error:
            errors.append(parse_error)
            continue
        try:
            kind, player, changes = await _resolve_target(parsed["identity"], parsed["changes"])
            normalized = {field: _normalize_value(field, value) for field, value in changes.items()}
            old = {field: player.get(field) for field in normalized}
            updated = await (update_special_player(player["special_edition_id"], normalized) if kind == "special" else update_global_player(player["player_id"], normalized))
            if not updated:
                errors.append(f"{parsed['identity']}: player disappeared before update.")
                continue
            # Players are denormalized into purchased squad JSON. Keep every
            # owned copy in sync with the authoritative player record.
            snapshot = {
                key: updated.get(key)
                for key in ("name", "edition", "country", "role", "bat_level", "bowl_level", "batting_hand", "bowling_hand", "is_special", "special_edition_id", "player_id")
                if key in updated
            }
            await sync_player_snapshot(int(updated.get("player_id") or player.get("player_id") or 0), snapshot)
            success.append((parsed["identity"], kind, old, normalized))
        except Exception as exc:
            errors.append(f"{parsed['identity']}: {exc}")

    lines = [f"✅ <b>Player Edit Complete</b>", ""]
    if success:
        for identity, kind, old, new in success:
            label = "✨ Special" if kind == "special" else "🌍 Global"
            lines.append(f"{label} • <b>{identity}</b>")
            for field, value in new.items():
                lines.append(f"• {field}: <code>{old.get(field)}</code> ➜ <code>{value}</code>")
    if errors:
        lines.extend(["", "⚠️ <b>Not changed</b>"])
        lines.extend(f"• {e}" for e in errors[:20])
    await app.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
