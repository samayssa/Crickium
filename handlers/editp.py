from __future__ import annotations

import re

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID
from database.access_repo import has_upload_access
from database.players_repo import normalize_role
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


@register("editp")
async def editp_command(message):
    chat_id = message["chat"]["id"]
    user_id = int((message.get("from") or {}).get("id") or 0)
    if not await _has_access(user_id):
        await app.send_message(chat_id, "🚫 This command is restricted to the bot owner or users granted admin player access via /access.")
        return
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
