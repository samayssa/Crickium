from __future__ import annotations

import html
import re

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID
from services.card_identity import collection_label
from database.access_repo import has_upload_access
from database.players_repo import normalize_role
from database.squads_repo import sync_player_snapshot
from database.playint_repo import get_engine_team_player, update_engine_team_player
from database.playint_teams_repo import normalize_team_keyword as normalize_t20i_team, team_name as t20i_team_name
from database.playipl_teams_repo import normalize_team_keyword as normalize_ipl_team, team_name as ipl_team_name
from database.playwpl_teams_repo import normalize_team_keyword as normalize_wpl_team, team_name as wpl_team_name
from database.playwbbl_teams_repo import normalize_team_keyword as normalize_wbbl_team, team_name as wbbl_team_name
from database.playbbl_teams_repo import normalize_team_keyword as normalize_bbl_team, team_name as bbl_team_name
from database.playilt_teams_repo import normalize_team_keyword as normalize_ilt_team, team_name as ilt_team_name
from database.special_players_repo import (
    split_player_edition,
    get_special_player,
    get_special_player_by_id,
    update_global_player,
    update_special_player,
)
from database.showcase_players_repo import get_showcase_player_by_identity, update_showcase_player, parse_showcase_image_target

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


async def _resolve_target(identity: str, changes: dict, showcase_name: str | None = None):
    if showcase_name:
        player = await get_showcase_player_by_identity(identity.strip(), showcase_name)
        if not player:
            raise ValueError(f"Showcase player {identity} [{showcase_name}] was not found in the Showcase database.")
        return "showcase", player, changes
    showcase_target = parse_showcase_image_target(identity)
    if showcase_target:
        base_name, target_showcase = showcase_target
        player = await get_showcase_player_by_identity(base_name, target_showcase)
        if not player:
            raise ValueError(f"Showcase player {base_name} [{target_showcase}] was not found in the Showcase database.")
        return "showcase", player, changes
    base_name, embedded_edition = split_player_edition(identity)
    requested_target_edition = embedded_edition
                                                                                      
    if not requested_target_edition and "edition" in changes:
        requested_target_edition = changes["edition"]
                                                                             
                                                              
        special = await get_special_player(base_name, requested_target_edition)
        if special:
            return "special", special, {k: v for k, v in changes.items() if k != "edition"}
                                                                                    
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


def _showcase_scope_arg(text: str) -> str | None:
    parts = str(text or "").strip().split(maxsplit=1)
    if len(parts) < 2 or parts[0].lower() not in ("showcase", "legacy"):
        return None
    value = parts[1].strip()
    return value or None


# Match the same section-header syntax accepted by /upload_pl.
TEAM_HEADER_RE = re.compile(
    r"^(T20I|IPL|WPL|WBBL|BBL|ILT)\s*-\s*([A-Za-z0-9_]+)\s*$",
    re.IGNORECASE,
)
TEAM_PREFIX_RE = re.compile(r"^(T20I|IPL|WPL|WBBL|BBL|ILT)\s*-", re.IGNORECASE)
TEAM_CONFIG = {
    "T20I": (normalize_t20i_team, t20i_team_name),
    "IPL": (normalize_ipl_team, ipl_team_name),
    "WPL": (normalize_wpl_team, wpl_team_name),
    "WBBL": (normalize_wbbl_team, wbbl_team_name),
    "BBL": (normalize_bbl_team, bbl_team_name),
    "ILT": (normalize_ilt_team, ilt_team_name),
}


async def _read_replied_source(message: dict) -> tuple[str | None, str | None]:
    """Read a replied text message or .txt document, as /upload_pl does."""
    reply_to = message.get("reply_to_message") or {}
    reply_text = str(reply_to.get("text") or "").strip()
    if reply_text:
        return reply_text, None

    document = reply_to.get("document") or {}
    file_id = document.get("file_id")
    if not file_id:
        return None, None

    file_name = str(document.get("file_name") or "").strip().lower()
    mime_type = str(document.get("mime_type") or "").lower()
    if not file_name.endswith(".txt") and mime_type not in {"text/plain", "text/csv"}:
        return None, "⚠️ Please reply to a player-edit text message or a <code>.txt</code> file."

    try:
        raw = await app.download_media(str(file_id))
        return raw.decode("utf-8-sig", errors="replace").strip(), None
    except Exception as exc:
        return None, f"⚠️ I could not read that TXT file: <code>{html.escape(str(exc))}</code>"


def _team_scope(header: str):
    """Resolve a section header to (engine, team_code, team_name, error)."""
    match = TEAM_HEADER_RE.fullmatch(str(header or "").strip())
    if not match:
        return None, None, None, None

    engine = match.group(1).upper()
    raw_team = match.group(2).strip().upper()
    normalizer, get_team_name = TEAM_CONFIG[engine]

    # Pass the bare code to the normalizer. This also avoids depending on a
    # prefix-stripping quirk in older WBBL normalizer revisions.
    code = normalizer(raw_team)
    if not code:
        return "INVALID", None, None, f"Invalid {engine} team code in section header: {header!r}"
    return engine, code, get_team_name(code), None


def _engine_team_arg(text: str):
    """Support the legacy explicit form: /editp BBL-BRH."""
    parts = str(text or "").split()
    if len(parts) < 2:
        return None
    engine, code, _name, error = _team_scope(parts[1])
    if error or not engine or engine == "INVALID":
        return None
    return engine, code


async def _apply_engine_edit(engine: str, team_code: str, line: str):
    parsed, parse_error = _parse_line(line)
    if parse_error:
        raise ValueError(parse_error)

    player = await get_engine_team_player(engine, team_code, parsed["identity"])
    if not player:
        raise ValueError(f"{parsed['identity']}: player was not found in {engine}-{team_code}.")
    if "edition" in parsed["changes"]:
        raise ValueError("edition is only available for special/global players, not engine squad players.")

    normalized = {
        field: _normalize_value(field, value)
        for field, value in parsed["changes"].items()
    }
    updated = await update_engine_team_player(
        engine, team_code, int(player["player_id"]), normalized
    )
    if not updated:
        raise ValueError(f"{parsed['identity']}: player disappeared before update.")
    return parsed["identity"], player, updated, normalized


async def _edit_engine_team(message, engine: str, team_code: str) -> bool:
    """Legacy one-franchise mode, now accepting a replied TXT document too."""
    chat_id = int(message["chat"]["id"])
    source, source_error = await _read_replied_source(message)
    if source_error:
        await app.send_message(chat_id, source_error, parse_mode="HTML")
        return True
    if not source:
        await app.send_message(
            chat_id,
            "⚠️ Reply to a text message or .txt file containing lines like "
            "<code>[Player Name][batlv=50]</code>.",
            parse_mode="HTML",
        )
        return True

    # When a header-based multi-franchise file is supplied, the headers are
    # authoritative even if the command also contains an explicit team code.
    if await _edit_sectioned_source(chat_id, source):
        return True

    errors, success = [], []
    for line_no, line in enumerate(source.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            success.append(await _apply_engine_edit(engine, team_code, line.strip()))
        except Exception as exc:
            errors.append(f"Line {line_no}: {exc}")

    await _send_engine_edit_report(chat_id, [(engine, team_code, success)], errors)
    return True


async def _send_engine_edit_report(chat_id: int, groups: list, errors: list[str]):
    lines_out = ["✅ <b>Player Edit Complete</b>", ""]
    total = sum(len(items) for _engine, _code, items in groups)
    lines_out.append(f"✅ Updated: <b>{total}</b>")

    for engine, team_code, success in groups:
        _normalizer, get_team_name = TEAM_CONFIG[engine]
        lines_out.append(
            f"\n🎮 <b>{html.escape(engine)}</b> • "
            f"🏏 <b>{html.escape(get_team_name(team_code))} ({html.escape(team_code)})</b>"
        )
        for identity, old_player, _updated, changed in success:
            lines_out.append(f"\n✅ <b>{html.escape(identity)}</b>")
            for field, value in changed.items():
                old_value = old_player.get(field)
                lines_out.append(
                    f"• {html.escape(field)}: "
                    f"<code>{html.escape(str(old_value))}</code> → "
                    f"<code>{html.escape(str(value))}</code>"
                )

    if errors:
        lines_out.extend(["", f"⚠️ <b>Not changed: {len(errors)}</b>"])
        lines_out.extend(f"• {html.escape(str(error))}" for error in errors[:30])
        if len(errors) > 30:
            lines_out.append(f"• …and {len(errors) - 30} more.")
    await app.send_message(chat_id, "\n".join(lines_out), parse_mode="HTML")


async def _edit_sectioned_source(chat_id: int, raw_text: str) -> bool:
    """Apply a mixed-engine edit file whose section headers mirror /upload_pl.

    Returns False when no supported team header is present, allowing the
    existing global/special/showcase edit workflow to continue unchanged.
    """
    rows = []
    errors: list[str] = []
    current_scope = None
    saw_header = False
    invalid_scope = False

    for line_no, raw_line in enumerate(str(raw_text or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if TEAM_HEADER_RE.fullmatch(line):
            saw_header = True
            engine, team_code, team_display_name, header_error = _team_scope(line)
            if header_error:
                current_scope = None
                invalid_scope = True
                errors.append(f"Line {line_no}: {header_error}")
            else:
                current_scope = (engine, team_code, team_display_name)
                invalid_scope = False
                rows.append({"scope": current_scope, "line_no": line_no, "line": None})
            continue

        # A malformed header should not accidentally be parsed as a player edit.
        if TEAM_PREFIX_RE.match(line):
            saw_header = True
            current_scope = None
            invalid_scope = True
            errors.append(f"Line {line_no}: invalid team section header: {line!r}")
            continue

        if current_scope is None:
            if invalid_scope:
                errors.append(
                    f"Line {line_no}: player edit ignored because the previous team section header was invalid."
                )
            else:
                errors.append(
                    f"Line {line_no}: expected a team section header (for example BBL-ADS or ILT-ADKR) before player edits."
                )
            continue

        rows.append({"scope": current_scope, "line_no": line_no, "line": line})

    if not saw_header:
        return False

    grouped_success: dict[tuple[str, str], list] = {}
    grouped_order: list[tuple[str, str]] = []
    for row in rows:
        if row["line"] is None:
            engine, code, _team_display_name = row["scope"]
            key = (engine, code)
            if key not in grouped_success:
                grouped_success[key] = []
                grouped_order.append(key)
            continue

        engine, code, _team_display_name = row["scope"]
        key = (engine, code)
        if key not in grouped_success:
            grouped_success[key] = []
            grouped_order.append(key)
        try:
            grouped_success[key].append(await _apply_engine_edit(engine, code, row["line"]))
        except Exception as exc:
            errors.append(f"Line {row['line_no']} ({engine}-{code}): {exc}")

    report_groups = [
        (engine, code, grouped_success[(engine, code)])
        for engine, code in grouped_order
    ]
    await _send_engine_edit_report(chat_id, report_groups, errors)
    return True


@register("editp")
async def editp_command(message):
    chat_id = message["chat"]["id"]
    user_id = int((message.get("from") or {}).get("id") or 0)
    if not await _has_access(user_id):
        await app.send_message(
            chat_id,
            "🚫 This command is restricted to the bot owner or users granted admin player access via /access.",
        )
        return

    command_text = str(message.get("text") or "").strip()
    engine_team = _engine_team_arg(command_text)
    if engine_team:
        return await _edit_engine_team(message, *engine_team)

    showcase_scope = _showcase_scope_arg(command_text)
    raw_text, source_error = await _read_replied_source(message)
    if source_error:
        await app.send_message(chat_id, source_error, parse_mode="HTML")
        return
    if not raw_text:
        await app.send_message(
            chat_id,
            "⚠️ Reply to a player-edit text message or a <code>.txt</code> file with <code>/editp</code>.\n\n"
            "For franchise squads, use the same section headers as <code>/upload_pl</code>:\n"
            "<code>BBL-ADS</code> or <code>ILT-ADKR</code>\n"
            "Then add one or more lines such as <code>[Player Name][batlv=50][bowllv=45]</code>.\n\n"
            "You can include multiple franchise sections in one TXT file.",
            parse_mode="HTML",
        )
        return

    # New upload-style engine sections: BBL-ADS, ILT-ADKR, WBBL-ADS, etc.
    if await _edit_sectioned_source(chat_id, raw_text):
        return

    # Original global / special / showcase editing remains supported for
    # non-sectioned text messages and TXT documents.
    success = []
    errors = []
    for line_no, line in enumerate(raw_text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parsed, parse_error = _parse_line(line)
        if parse_error:
            errors.append(f"Line {line_no}: {parse_error}")
            continue
        try:
            kind, player, changes = await _resolve_target(
                parsed["identity"], parsed["changes"], showcase_scope
            )
            normalized = {
                field: _normalize_value(field, value)
                for field, value in changes.items()
            }
            old = {field: player.get(field) for field in normalized}
            if kind == "special":
                updated = await update_special_player(player["special_edition_id"], normalized)
            elif kind == "showcase":
                updated = await update_showcase_player(player["showcase_card_id"], normalized)
            else:
                updated = await update_global_player(player["player_id"], normalized)
            if not updated:
                errors.append(f"Line {line_no}: {parsed['identity']}: player disappeared before update.")
                continue

            snapshot = {
                key: updated.get(key)
                for key in (
                    "name", "edition", "country", "role", "bat_level", "bowl_level",
                    "batting_hand", "bowling_hand", "is_special", "is_showcase",
                    "player_kind", "special_edition_id", "showcase_card_id",
                    "showcase_set_id", "showcase_name", "set_name",
                    "showcase_set_name", "player_id",
                )
                if key in updated and updated.get(key) is not None
            }
            await sync_player_snapshot(
                int(updated.get("player_id") or player.get("player_id") or 0), snapshot
            )
            success.append((parsed["identity"], "legacy" if kind == "showcase" and collection_label(player) == "Legacy" else kind, old, normalized))
        except Exception as exc:
            errors.append(f"Line {line_no}: {parsed['identity']}: {exc}")

    lines = ["✅ <b>Player Edit Complete</b>", ""]
    if success:
        for identity, kind, old, new in success:
            label = "✨ Special" if kind == "special" else ("🕰️ Legacy" if kind == "legacy" else ("🏆 Showcase" if kind == "showcase" else "🌍 Global"))
            lines.append(f"{label} • <b>{html.escape(identity)}</b>")
            for field, value in new.items():
                lines.append(
                    f"• {html.escape(field)}: "
                    f"<code>{html.escape(str(old.get(field)))}</code> → "
                    f"<code>{html.escape(str(value))}</code>"
                )
    if errors:
        lines.extend(["", f"⚠️ <b>Not changed: {len(errors)}</b>"])
        lines.extend(f"• {html.escape(str(error))}" for error in errors[:30])
        if len(errors) > 30:
            lines.append(f"• …and {len(errors) - 30} more.")
    await app.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
