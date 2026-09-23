from __future__ import annotations

print("upload_players.py loaded")

import re

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID
from database.players_repo import bulk_upload_players, parse_player_line
from database.special_players_repo import parse_special_player_line, insert_special_player, split_player_edition
from database.playint_repo import insert_playint_player, parse_playint_player_line
from database.playint_teams_repo import normalize_team_keyword, team_name
from database.playipl_repo import parse_playipl_player_line, upsert_playipl_player
from database.playipl_teams_repo import normalize_team_keyword as normalize_ipl_team_keyword, team_name as ipl_team_name
from database.access_repo import has_upload_access


TEAM_HEADER_RE = re.compile(r"^(T20I|IPL)\s*-\s*([A-Za-z0-9_]+)\s*$", re.IGNORECASE)


def _chat_id(message: dict) -> int:
    return int((message.get("chat") or {}).get("id") or 0)


def _user_id(message: dict) -> int:
    return int((message.get("from") or {}).get("id") or 0)


async def _read_replied_source(message: dict) -> tuple[str | None, str | None]:
    """Read the player source from a replied text message or TXT document.

    Returns (text, error_message). Blank source is returned as (None, None)
    so the caller can display the normal upload-format guidance.
    """
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
        return None, "⚠️ Please reply to a player-data message or a <code>.txt</code> text file."

    try:
        raw = await app.download_media(str(file_id))
        return raw.decode("utf-8-sig", errors="replace").strip(), None
    except Exception as exc:
        return None, f"⚠️ I could not read that TXT file: <code>{exc}</code>"


def _team_scope(header: str):
    """Validate an embedded team section header.

    Returns (engine_key, team_code, team_name, error).  An invalid header never
    falls back to the global player table.
    """
    match = TEAM_HEADER_RE.match(header.strip())
    if not match:
        return None, None, None, None

    engine = match.group(1).upper()
    raw_team = match.group(2)

    if engine == "T20I":
        code = normalize_team_keyword(f"T20I-{raw_team}")
        if not code:
            return "INVALID", None, None, f"Invalid T20I team code in section header: {header!r}"
        return "T20I", code, team_name(code), None

    code = normalize_ipl_team_keyword(f"IPL-{raw_team}")
    if not code:
        return "INVALID", None, None, f"Invalid IPL team code in section header: {header!r}"
    return "IPL", code, ipl_team_name(code), None


def _has_team_sections(raw_text: str) -> bool:
    return any(TEAM_HEADER_RE.match(line.strip()) for line in raw_text.splitlines() if line.strip())


async def _upload_team_line(engine: str, team_code: str, player_line: str, uploaded_by: int):
    if engine == "T20I":
        player, error = parse_playint_player_line(player_line)
        if error:
            return False, False, error
        inserted, row = await insert_playint_player(
            team_code,
            team_name(team_code),
            player,
            uploaded_by,
            engine_key="T20I",
        )
        return bool(inserted), not bool(inserted), None

    player, error = parse_playipl_player_line(player_line)
    if error:
        return False, False, error
    ok, row = await upsert_playipl_player(
        team_code,
        ipl_team_name(team_code),
        player,
        uploaded_by,
    )
    # PlayIPL intentionally preserves its existing upsert semantics: an
    # accepted row is reported as saved/updated, not as "already exists".
    return bool(ok), False, None


async def _process_mixed_source(raw_text: str, uploaded_by: int) -> dict:
    """Process global/special players plus multiple T20I/IPL team sections.

    Syntax:
        T20I-IND
        [Player]...

        IPL-RCB
        [Player]...

    Blank lines are ignored. A malformed team header puts the parser into an
    INVALID section until another valid team header is encountered, ensuring
    those following lines can never accidentally enter the global players table.
    """
    global_lines: list[str] = []
    special_lines: list[str] = []
    team_items: list[dict] = []
    errors: list[str] = []

    current_scope = None
    for line_no, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        scope_engine, scope_code, scope_name, header_error = _team_scope(line)
        if scope_engine is not None:
            if header_error:
                current_scope = ("INVALID", None, line)
                errors.append(f"Line {line_no}: {header_error}")
            else:
                current_scope = (scope_engine, scope_code, scope_name)
            continue

        if current_scope and current_scope[0] == "INVALID":
            errors.append(
                f"Line {line_no}: player line ignored because the previous team section header was invalid: {line!r}"
            )
            continue

        if current_scope:
            if not line.startswith("["):
                errors.append(
                    f"Line {line_no}: expected a player line under {current_scope[0]}-{current_scope[1]}, got {line!r}"
                )
                continue
            team_items.append(
                {
                    "line_no": line_no,
                    "engine": current_scope[0],
                    "team_code": current_scope[1],
                    "team_name": current_scope[2],
                    "line": line,
                }
            )
            continue

        # No team section is active. Preserve the existing global/special
        # player behavior exactly.
        player_probe, probe_error = parse_player_line(line)
        if not probe_error:
            _base, edition = split_player_edition(player_probe.get("name", ""))
            (special_lines if edition else global_lines).append(line)
        else:
            if "(" in line and ")" in line:
                special_lines.append(line)
            else:
                global_lines.append(line)

    summary = {
        "global": {"total": len(global_lines), "uploaded": 0, "exists": 0, "failed": 0, "details": []},
        "special": {"total": len(special_lines), "uploaded": 0, "exists": 0, "failed": 0, "details": []},
        "teams": {},
        "section_errors": errors,
    }

    if global_lines:
        global_result = await bulk_upload_players("\n".join(global_lines), uploaded_by=uploaded_by)
        summary["global"] = {
            "total": int(global_result.get("total_lines", len(global_lines))),
            "uploaded": int(global_result.get("uploaded", 0)),
            "exists": int(global_result.get("already_exists", 0)),
            "failed": int(global_result.get("failed", 0)),
            "details": list(global_result.get("failed_details", [])),
        }

    for line in special_lines:
        player, error = parse_special_player_line(line)
        if error:
            summary["special"]["failed"] += 1
            summary["special"]["details"].append(error)
            continue
        try:
            inserted, _row = await insert_special_player(player, uploaded_by=uploaded_by)
            if inserted:
                summary["special"]["uploaded"] += 1
            else:
                summary["special"]["exists"] += 1
        except Exception as exc:
            summary["special"]["failed"] += 1
            summary["special"]["details"].append(
                f"{player.get('name', 'Player')} ({player.get('edition', 'Edition')}): {exc}"
            )

    for item in team_items:
        key = (item["engine"], item["team_code"])
        bucket = summary["teams"].setdefault(
            key,
            {
                "engine": item["engine"],
                "team_code": item["team_code"],
                "team_name": item["team_name"],
                "total": 0,
                "uploaded": 0,
                "exists": 0,
                "failed": 0,
                "details": [],
            },
        )
        bucket["total"] += 1
        try:
            uploaded, exists, error = await _upload_team_line(
                item["engine"], item["team_code"], item["line"], uploaded_by
            )
            if error:
                bucket["failed"] += 1
                bucket["details"].append(f"Line {item['line_no']}: {error}")
            elif uploaded:
                bucket["uploaded"] += 1
            elif exists:
                bucket["exists"] += 1
            else:
                bucket["failed"] += 1
                bucket["details"].append(f"Line {item['line_no']}: player was not saved")
        except Exception as exc:
            bucket["failed"] += 1
            bucket["details"].append(f"Line {item['line_no']}: {exc}")

    return summary


def _render_upload_report(summary: dict) -> str:
    lines = [
        "📋 <b>PLAYER UPLOAD REPORT</b>",
        "",
        f"🌐 Global Players: <b>{summary['global']['uploaded']}</b> new • <b>{summary['global']['exists']}</b> existing • <b>{summary['global']['failed']}</b> failed",
        f"✨ Special Editions: <b>{summary['special']['uploaded']}</b> new • <b>{summary['special']['exists']}</b> existing • <b>{summary['special']['failed']}</b> failed",
    ]

    for key in sorted(summary["teams"]):
        bucket = summary["teams"][key]
        lines.append(
            f"{'🇮🇳' if bucket['engine'] == 'T20I' else '🏏'} "
            f"<b>{bucket['engine']} • {bucket['team_name']} ({bucket['team_code']})</b>: "
            f"{bucket['uploaded']} saved • {bucket['exists']} existing • {bucket['failed']} failed"
        )

    all_details = list(summary.get("section_errors", []))
    all_details.extend(summary["global"]["details"])
    all_details.extend(summary["special"]["details"])
    for key in sorted(summary["teams"]):
        all_details.extend(summary["teams"][key]["details"])

    if all_details:
        lines.extend(["", "<b>Failure details:</b>"])
        lines.extend(f"• {detail}" for detail in all_details[:25])
        if len(all_details) > 25:
            lines.append(f"• …and {len(all_details) - 25} more.")

    total_processed = (
        summary["global"]["total"]
        + summary["special"]["total"]
        + sum(v["total"] for v in summary["teams"].values())
    )
    total_saved = (
        summary["global"]["uploaded"]
        + summary["special"]["uploaded"]
        + sum(v["uploaded"] for v in summary["teams"].values())
    )
    total_failed = (
        summary["global"]["failed"]
        + summary["special"]["failed"]
        + sum(v["failed"] for v in summary["teams"].values())
    )
    lines.extend([
        "",
        f"📥 <b>Total processed:</b> {total_processed}",
        f"✅ <b>Total saved:</b> {total_saved}",
        f"❌ <b>Total failed:</b> {total_failed}",
    ])
    return "\n".join(lines)


@register("upload_pl")
async def upload_players_command(message):
    chat_id = _chat_id(message)
    user_id = _user_id(message)

    print(
        f"[upload_pl] Command invoked by user_id={user_id} "
        f"username=@{(message.get('from') or {}).get('username')}"
    )

    if user_id != ADMIN_USER_ID and not await has_upload_access(user_id):
        await app.send_message(
            chat_id,
            "🚫 This command is restricted to the bot owner, or users the owner has granted access to via /access.",
        )
        return

    command_parts = (message.get("text") or "").split()
    explicit_target = command_parts[1].strip() if len(command_parts) > 1 else None

    # Read the reply source once. This now supports both normal text replies and
    # .txt file replies for every upload mode.
    raw_text, source_error = await _read_replied_source(message)
    if source_error:
        await app.send_message(chat_id, source_error, parse_mode="HTML")
        return
    if not raw_text:
        await app.send_message(
            chat_id,
            "⚠️ Please use /upload_pl as a reply to player data text or a <code>.txt</code> file.\n\n"
            "Player format:\n"
            "<code>[Player Name][Country][Role][RH/LH-BAT &lt;LEVEL&gt;][RAF/LAF/RAM/LAM/RAO/LAO/RAL/LAL &lt;LEVEL&gt;]</code>\n\n"
            "Multi-team format:\n"
            "<code>T20I-IND</code>\n"
            "<code>[Player Name][Country][Role][RH-BAT 96][RAF 38]</code>\n\n"
            "<code>IPL-RCB</code>\n"
            "<code>[Player Name][Country][Role][RH-BAT 96][RAF 38]</code>",
            parse_mode="HTML",
        )
        return

    # A command target such as /upload_pl IPL-RCB or /upload_pl T20I-IND
    # remains supported exactly as before. If the source itself contains team
    # section headers, the embedded headers win and the whole source is routed
    # through the new multi-team parser.
    target_ipl = normalize_ipl_team_keyword(explicit_target) if explicit_target else None
    target_int = normalize_team_keyword(explicit_target) if explicit_target else None

    if _has_team_sections(raw_text):
        summary = await _process_mixed_source(raw_text, uploaded_by=user_id)
        await app.send_message(chat_id, _render_upload_report(summary), parse_mode="HTML")
        return

    if target_ipl:
        uploaded = failed = 0
        details = []
        existing_or_updated = 0
        for line_no, line in enumerate((l for l in raw_text.splitlines() if l.strip()), start=1):
            player, error = parse_playipl_player_line(line)
            if error:
                failed += 1
                details.append(f"Line {line_no}: {error}")
                continue
            try:
                ok, _row = await upsert_playipl_player(
                    target_ipl,
                    ipl_team_name(target_ipl),
                    player,
                    user_id,
                )
                if ok:
                    uploaded += 1
                else:
                    existing_or_updated += 1
            except Exception as exc:
                failed += 1
                details.append(f"Line {line_no}: {player.get('name', 'Player')}: {exc}")

        report = (
            f"📋 <b>PlayIPL Franchise Upload Report</b>\n\n"
            f"🏏 Franchise: <b>{ipl_team_name(target_ipl)} ({target_ipl})</b>\n"
            f"✅ Saved/Updated: {uploaded}\n♻️ Existing: {existing_or_updated}\n❌ Failed: {failed}"
        )
        if details:
            report += "\n\n<b>Failure details:</b>\n" + "\n".join(f"• {d}" for d in details[:20])
        await app.send_message(chat_id, report, parse_mode="HTML")
        return

    if target_int:
        uploaded = already = failed = 0
        details = []
        for line_no, line in enumerate((l for l in raw_text.splitlines() if l.strip()), start=1):
            player, error = parse_playint_player_line(line)
            if error:
                failed += 1
                details.append(f"Line {line_no}: {error}")
                continue
            try:
                inserted, _row = await insert_playint_player(
                    target_int,
                    team_name(target_int),
                    player,
                    user_id,
                    engine_key="T20I",
                )
                if inserted:
                    uploaded += 1
                else:
                    already += 1
            except Exception as exc:
                failed += 1
                details.append(f"Line {line_no}: {player.get('name', 'Player')}: {exc}")

        report = (
            f"📋 <b>PlayInt Team Upload Report</b>\n\n"
            f"🌍 Team: <b>{team_name(target_int)}</b>\n"
            f"✅ Saved: {uploaded}\n♻️ Existing: {already}\n❌ Failed: {failed}"
        )
        if details:
            report += "\n\n<b>Failure details:</b>\n" + "\n".join(f"• {d}" for d in details[:20])
        await app.send_message(chat_id, report, parse_mode="HTML")
        return

    # No explicit team target: preserve the existing global + special edition
    # uploader, while allowing the same source to contain all supported methods.
    summary = await _process_mixed_source(raw_text, uploaded_by=user_id)
    await app.send_message(chat_id, _render_upload_report(summary), parse_mode="HTML")
    print("[upload_pl] Done.")
