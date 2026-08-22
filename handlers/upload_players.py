print("upload_players.py loaded")

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID
from database.players_repo import bulk_upload_players, parse_player_line
from database.special_players_repo import parse_special_player_line, insert_special_player, split_player_edition
from database.playint_repo import insert_playint_player, parse_playint_player_line
from database.playint_teams_repo import normalize_team_keyword, team_name
from database.access_repo import has_upload_access


@register("upload_pl")
async def upload_players_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")

    print(f"[upload_pl] Command invoked by user_id={user_id} username=@{from_user.get('username')}")

    # ---- Owner, or a user granted access via /access ----
    if user_id != ADMIN_USER_ID and not await has_upload_access(user_id):
        print(f"[upload_pl] REJECTED: user_id={user_id} is not the bot owner and has no granted access.")
        await app.send_message(
            chat_id,
            "🚫 This command is restricted to the bot owner, or users the owner has granted access to via /access."
        )
        return

    # ---- Optional PlayInt team upload: /upload_pl T20I-IND ----
    parts = (message.get("text") or "").split()
    playint_keyword = parts[1] if len(parts) > 1 else None
    team_code = normalize_team_keyword(playint_keyword) if playint_keyword else None
    if playint_keyword and team_code:
        reply_to = message.get("reply_to_message")
        if not reply_to or "text" not in reply_to:
            await app.send_message(chat_id, "⚠️ Please use /upload_pl T20I-XXX as a reply to a message containing player data.")
            return
        raw_text = reply_to["text"]
        uploaded = already = failed = 0
        details = []
        for line in [l for l in raw_text.splitlines() if l.strip()]:
            player, error = parse_playint_player_line(line)
            if error:
                failed += 1; details.append(error); continue
            try:
                inserted, _row = await insert_playint_player(team_code, team_name(team_code), player, user_id, engine_key="T20I")
                if inserted:
                    uploaded += 1
                else:
                    already += 1
            except Exception as exc:
                failed += 1; details.append(f"{player.get('name','Player')}: {exc}")
        report = (f"📋 <b>PlayInt Team Upload Report</b>\n\n"
                  f"🌍 Team: <b>{team_name(team_code)}</b>\n"
                  f"✅ Saved: {uploaded}\n♻️ Updated/Existing: {already}\n❌ Failed: {failed}")
        if details:
            report += "\n\n<b>Failure details:</b>\n" + "\n".join(f"• {d}" for d in details[:15])
        await app.send_message(chat_id, report, parse_mode="HTML")
        return

    # ---- Must be used as a reply to a text message ----
    reply_to = message.get("reply_to_message")
    if not reply_to or "text" not in reply_to:
        print("[upload_pl] REJECTED: not used as a reply to a text message.")
        await app.send_message(
            chat_id,
            "⚠️ Please use /upload_pl as a *reply* to a message containing player data.\n\n"
            "Format (one player per line):\n"
            "`[Player Name][Country][Role][RH/LH-BAT <LEVEL>][RAF/LAF/RAM/LAM/RAO/LAO/RAL/LAL <LEVEL>]`\n\n"
            "Batting field stays RH/LH-BAT, bowling field now uses arm-style codes like RAF/LAF/RAM/LAM/RAO/LAO/RAL/LAL.\n\n"
            "Example:\n"
            "`[Virat Kohli][India][Batsman][RH-BAT 96][RAF 38]`"
        )
        return

    raw_text = reply_to["text"]
    print(f"[upload_pl] Processing replied text ({len(raw_text)} chars)...")

    # Preserve the existing global-pool behavior exactly for ordinary lines,
    # while routing bracketed edition names to the independent special table.
    global_lines = []
    special_lines = []
    for line in [l for l in raw_text.splitlines() if l.strip()]:
        player_probe, probe_error = parse_player_line(line)
        if not probe_error:
            _base, edition = split_player_edition(player_probe.get("name", ""))
            (special_lines if edition else global_lines).append(line)
        else:
            # Let the global parser produce its normal failure detail unless the
            # line clearly declares an edition, in which case the special parser
            # will provide the appropriate edition-specific validation.
            if "(" in line and ")" in line:
                special_lines.append(line)
            else:
                global_lines.append(line)

    summary = await bulk_upload_players("\n".join(global_lines), uploaded_by=user_id) if global_lines else {
        "total_lines": 0, "uploaded": 0, "already_exists": 0, "failed": 0, "failed_details": []
    }

    special_uploaded = 0
    special_exists = 0
    special_failed = 0
    special_failures = []
    for line in special_lines:
        player, error = parse_special_player_line(line)
        if error:
            special_failed += 1
            special_failures.append(error)
            continue
        try:
            inserted, _row = await insert_special_player(player, uploaded_by=user_id)
            if inserted:
                special_uploaded += 1
            else:
                special_exists += 1
        except Exception as exc:
            special_failed += 1
            special_failures.append(f"{player.get('name','Player')} ({player.get('edition','Edition')}): {exc}")

    total_lines = summary["total_lines"] + len(special_lines)
    all_failures = summary["failed_details"] + special_failures
    lines = [
        "📋 *Player Upload Report*",
        "",
        f"📥 Total lines processed: {total_lines}",
        f"✅ Newly uploaded: {summary['uploaded'] + special_uploaded}",
        f"♻️ Already in database: {summary['already_exists'] + special_exists}",
        f"❌ Failed: {summary['failed'] + special_failed}",
    ]
    if special_lines:
        lines.extend(["", f"✨ Special editions: {special_uploaded} uploaded, {special_exists} already in special database"])

    if all_failures:
        lines.append("")
        lines.append("*Failure details:*")
        for detail in all_failures[:15]:
            lines.append(f"• {detail}")
        if len(all_failures) > 15:
            lines.append(f"...and {len(all_failures) - 15} more.")

    report = "\n".join(lines)
    print(f"[upload_pl] Sending report to chat_id={chat_id}")
    await app.send_message(chat_id, report, parse_mode="Markdown")
    print("[upload_pl] Done.")
