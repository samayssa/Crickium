from __future__ import annotations

import asyncio
import html
import json
import secrets
from typing import Any

from handlers.registry import register, register_callback
from app import app
from database.auction_tournament_repo import (
    ACTIVE_DRAFT_STATUSES,
    assign_team,
    create_draft,
    create_ipl_teams,
    create_pools,
    fetch_all_pool_players,
    fetch_pool_summary,
    fetch_teams,
    find_user_by_identifier,
    get_active_draft,
    get_team,
    get_tournament,
    get_tournament_for_prize,
    get_owned_running_tournaments,
    find_owned_running_tournament,
    create_tournament_backup,
    lookup_tournament_backup,
    restore_tournament_backup,
    RUNNING_TOURNAMENT_STATUSES,
    get_user_registration,
    remove_team_owner,
    update_tournament,
)
from database.query import fetchrow
from database.squads_repo import get_team_squad
from services.auction_tournament_session import sync_tournament_session
from services.auction_tournament import (
    IPL_TEAM_MAP,
    IPL_TEAM_ORDER,
    build_prize_breakdown,
    cancel_keyboard,
    confirm_prize_keyboard,
    create_registration_announcement,
    create_tournament_final,
    create_type_keyboard,
    esc,
    final_create_keyboard,
    group_choice_keyboard,
    handle_tournament_reply,
    mode_keyboard,
    parse_pool_text,
    parse_prize_input,
    pool_confirmation_keyboard,
    prize_command_text,
    public_group_info,
    render_creation_success,
    render_final_review,
    render_group_review,
    render_pool_prompt,
    render_prize_confirmation,
    render_prize_prompt,
    render_tournament_overview,
    team_keyboard,
    teamown_confirm_keyboard,
    update_group_board,
    teamown_keyboard,
    remove_confirm_keyboard,
    validate_pool_players,
    resolve_owned_prize_player,
)

NO_KEYBOARD = {"inline_keyboard": []}


def _chat_type(message: dict) -> str:
    value = str((message.get("chat") or {}).get("type") or "").strip().lower()
    return value


def _is_group_chat(message: dict) -> bool:
    return _chat_type(message) in {"group", "supergroup", "chat_type.group", "chat_type.supergroup"}


def _user_id(message: dict) -> int:
    return int((message.get("from") or {}).get("id") or 0)


def _arg_text(message: dict) -> str:
    text = str(message.get("text") or "").strip()
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else ""


def _tournament_owner_ok(tournament: Any, user_id: int) -> bool:
    return tournament is not None and int(tournament["creator_id"]) == int(user_id)


async def _edit_prompt(tournament_id: int, text: str, markup: dict | None = None):
    tournament = await get_tournament(tournament_id)
    if not tournament:
        return
    chat_id = int(tournament["creation_chat_id"])
    message_id = int(tournament.get("prompt_message_id") or tournament.get("overview_message_id") or 0)
    if message_id:
        await app.edit_message_text(
            chat_id,
            message_id,
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )


async def _set_prompt_id(tournament_id: int, message_id: int):
    await update_tournament(tournament_id, prompt_message_id=int(message_id), overview_message_id=int(message_id))


async def _send_cancelled(tournament_id: int):
    tournament = await get_tournament(tournament_id)
    if not tournament:
        return
    await update_tournament(tournament_id, status="cancelled")
    try:
        await app.edit_message_text(
            int(tournament["creation_chat_id"]),
            int(tournament.get("prompt_message_id") or tournament.get("overview_message_id") or 0),
            "<b>❌ TOURNAMENT CREATION CANCELLED</b>\n\nNo tournament was created and no player card was removed from your squad.",
            parse_mode="HTML",
            reply_markup=NO_KEYBOARD,
        )
    except Exception as exc:
        print(f"[tournament] cancellation edit failed: {exc!r}")


@register("createtour")
async def create_tour_command(message: dict):
    user_id = _user_id(message)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    first_name = (message.get("from") or {}).get("first_name") or "Tournament Host"
    username = (message.get("from") or {}).get("username")

    existing = await get_active_draft(user_id)
    if existing:
        await app.send_message(
            chat_id,
            "<b>⚠️ You already have a tournament creation in progress.</b>\n\n"
            "Use the buttons on your current setup message or press Cancel before starting a new tournament.",
            parse_mode="HTML",
        )
        return

    text = (
        "<b>╭━━〔 🏆 CREATE TOURNAMENT 〕━━╮</b>\n\n"
        "Choose the game instance for your tournament.\n\n"
        "<blockquote>"
        "🏏 <b>Indian Premier League (IPL)</b>\n"
        "Total teams: <b>10</b>\n\n"
        "🌍 <b>T20 World Cup Tournament</b>\n"
        "Total teams: <b>10/20</b>\n\n"
        "🧩 <b>Custom Tournament</b>\n"
        "Create your own tournament name and let users participate with their own collectible squads."
        "</blockquote>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )
    await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=create_type_keyboard(user_id))
    # create_draft is persistent DB state; mirror it to the restart-safe session file.
    if existing is None:
        fresh = await get_active_draft(user_id)
        if fresh:
            await sync_tournament_session(int(fresh["tournament_id"]))


def _pool_player_preview_line(player: dict) -> str:
    edition = f" ({esc(player.get("edition"))})" if player.get("edition") else ""
    return f"├ {esc(player.get("name"))}{edition} • OVR {int(player.get("ovr") or 0)}"


@register("setpool")
async def setpool_command(message: dict):
    user_id = _user_id(message)
    arg = _arg_text(message).upper()
    if arg not in {"IPL", "PLAYIPL"}:
        await app.send_message(
            int((message.get("chat") or {}).get("id") or 0),
            "⚠️ For the current auction module, use <code>/setpool IPL</code>.",
            parse_mode="HTML",
        )
        return

    tournament = await get_active_draft(user_id)
    if not tournament:
        await app.send_message(
            int((message.get("chat") or {}).get("id") or 0),
            "⚠️ You do not have an active IPL auction-tournament creation in progress.",
        )
        return

    if str(tournament["status"]) not in {"overview", "await_pool", "confirm_pool"}:
        await app.send_message(
            int((message.get("chat") or {}).get("id") or 0),
            "⚠️ The tournament is not currently waiting for an auction pool.",
        )
        return

    reply = message.get("reply_to_message") or {}
    source_text = str(reply.get("text") or "").strip()
    source_document = reply.get("document") or {}
    if not source_text and source_document.get("file_id"):
        file_name = str(source_document.get("file_name") or "").lower()
        if not file_name.endswith(".txt"):
            await app.send_message(
                int((message.get("chat") or {}).get("id") or 0),
                "⚠️ The replied document must be a <code>.txt</code> file.",
                parse_mode="HTML",
            )
            return
        try:
            raw = await app.download_media(str(source_document["file_id"]))
            source_text = raw.decode("utf-8-sig", errors="replace")
        except Exception as exc:
            await app.send_message(
                int((message.get("chat") or {}).get("id") or 0),
                f"⚠️ I could not read that TXT file: <code>{esc(exc)}</code>",
                parse_mode="HTML",
            )
            return

    if not source_text:
        prompt_message_id = int(tournament.get("overview_message_id") or tournament.get("prompt_message_id") or 0)
        chat_id = int(tournament["creation_chat_id"])
        if prompt_message_id:
            await update_tournament(tournament["tournament_id"], status="await_pool", prompt_message_id=prompt_message_id)
            await app.edit_message_text(
                chat_id,
                prompt_message_id,
                render_pool_prompt(),
                parse_mode="HTML",
                reply_markup=cancel_keyboard(int(tournament["tournament_id"])),
            )
        else:
            sent = await app.send_message(chat_id, render_pool_prompt(), parse_mode="HTML", reply_markup=cancel_keyboard(int(tournament["tournament_id"])))
            await _set_prompt_id(int(tournament["tournament_id"]), int(sent["message_id"]))
        return

    pools, errors = parse_pool_text(source_text)
    valid_pools, errors, success, failed = await validate_pool_players(pools, errors)
    if success == 0:
        errors.append({"pool": "-", "line": 0, "detail": "No valid auction players were found. Nothing can be saved until at least one player is valid."})

    preview = valid_pools
    tid = int(tournament["tournament_id"])
    await update_tournament(
        tid,
        status="confirm_pool" if success > 0 else "await_pool",
        pool_preview=preview,
        pool_errors=errors,
        pool_count=len(valid_pools),
        player_count=success,
        prompt_message_id=int(tournament.get("overview_message_id") or tournament.get("prompt_message_id") or 0),
    )

    summary_lines = [
        "<b>╭━━〔 📦 AUCTION POOL REVIEW 〕━━╮</b>",
        "",
        f"✅ <b>Valid pools</b>  : {len(valid_pools)}",
        f"✅ <b>Valid players</b>: {success}",
        f"❌ <b>Failed entries</b>: {len(errors)}",
        "",
    ]
    for pool in valid_pools:
        summary_lines.append(
            f"<b>[Pool {int(pool['pool_no'])}: {esc(pool['pool_name'])}]</b> • Base {int(pool['base_price']):,}\n"
            + "\n".join(_pool_player_preview_line(p) for p in pool['players'])
        )
        summary_lines.append("")

    if errors:
        summary_lines += ["<blockquote><b>⚠️ VALIDATION DETAILS</b>"]
        for err in errors[:30]:
            summary_lines.append(
                f"Pool {esc(err.get('pool'))}, line {esc(err.get('line'))}: {esc(err.get('detail'))}"
            )
        if len(errors) > 30:
            summary_lines.append(f"… and {len(errors) - 30} more errors.")
        summary_lines.append("</blockquote>")
        summary_lines.append("")

    summary_lines += [
        "<b>Are you sure you want to set these players for the IPL auction?</b>",
        "",
        "Choose <b>Yes, go ahead</b> to save the valid details, <b>No, change it</b> to resend the pool, or <b>Cancel</b> to stop.",
        "",
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>",
    ]

    prompt_id = int(tournament.get("overview_message_id") or tournament.get("prompt_message_id") or 0)
    summary_text = "\n".join(summary_lines)
    summary_markup = pool_confirmation_keyboard(tid) if success else cancel_keyboard(tid)
    if not prompt_id:
        sent = await app.send_message(
            int(tournament["creation_chat_id"]),
            summary_text,
            parse_mode="HTML",
            reply_markup=summary_markup,
        )
        await _set_prompt_id(tid, int(sent["message_id"]))
    else:
        try:
            await app.edit_message_text(
                int(tournament["creation_chat_id"]),
                prompt_id,
                summary_text,
                parse_mode="HTML",
                reply_markup=summary_markup,
            )
        except Exception as exc:
            # Telegram can reject an edit because of a stale/invalid entity or
            # keyboard field. The pool was already validated and persisted as
            # the draft preview, so never leave the host without a response.
            print(f"[tournament] setpool edit failed; falling back to a new review message: {exc!r}")
            sent = await app.send_message(
                int(tournament["creation_chat_id"]),
                summary_text,
                parse_mode="HTML",
                reply_markup=summary_markup,
            )
            await _set_prompt_id(tid, int(sent["message_id"]))
            try:
                await app.delete_message(int(tournament["creation_chat_id"]), prompt_id)
            except Exception:
                pass


@register("setgroup")
async def setgroup_command(message: dict):
    user_id = _user_id(message)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    arg = _arg_text(message)
    tournament = await get_active_draft(user_id)
    if not tournament:
        await app.send_message(chat_id, "⚠️ You do not have an active tournament creation in progress.")
        return

    tid = int(tournament["tournament_id"])
    if not arg:
        await update_tournament(tid, status="await_group")
        await _edit_prompt(
            tid,
            "<b>╭━━〔 🌐 HOST GROUP 〕━━╮</b>\n\n"
            "Do you want to set a public Telegram group to host this tournament?\n\n"
            "<blockquote>✅ Yes, I want → continue to the group-ID step.\n"
            "No, I don't want → create without a host group.\n"
            "❌ Cancel → stop tournament creation.</blockquote>\n\n"
            "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>",
            group_choice_keyboard(tid),
        )
        return

    try:
        group_id = int(arg.split()[0])
    except ValueError:
        await app.send_message(chat_id, "⚠️ Group ID must be a numeric Telegram group ID such as <code>-1001234567890</code>.", parse_mode="HTML")
        return

    info, error = await public_group_info(group_id)
    if error:
        await app.send_message(chat_id, f"⚠️ <b>Group validation failed</b>\n\n{esc(error)}", parse_mode="HTML")
        return

    await update_tournament(
        tid,
        status="confirm_group",
        host_group_id=int(info["id"]),
        host_group_name=info["title"],
        host_group_username=info["username"],
    )
    tournament = await get_tournament(tid)
    await _edit_prompt(tid, render_group_review(tournament), final_create_keyboard(tid, group_review=True))


def _running_tournament_keyboard(prefix: str, tournaments: list[dict], *, target_id: int, remove: bool, host_id: int) -> dict:
    rows = []
    for tournament in tournaments:
        tid = int(tournament["tournament_id"])
        name = str(tournament.get("tournament_name") or tournament.get("tournament_code") or "Tournament")
        rows.append([
            {
                "text": name[:60],
                "callback_data": f"tour_teamown_tourpick:{tid}:{int(target_id)}:{1 if remove else 0}:{int(host_id)}",
                "style": "primary",
            }
        ])
    rows.append([{"text": "❌ Cancel", "callback_data": f"tour_ui_cancel:{int(tournaments[0]['tournament_id'])}", "style": "danger"}])
    return {"inline_keyboard": rows}


async def _resolve_teamown_target(message: dict, user_id: int, target_tokens: list[str]):
    target = None
    from_argument = False
    if target_tokens:
        target = await find_user_by_identifier(target_tokens[0])
        from_argument = target is not None
    if target is None:
        reply = message.get("reply_to_message") or {}
        rid = int((reply.get("from") or {}).get("id") or 0)
        if rid:
            target = await fetchrow("SELECT * FROM users WHERE user_id=$1 LIMIT 1;", rid)
    return target, from_argument


@register("teamown")
async def teamown_command(message: dict):
    user_id = _user_id(message)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    args = _arg_text(message)
    tokens = args.split()
    remove = bool(tokens and tokens[-1].lower() == "remove")
    if remove:
        tokens = tokens[:-1]
    elif tokens and tokens[0].lower() == "remove":
        remove = True
        tokens = tokens[1:]

    target, target_from_argument = await _resolve_teamown_target(message, user_id, tokens)
    if target is None:
        await app.send_message(
            chat_id,
            "⚠️ Target user not found. Use <code>/teamown @username</code>, <code>/teamown USER_ID</code>, "
            "or reply to the user's message with <code>/teamown</code>.",
            parse_mode="HTML",
        )
        return

    target_id = int(target["user_id"])
    if target_id == user_id:
        await app.send_message(chat_id, "⚠️ The host cannot assign or remove their own tournament franchise through this command.")
        return

    # The first token identifies the user. Any remaining tokens identify the
    # tournament, e.g. IPL, T20WC, or the exact custom tournament name.
    if target_from_argument:
        keyword = " ".join(tokens[1:]).strip() if tokens else ""
    else:
        keyword = " ".join(tokens).strip()
    running = await get_owned_running_tournaments(user_id)
    running = [r for r in running if str(r.get("status") or "") == "created"]

    if not running:
        await app.send_message(chat_id, "⚠️ You do not currently own a running tournament that accepts team ownership changes.")
        return

    tournament = None
    if keyword:
        tournament = await find_owned_running_tournament(user_id, keyword)
        if tournament and str(tournament.get("status") or "") != "created":
            tournament = None
        if tournament is None:
            await app.send_message(
                chat_id,
                f"⚠️ No running tournament owned by you matches <code>{esc(keyword)}</code>.",
                parse_mode="HTML",
            )
            return
    elif len(running) == 1:
        tournament = running[0]
    else:
        await app.send_message(
            chat_id,
            "<b>╭━━〔 🏟️ SELECT TOURNAMENT 〕━━╮</b>\n\n"
            f"👤 <b>User</b> : {esc(target.get('first_name') or target.get('username') or target_id)}\n"
            f"🆔 <b>ID</b>   : <code>{target_id}</code>\n\n"
            "You own more than one running tournament. Choose the tournament for this team-ownership action.",
            parse_mode="HTML",
            reply_markup=_running_tournament_keyboard(
                "teamown", running, target_id=target_id, remove=remove, host_id=user_id
            ),
        )
        return

    await _teamown_present_action(chat_id, user_id, target, tournament, remove=remove)


async def _teamown_present_action(chat_id: int, host_id: int, target: dict, tournament: dict, *, remove: bool):
    tid = int(tournament["tournament_id"])
    target_id = int(target["user_id"])
    target_name = esc(target.get("first_name") or target.get("username") or target_id)
    tournament_name = esc(tournament.get("tournament_name") or tournament.get("tournament_code") or "Tournament")

    if remove:
        owned = await get_user_registration(tid, target_id)
        if not owned:
            await app.send_message(
                chat_id,
                f"⚠️ {target_name} does not currently own a team in <b>{tournament_name}</b>.",
                parse_mode="HTML",
            )
            return
        team_name = esc(IPL_TEAM_MAP.get(str(owned["team_code"]), str(owned["team_code"])))
        text = (
            "<b>╭━━〔 🗑️ REMOVE TEAM OWNER 〕━━╮</b>\n\n"
            f"👤 <b>User</b>      : {target_name}\n"
            f"🆔 <b>ID</b>        : <code>{target_id}</code>\n"
            f"🏆 <b>Tournament</b>: {tournament_name}\n"
            f"🏷️ <b>Team</b>      : {team_name}\n\n"
            f"<b>Are you sure you want to remove this user from {tournament_name} for {team_name}?</b>\n\n"
            "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
        )
        await app.send_message(
            chat_id, text, parse_mode="HTML",
            reply_markup=remove_confirm_keyboard(tid, target_id, str(owned["team_code"])),
        )
        return

    text = (
        "<b>╭━━〔 👑 TEAM OWNERSHIP 〕━━╮</b>\n\n"
        f"👤 <b>User</b>      : {target_name}\n"
        f"🆔 <b>ID</b>        : <code>{target_id}</code>\n"
        f"🏆 <b>Tournament</b>: {tournament_name}\n\n"
        "<b>Which team do you want to assign to this user for this tournament?</b>\n\n"
        "Choose a franchise below. The next screen will ask for confirmation.\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )
    await app.send_message(
        chat_id, text, parse_mode="HTML",
        reply_markup=teamown_keyboard(tid, target_id),
    )


@register("endtour")
async def endtour_command(message: dict):
    user_id = _user_id(message)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    running = await get_owned_running_tournaments(user_id)
    if not running:
        await app.send_message(
            chat_id,
            "<b>⚠️ No tournament you have running right now.</b>\n\nCreate one with <code>/createtour</code>.",
            parse_mode="HTML",
        )
        return

    if len(running) == 1:
        tournament = running[0]
        await _show_endtour_confirmation(chat_id, tournament, user_id)
        return

    rows = []
    for tournament in running:
        rows.append([{
            "text": str(tournament.get("tournament_name") or tournament.get("tournament_code") or "Tournament")[:60],
            "callback_data": f"tour_end_pick:{int(tournament['tournament_id'])}:{int(user_id)}",
            "style": "primary",
        }])
    rows.append([{"text": "❌ Cancel", "callback_data": f"tour_end_cancel:{int(user_id)}", "style": "danger"}])
    await app.send_message(
        chat_id,
        "<b>╭━━〔 🛑 END TOURNAMENT 〕━━╮</b>\n\n"
        "You currently own more than one running tournament. Which tournament do you want to end completely?",
        parse_mode="HTML",
        reply_markup={"inline_keyboard": rows},
    )


async def _show_endtour_confirmation(chat_id: int, tournament: dict, host_id: int, *, message_id: int | None = None):
    tid = int(tournament["tournament_id"])
    name = esc(tournament.get("tournament_name") or tournament.get("tournament_code") or "Tournament")
    text = (
        "<b>╭━━〔 ⚠️ END TOURNAMENT 〕━━╮</b>\n\n"
        f"🏆 <b>Tournament</b> : {name}\n"
        f"👤 <b>Hosted by</b>  : {esc(tournament.get('creator_username') and '@' + str(tournament.get('creator_username')).lstrip('@') or tournament.get('creator_name') or host_id)}\n"
        f"🎯 <b>Type</b>       : {esc(tournament.get('tournament_code') or 'IPL')} • {'Auction' if tournament.get('auction_mode') else 'No Auction'}\n"
        f"🪙 <b>Prize</b>      : {int(tournament.get('prize_coins') or 0):,} Coins • {int(tournament.get('prize_rubies') or 0):,} Rubies\n"
        f"📦 <b>Auction</b>    : {int(tournament.get('pool_count') or 0)} pools • {int(tournament.get('player_count') or 0)} players\n\n"
        "<blockquote>Once confirmed, this tournament and its current state will be ended and removed from the active tournament database. "
        "A verified backup file will be sent to you. You can restore it later by replying to that backup file with <code>/restore</code>.</blockquote>\n\n"
        "<b>Are you sure you want to end this tournament completely?</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )
    markup = {
        "inline_keyboard": [
            [{"text": "✅ Yes, End", "callback_data": f"tour_end_confirm:{tid}:{int(host_id)}", "style": "success"}],
            [{"text": "❌ Cancel", "callback_data": f"tour_end_cancel:{int(host_id)}", "style": "danger"}],
        ]
    }
    if message_id:
        await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=markup)
    else:
        await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


async def _send_endtour_backup(chat_id: int, backup: dict):
    tournament = backup["tournament"]
    name = esc(tournament.get("tournament_name") or tournament.get("tournament_code") or "Tournament")
    teams = backup.get("teams") or []
    pools = backup.get("pools") or []
    caption = (
        "<b>✅ TOURNAMENT ENDED COMPLETELY</b>\n\n"
        f"🏆 <b>Tournament</b> : {name}\n"
        f"👤 <b>Hosted by</b>  : {esc(tournament.get('creator_username') and '@' + str(tournament.get('creator_username')).lstrip('@') or tournament.get('creator_name') or tournament.get('creator_id'))}\n"
        f"👥 <b>Total Teams</b> : {len(teams)}\n"
        f"🪙 <b>Prize</b>       : {int(tournament.get('prize_coins') or 0):,} Coins • {int(tournament.get('prize_rubies') or 0):,} Rubies\n"
        f"📦 <b>Auction Pools</b>: {len(pools)}\n\n"
        "<blockquote>🔐 <b>Verified backup attached.</b>\nReply to this file with <code>/restore</code> to restore the tournament.</blockquote>"
    )
    try:
        await app.send_document(
            chat_id,
            backup["raw"],
            filename=str(backup["filename"]),
            caption=caption,
            parse_mode="HTML",
        )
        return
    except Exception as exc:
        print(f"[tournament] backup document-with-caption send failed: {exc!r}")
        await app.send_message(chat_id, caption, parse_mode="HTML")
        try:
            await app.send_document(chat_id, backup["raw"], filename=str(backup["filename"]))
        except Exception as second:
            await app.send_message(chat_id, f"⚠️ Backup file could not be sent automatically: <code>{esc(second)}</code>", parse_mode="HTML")


@register("restore")
async def restore_command(message: dict):
    user_id = _user_id(message)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    reply = message.get("reply_to_message") or {}
    document = reply.get("document") or {}
    if not document.get("file_id"):
        await app.send_message(chat_id, "⚠️ Reply to the tournament backup <code>.json</code> file with <code>/restore</code>.", parse_mode="HTML")
        return
    try:
        raw = await app.download_media(str(document["file_id"]))
        payload = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        await app.send_message(chat_id, f"⚠️ I could not read that tournament backup file: <code>{esc(exc)}</code>", parse_mode="HTML")
        return

    backup = await lookup_tournament_backup(payload, user_id)
    if not backup:
        await app.send_message(
            chat_id,
            "<b>❌ This is not your tournament backup.</b>\n\nThe file may belong to another host, may have been modified, or may no longer be a known backup.",
            parse_mode="HTML",
        )
        return

    data = backup.get("payload") or {}
    tournament = data.get("tournament") or {}
    teams = data.get("teams") or []
    pools = data.get("pools") or []
    players = data.get("pool_players") or []
    restore_id = int(backup["backup_id"])
    text = (
        "<b>╭━━〔 ♻️ TOURNAMENT RESTORE FOUND 〕━━╮</b>\n\n"
        f"🏆 <b>Name</b>       : {esc(tournament.get('tournament_name') or 'Tournament')}\n"
        f"🎯 <b>Type</b>       : {esc(tournament.get('tournament_code') or 'IPL')} • {'Auction' if tournament.get('auction_mode') else 'No Auction'}\n"
        f"👥 <b>Teams</b>      : {len(teams)}\n"
        f"👤 <b>Participants</b>: {sum(1 for t in teams if t.get('owner_user_id'))}\n"
        f"🎴 <b>Auction Players</b>: {len(players)}\n"
        f"📦 <b>Pools</b>      : {len(pools)}\n"
        f"🪙 <b>Prize</b>      : {int(tournament.get('prize_coins') or 0):,} Coins • {int(tournament.get('prize_rubies') or 0):,} Rubies\n\n"
        "<blockquote>The restore will recreate the tournament from this verified backup at the same saved stage/state.</blockquote>\n\n"
        "<b>Do you want to restore it?</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )
    markup = {
        "inline_keyboard": [
            [{"text": "✅ Yes, restore it", "callback_data": f"tour_restore_confirm:{restore_id}:{user_id}", "style": "success"}],
            [{"text": "❌ Cancel", "callback_data": f"tour_restore_cancel:{user_id}", "style": "danger"}],
        ]
    }
    await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


@register("prize")
async def prize_command(message: dict):
    user_id = _user_id(message)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    tournament = await get_tournament_for_prize(chat_id, user_id)
    if not tournament:
        await app.send_message(chat_id, "⚠️ No created IPL auction tournament was found here.")
        return
    players = await fetch_all_pool_players(int(tournament["tournament_id"]))
    await app.send_message(chat_id, prize_command_text(tournament, players), parse_mode="HTML")


async def handle_non_command_message(message: dict) -> bool:
    """Called by main.py only for non-command messages.

    Returns True when the message was consumed by the auction creation flow.
    """
    try:
        return await handle_tournament_reply(message)
    except Exception as exc:
        print(f"[tournament] non-command reply handling failed: {exc!r}")
        return False


@register_callback("tour_type")
async def on_tour_type(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    chat = callback_query.get("message") or {}
    chat_id = int((chat.get("chat") or {}).get("id") or 0)
    parts = str(callback_query.get("data") or "").split(":")
    choice = parts[1] if len(parts) > 1 else ""
    owner_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    if uid != owner_id:
        await app.answer_callback_query(callback_query["id"], "This tournament setup belongs to another user.", show_alert=True)
        return

    if choice != "IPL":
        await app.answer_callback_query(callback_query["id"], "That tournament engine is reserved for the next module.", show_alert=True)
        return

    tournament = await create_draft(
        uid,
        (callback_query.get("from") or {}).get("username"),
        (callback_query.get("from") or {}).get("first_name"),
        chat_id,
    )
    tid = int(tournament["tournament_id"])
    await create_ipl_teams(tid, IPL_TEAM_ORDER)
    await update_tournament(tid, status="select_mode", prompt_message_id=int(chat.get("message_id") or 0), overview_message_id=int(chat.get("message_id") or 0))

    await app.edit_message_text(
        chat_id,
        int(chat.get("message_id") or 0),
        "<b>╭━━〔 🏏 INDIAN PREMIER LEAGUE 〕━━╮</b>\n\n"
        "Choose how this IPL tournament should be created.\n\n"
        "<blockquote>"
        "🔨 <b>Auction Tour</b> → teams are built through the auction stage.\n"
        "🏏 <b>Without Auction Tour</b> → reserved for the non-auction tournament flow."
        "</blockquote>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>",
        parse_mode="HTML",
        reply_markup=mode_keyboard(tid),
    )
    await app.answer_callback_query(callback_query["id"], "IPL selected.")


@register_callback("tour_mode")
async def on_tour_mode(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) < 3:
        return
    tid = int(parts[1])
    mode = parts[2]
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "This tournament setup belongs to another user.", show_alert=True)
        return

    if mode != "auction":
        await app.answer_callback_query(callback_query["id"], "Without-auction tournaments are reserved for the next module.", show_alert=True)
        return

    await update_tournament(tid, status="await_prize", auction_mode=True)
    await _edit_prompt(tid, render_prize_prompt(), cancel_keyboard(tid))
    await app.answer_callback_query(callback_query["id"], "Auction mode selected.")


@register_callback("tour_new_cancel")
async def on_tour_new_cancel(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    parts = str(callback_query.get("data") or "").split(":")
    owner_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    if uid != owner_id:
        await app.answer_callback_query(callback_query["id"], "This tournament setup belongs to another user.", show_alert=True)
        return
    msg = callback_query.get("message") or {}
    await app.edit_message_text(
        int((msg.get("chat") or {}).get("id") or 0),
        int(msg.get("message_id") or 0),
        "<b>❌ TOURNAMENT CREATION CANCELLED</b>\n\nNo tournament setup was started.",
        parse_mode="HTML",
        reply_markup=NO_KEYBOARD,
    )
    await app.answer_callback_query(callback_query["id"], "Cancelled.")


@register_callback("tour_ui_cancel")
async def on_tour_ui_cancel(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tid = int(str(callback_query.get("data") or "").split(":")[-1])
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "Only the tournament host can close this prompt.", show_alert=True)
        return
    msg = callback_query.get("message") or {}
    await app.edit_message_text(
        int((msg.get("chat") or {}).get("id") or 0),
        int(msg.get("message_id") or 0),
        "<b>❌ ACTION CANCELLED</b>\n\nThe tournament itself is unchanged.",
        parse_mode="HTML",
        reply_markup=NO_KEYBOARD,
    )
    await app.answer_callback_query(callback_query["id"], "Action cancelled.")


@register_callback("tour_cancel")
async def on_tour_cancel(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) < 2:
        return
    tid = int(parts[1])
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "Only the tournament creator can cancel this setup.", show_alert=True)
        return
    await _send_cancelled(tid)
    await app.answer_callback_query(callback_query["id"], "Tournament creation cancelled.")


@register_callback("tour_prize_confirm")
async def on_prize_confirm(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tid = int(str(callback_query.get("data") or "").split(":")[-1])
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "This setup belongs to another user.", show_alert=True)
        return

    player = tournament.get("prize_player")
    if isinstance(player, str):
        player = json.loads(player)
    if player:
        current, error = await resolve_owned_prize_player(uid, str(player.get("name") or ""))
        if error or not current or int(current.get("player_id") or 0) != int(player.get("player_id") or 0):
            await app.answer_callback_query(callback_query["id"], "The prize player changed or is no longer owned. Please change the prize.", show_alert=True)
            await _edit_prompt(tid, render_prize_prompt("The optional prize player is no longer available in your squad."), cancel_keyboard(tid))
            await update_tournament(tid, status="await_prize")
            return

    await update_tournament(tid, status="overview")
    tournament = await get_tournament(tid)
    await _edit_prompt(
        tid,
        render_tournament_overview(tournament),
        cancel_keyboard(tid),
    )
    await app.answer_callback_query(callback_query["id"], "Prize confirmed.")


@register_callback("tour_prize_change")
async def on_prize_change(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tid = int(str(callback_query.get("data") or "").split(":")[-1])
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "This setup belongs to another user.", show_alert=True)
        return
    await update_tournament(tid, status="await_prize")
    await _edit_prompt(tid, render_prize_prompt(), cancel_keyboard(tid))
    await app.answer_callback_query(callback_query["id"], "Enter the new prize details.")


@register_callback("tour_pool_confirm")
async def on_pool_confirm(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tid = int(str(callback_query.get("data") or "").split(":")[-1])
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "This setup belongs to another user.", show_alert=True)
        return

    preview = tournament.get("pool_preview") or []
    if isinstance(preview, str):
        preview = json.loads(preview)
    if not preview:
        await app.answer_callback_query(callback_query["id"], "There are no valid auction players to save.", show_alert=True)
        return

    pool_count, player_count = await create_pools(tid, preview)
    await update_tournament(
        tid,
        status="ask_group",
        pool_preview=None,
        pool_errors=None,
        pool_count=pool_count,
        player_count=player_count,
    )
    await _edit_prompt(
        tid,
        "<b>╭━━〔 🌐 HOST GROUP 〕━━╮</b>\n\n"
        "Do you want to set a public Telegram group to host this tournament?\n\n"
        "<blockquote>✅ <b>Yes, I want</b> → configure a public group.\n"
        "No, I don't want → continue without a group.\n"
        "❌ <b>Cancel</b> → stop tournament creation.</blockquote>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>",
        group_choice_keyboard(tid),
    )
    await app.answer_callback_query(callback_query["id"], "Auction pool saved.")


@register_callback("tour_pool_change")
async def on_pool_change(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tid = int(str(callback_query.get("data") or "").split(":")[-1])
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "This setup belongs to another user.", show_alert=True)
        return
    await update_tournament(tid, status="await_pool")
    await _edit_prompt(tid, render_pool_prompt(), cancel_keyboard(tid))
    await app.answer_callback_query(callback_query["id"], "Resend the auction pool.")


@register_callback("tour_group_yes")
async def on_group_yes(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tid = int(str(callback_query.get("data") or "").split(":")[-1])
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "This setup belongs to another user.", show_alert=True)
        return
    await update_tournament(tid, status="await_group")
    await _edit_prompt(
        tid,
        "<b>╭━━〔 🌐 SET HOST GROUP 〕━━╮</b>\n\n"
        "Now send the group ID with:\n"
        "<code>/setgroup -1001234567890</code>\n\n"
        "<blockquote>Only public groups are accepted. The bot must already be in the group with the required admin permissions.</blockquote>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>",
        cancel_keyboard(tid),
    )
    await app.answer_callback_query(callback_query["id"], "Send the group ID with /setgroup.")


@register_callback("tour_group_no")
async def on_group_no(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tid = int(str(callback_query.get("data") or "").split(":")[-1])
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "This setup belongs to another user.", show_alert=True)
        return
    await update_tournament(tid, status="final_confirm")
    tournament = await get_tournament(tid)
    await _edit_prompt(tid, render_final_review(tournament), final_create_keyboard(tid))
    await app.answer_callback_query(callback_query["id"], "No host group selected.")


@register_callback("tour_group_confirm")
async def on_group_confirm(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tid = int(str(callback_query.get("data") or "").split(":")[-1])
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "This setup belongs to another user.", show_alert=True)
        return
    await update_tournament(tid, status="final_confirm")
    tournament = await get_tournament(tid)
    await _edit_prompt(tid, render_final_review(tournament), final_create_keyboard(tid))
    await app.answer_callback_query(callback_query["id"], "Host group confirmed.")


@register_callback("tour_group_change")
async def on_group_change(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tid = int(str(callback_query.get("data") or "").split(":")[-1])
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "This setup belongs to another user.", show_alert=True)
        return
    await update_tournament(tid, status="await_group", host_group_id=None, host_group_name=None, host_group_username=None)
    await _edit_prompt(
        tid,
        "<b>╭━━〔 🌐 SET HOST GROUP 〕━━╮</b>\n\n"
        "Resend the public group with:\n"
        "<code>/setgroup -1001234567890</code>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>",
        cancel_keyboard(tid),
    )
    await app.answer_callback_query(callback_query["id"], "Change the group.")


@register_callback("tour_create")
async def on_tour_create(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tid = int(str(callback_query.get("data") or "").split(":")[-1])
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "This setup belongs to another user.", show_alert=True)
        return

    # Verify the creator still owns the optional prize player before showing
    # the 3-second creation status.
    player = tournament.get("prize_player")
    if isinstance(player, str):
        player = json.loads(player)
    if player:
        _, error = await resolve_owned_prize_player(uid, str(player.get("name") or ""))
        if error:
            await app.answer_callback_query(callback_query["id"], "The prize player is no longer available. Please change the prize.", show_alert=True)
            await update_tournament(tid, status="await_prize")
            await _edit_prompt(tid, render_prize_prompt(error), cancel_keyboard(tid))
            return

    await app.answer_callback_query(callback_query["id"], "Creating tournament...")
    source_chat = int(tournament["creation_chat_id"])
    source_message = int(tournament.get("prompt_message_id") or tournament.get("overview_message_id") or 0)
    if source_message:
        try:
            await app.delete_message(source_chat, source_message)
        except Exception:
            pass

    progress = await app.send_message(
        source_chat,
        "<b>Creating your IPL tournament...</b>",
        parse_mode="HTML",
    )
    await asyncio.sleep(3)
    try:
        await app.delete_message(source_chat, int(progress.get("message_id") or 0))
    except Exception:
        pass

    ok, error = await create_tournament_final(tid)
    tournament = await get_tournament(tid)
    if (not ok) and str(tournament.get("status") or "") != "created":
        # The only pre-creation failure currently possible after the final
        # check is a race where the optional prize player disappeared. Restore
        # a live setup prompt instead of leaving the creator with a dead draft.
        await update_tournament(tid, status="await_prize")
        sent = await app.send_message(
            source_chat,
            render_prize_prompt(error or "The prize setup needs to be entered again."),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(tid),
        )
        await _set_prompt_id(tid, int(sent["message_id"]))
        await app.answer_callback_query(callback_query["id"], "The tournament was not created. Please update the prize.", show_alert=True)
        return

    tournament = await get_tournament(tid)
    group_ok = bool(tournament.get("host_group_id")) and ok
    success_text = render_creation_success(tournament, group_ok, error)
    await app.send_message(source_chat, success_text, parse_mode="HTML")
    await app.answer_callback_query(callback_query["id"], "Tournament creation finished.")


@register_callback("tour_selfreg")
async def on_self_registration(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tid = int(str(callback_query.get("data") or "").split(":")[-1])
    tournament = await get_tournament(tid)
    if not tournament or not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "Only the tournament host can enable self-registration.", show_alert=True)
        return
    await update_tournament(tid, self_registration_enabled=True)
    await update_group_board(tid)
    await app.answer_callback_query(callback_query["id"], "Self-registration enabled.")


@register_callback("tour_team")
async def on_team_register(callback_query: dict):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 3:
        return
    tid = int(parts[1])
    team_code = parts[2].upper()
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    user = callback_query.get("from") or {}
    tournament = await get_tournament(tid)
    if not tournament or tournament["status"] != "created":
        await app.answer_callback_query(callback_query["id"], "This tournament is not accepting registrations.", show_alert=True)
        return
    if int(tournament.get("host_group_id") or 0) != int((callback_query.get("message") or {}).get("chat", {}).get("id") or 0):
        await app.answer_callback_query(callback_query["id"], "This registration board is not valid here.", show_alert=True)
        return
    if not tournament["self_registration_enabled"]:
        await app.answer_callback_query(callback_query["id"], "Self-registration is not enabled by the tournament host yet.", show_alert=True)
        return
    if team_code not in IPL_TEAM_ORDER:
        await app.answer_callback_query(callback_query["id"], "Unknown franchise.", show_alert=True)
        return

    team = await get_team(tid, team_code)
    if not team or team["owner_user_id"]:
        await app.answer_callback_query(callback_query["id"], "That franchise is already taken.", show_alert=True)
        return

    existing = await get_user_registration(tid, uid)
    if existing:
        await app.answer_callback_query(callback_query["id"], f"You already own {existing['team_code']} in this tournament.", show_alert=True)
        return

    # The Telegram API can only DM users who have started the bot. If the DM
    # is unavailable, do not reserve the franchise.
    dm_text = (
        "<b>╭━━〔 ✅ TOURNAMENT REGISTRATION 〕━━╮</b>\n\n"
        f"🎉 Congratulations, <b>{esc(user.get('first_name') or user.get('username') or uid)}</b>.\n\n"
        f"🏏 <b>Tournament</b>: {esc(tournament.get('tournament_name'))}\n"
        f"🏷️ <b>Your Team</b>   : {esc(IPL_TEAM_MAP.get(team_code, team_code))}\n"
        f"👤 <b>Hosted by</b>   : {esc(tournament.get('creator_username') or tournament.get('creator_name') or 'Tournament Host')}\n"
        f"🌐 <b>Host Group</b>  : {esc(tournament.get('host_group_name'))}\n\n"
        "<blockquote>Confirm your franchise registration. Cancelling here leaves the team available for someone else.</blockquote>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )
    markup = {
        "inline_keyboard": [[
            {"text": "✅ Yes, confirm", "callback_data": f"tour_register_confirm:{tid}:{team_code}", "style": "success"},
            {"text": "❌ Cancel registration", "callback_data": f"tour_register_cancel:{tid}:{team_code}", "style": "danger"},
        ]]
    }
    try:
        await app.send_message(uid, dm_text, parse_mode="HTML", reply_markup=markup)
        await app.answer_callback_query(callback_query["id"], "Check your private chat with the bot.")
    except Exception as exc:
        await app.answer_callback_query(
            callback_query["id"],
            "Please open the bot and press /start first. I could not open the private registration message.",
            show_alert=True,
        )
        print(f"[tournament] registration DM failed for user={uid}: {exc!r}")


@register_callback("tour_register_confirm")
async def on_register_confirm(callback_query: dict):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 3:
        return
    tid = int(parts[1])
    team_code = parts[2].upper()
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tournament = await get_tournament(tid)
    if not tournament:
        await app.edit_message_text(int(uid), int(callback_query.get("message", {}).get("message_id") or 0), "⚠️ Tournament no longer exists.")
        return

    success, reason = await assign_team(
        tid,
        team_code,
        uid,
        (callback_query.get("from") or {}).get("username"),
        (callback_query.get("from") or {}).get("first_name"),
        "self",
    )
    if not success:
        await app.answer_callback_query(callback_query["id"], reason, show_alert=True)
        return

    await update_group_board(tid)
    await app.edit_message_text(
        uid,
        int(callback_query.get("message", {}).get("message_id") or 0),
        "<b>✅ TOURNAMENT REGISTRATION CONFIRMED</b>\n\n"
        f"You are now the franchise owner of <b>{esc(IPL_TEAM_MAP.get(team_code, team_code))}</b>.\n\n"
        "Your host group board has been updated.",
        parse_mode="HTML",
        reply_markup=NO_KEYBOARD,
    )
    await app.answer_callback_query(callback_query["id"], "Registration confirmed.")


@register_callback("tour_register_cancel")
async def on_register_cancel(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    mid = int(callback_query.get("message", {}).get("message_id") or 0)
    try:
        await app.edit_message_text(
            uid,
            mid,
            "<b>❌ REGISTRATION CANCELLED</b>\n\nNo franchise was reserved for you.",
            parse_mode="HTML",
            reply_markup=NO_KEYBOARD,
        )
    except Exception:
        pass
    await app.answer_callback_query(callback_query["id"], "Registration cancelled.")



async def _restore_stage_to_host(chat_id: int, tournament: dict):
    tid = int(tournament["tournament_id"])
    status = str(tournament.get("status") or "select_mode")
    sent = None

    if status == "select_mode":
        text = (
            "<b>╭━━〔 ♻️ RESTORED TOURNAMENT SETUP 〕━━╮</b>\n\n"
            "Your tournament creation session has been restored. Choose the game instance to continue."
        )
        markup = create_type_keyboard(int(tournament["creator_id"]))
    elif status == "await_prize":
        text = render_prize_prompt()
        markup = cancel_keyboard(tid)
    elif status == "confirm_prize":
        player = tournament.get("prize_player")
        if isinstance(player, str):
            player = json.loads(player)
        prize = {
            "coins": int(tournament.get("prize_coins") or 0),
            "rubies": int(tournament.get("prize_rubies") or 0),
            "player": player,
        }
        text = render_prize_confirmation(prize)
        markup = confirm_prize_keyboard(tid)
    elif status in {"overview", "await_pool"}:
        text = render_tournament_overview(tournament, show_setpool=False)
        text += "\n\n<blockquote>📥 <b>Next step</b>\nUse <code>/setpool IPL</code> and reply with the auction pool text or a .txt file.</blockquote>"
        markup = cancel_keyboard(tid)
    elif status == "confirm_pool":
        preview = tournament.get("pool_preview") or []
        if isinstance(preview, str):
            preview = json.loads(preview)
        lines = [
            "<b>╭━━〔 📦 RESTORED AUCTION POOL REVIEW 〕━━╮</b>", "",
            f"✅ <b>Valid pools</b>  : {len(preview)}",
            f"✅ <b>Valid players</b>: {int(tournament.get('player_count') or 0)}",
            "",
        ]
        for pool in preview:
            lines.append(f"<b>[Pool {int(pool['pool_no'])}: {esc(pool['pool_name'])}]</b> • Base {int(pool['base_price']):,}")
            lines.extend(_pool_player_preview_line(player) for player in pool.get("players", []))
            lines.append("")
        lines += ["<b>Are you sure you want to set these players for the IPL auction?</b>", "", "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"]
        text = "\n".join(lines)
        markup = pool_confirmation_keyboard(tid)
    elif status in {"ask_group", "await_group"}:
        text = (
            "<b>╭━━〔 🌐 HOST GROUP 〕━━╮</b>\n\n"
            "Do you want to set a public Telegram group to host this tournament?\n\n"
            "<blockquote>✅ <b>Yes, I want</b> → configure a public group.\n"
            "No, I don't want → continue without a host group.\n"
            "❌ <b>Cancel</b> → stop tournament setup.</blockquote>\n\n"
            "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
        )
        markup = group_choice_keyboard(tid)
    elif status == "confirm_group":
        text = render_group_review(tournament)
        markup = final_create_keyboard(tid, group_review=True)
    elif status == "final_confirm":
        text = render_final_review(tournament)
        markup = final_create_keyboard(tid)
    elif status == "created":
        ok, error = await create_registration_announcement(tid)
        if not ok and error:
            await app.send_message(chat_id, f"⚠️ Tournament restored, but the group board could not be recreated: <code>{esc(error)}</code>", parse_mode="HTML")
        else:
            await app.send_message(chat_id, "✅ <b>Tournament restored successfully.</b> The tournament continues from its saved state.", parse_mode="HTML")
        await sync_tournament_session(tid)
        return
    else:
        text = render_tournament_overview(tournament)
        markup = cancel_keyboard(tid)

    sent = await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
    await update_tournament(tid, prompt_message_id=int(sent["message_id"]), overview_message_id=int(sent["message_id"]))
    await sync_tournament_session(tid)


@register_callback("tour_teamown_tourpick")
async def on_teamown_tournament_pick(callback_query: dict):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 5:
        return
    tid, target_id, remove_flag, host_id = int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    if uid != host_id:
        await app.answer_callback_query(callback_query["id"], "Only the tournament host can use this menu.", show_alert=True)
        return
    tournament = await get_tournament(tid)
    if not tournament or not _tournament_owner_ok(tournament, uid) or str(tournament.get("status") or "") != "created":
        await app.answer_callback_query(callback_query["id"], "That tournament is no longer available.", show_alert=True)
        return
    target = await fetchrow("SELECT * FROM users WHERE user_id=$1 LIMIT 1;", target_id)
    if not target:
        await app.answer_callback_query(callback_query["id"], "Target user no longer exists.", show_alert=True)
        return
    chat = callback_query.get("message") or {}
    chat_id = int((chat.get("chat") or {}).get("id") or 0)
    await app.edit_message_text(
        chat_id,
        int(chat.get("message_id") or 0),
        "<b>╭━━〔 🏟️ TOURNAMENT SELECTED 〕━━╮</b>\n\n"
        f"🏆 <b>Tournament</b>: {esc(tournament.get('tournament_name') or tournament.get('tournament_code'))}\n"
        f"👤 <b>User</b>      : {esc(target.get('first_name') or target.get('username') or target_id)}\n"
        f"🆔 <b>ID</b>        : <code>{target_id}</code>\n\n"
        "Proceeding to the team ownership step...\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>",
        parse_mode="HTML",
        reply_markup=NO_KEYBOARD,
    )
    await _teamown_present_action(chat_id, uid, target, tournament, remove=bool(remove_flag))
    await app.answer_callback_query(callback_query["id"], "Tournament selected.")


@register_callback("tour_end_pick")
async def on_endtour_pick(callback_query: dict):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 3:
        return
    tid, host_id = int(parts[1]), int(parts[2])
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    if uid != host_id:
        await app.answer_callback_query(callback_query["id"], "Only the tournament host can use this menu.", show_alert=True)
        return
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid) or str(tournament.get("status") or "") not in RUNNING_TOURNAMENT_STATUSES:
        await app.answer_callback_query(callback_query["id"], "That tournament is no longer running.", show_alert=True)
        return
    msg = callback_query.get("message") or {}
    await _show_endtour_confirmation(
        int((msg.get("chat") or {}).get("id") or 0), tournament, uid,
        message_id=int(msg.get("message_id") or 0),
    )
    await app.answer_callback_query(callback_query["id"], "Tournament selected.")


@register_callback("tour_end_cancel")
async def on_endtour_cancel(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) < 2 or uid != int(parts[1]):
        await app.answer_callback_query(callback_query["id"], "This menu belongs to another host.", show_alert=True)
        return
    msg = callback_query.get("message") or {}
    try:
        await app.edit_message_text(
            int((msg.get("chat") or {}).get("id") or 0), int(msg.get("message_id") or 0),
            "<b>❌ Action cancelled.</b>", parse_mode="HTML", reply_markup=NO_KEYBOARD,
        )
    except Exception:
        pass
    await app.answer_callback_query(callback_query["id"], "Cancelled.")


@register_callback("tour_end_confirm")
async def on_endtour_confirm(callback_query: dict):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 3:
        return
    tid, host_id = int(parts[1]), int(parts[2])
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    if uid != host_id:
        await app.answer_callback_query(callback_query["id"], "Only the tournament host can end this tournament.", show_alert=True)
        return
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid) or str(tournament.get("status") or "") not in RUNNING_TOURNAMENT_STATUSES:
        await app.answer_callback_query(callback_query["id"], "That tournament is no longer running.", show_alert=True)
        return
    chat_id = int((callback_query.get("message") or {}).get("chat", {}).get("id") or 0)
    message_id = int((callback_query.get("message") or {}).get("message_id") or 0)
    # Retire the live group board before removing the tournament rows. The
    # historical board remains visible as an ended notice instead of a stale
    # registration menu.
    group_id = int(tournament.get("host_group_id") or 0)
    board_id = int(tournament.get("group_registration_message_id") or 0)
    if group_id and board_id:
        try:
            await app.edit_message_text(
                group_id, board_id,
                f"<b>⛔ TOURNAMENT ENDED</b>\n\n"
                f"🏆 <b>{esc(tournament.get('tournament_name') or tournament.get('tournament_code') or 'Tournament')}</b>\n"
                "This tournament has been ended by the host. Its verified backup can be restored by the host.",
                parse_mode="HTML", reply_markup=NO_KEYBOARD,
            )
        except Exception as exc:
            print(f"[tournament] could not retire group board: {exc!r}")
    try:
        backup = await create_tournament_backup(tid)
    except Exception as exc:
        print(f"[tournament] endtour backup failed: {exc!r}")
        await app.answer_callback_query(callback_query["id"], "Backup failed. The tournament was not ended.", show_alert=True)
        return
    try:
        if message_id:
            await app.delete_message(chat_id, message_id)
    except Exception:
        pass
    await _send_endtour_backup(chat_id, backup)
    await app.answer_callback_query(callback_query["id"], "Tournament ended and backup created.")


@register_callback("tour_restore_cancel")
async def on_restore_cancel(callback_query: dict):
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) < 2 or uid != int(parts[1]):
        await app.answer_callback_query(callback_query["id"], "This restore action belongs to another user.", show_alert=True)
        return
    msg = callback_query.get("message") or {}
    try:
        await app.edit_message_text(
            int((msg.get("chat") or {}).get("id") or 0), int(msg.get("message_id") or 0),
            "<b>❌ Restore cancelled.</b>", parse_mode="HTML", reply_markup=NO_KEYBOARD,
        )
    except Exception:
        pass
    await app.answer_callback_query(callback_query["id"], "Restore cancelled.")


@register_callback("tour_restore_confirm")
async def on_restore_confirm(callback_query: dict):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 3:
        return
    backup_id, host_id = int(parts[1]), int(parts[2])
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    if uid != host_id:
        await app.answer_callback_query(callback_query["id"], "This restore belongs to another user.", show_alert=True)
        return
    row = await fetchrow("SELECT * FROM auction_tournament_backups WHERE backup_id=$1 AND creator_id=$2 LIMIT 1;", backup_id, uid)
    if not row:
        await app.answer_callback_query(callback_query["id"], "That backup is not owned by you.", show_alert=True)
        return
    chat_id = int((callback_query.get("message") or {}).get("chat", {}).get("id") or 0)
    message_id = int((callback_query.get("message") or {}).get("message_id") or 0)
    try:
        tournament = await restore_tournament_backup(dict(row))
    except Exception as exc:
        print(f"[tournament] restore failed: {exc!r}")
        await app.answer_callback_query(callback_query["id"], "Restore failed. The backup was not changed.", show_alert=True)
        return
    try:
        if message_id:
            await app.delete_message(chat_id, message_id)
    except Exception:
        pass
    await _restore_stage_to_host(chat_id, tournament)
    await app.answer_callback_query(callback_query["id"], "Tournament restored.")


@register_callback("tour_teamown_pick")
async def on_teamown_pick(callback_query: dict):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 4:
        return
    tid, target_id, team_code = int(parts[1]), int(parts[2]), parts[3].upper()
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "Only the tournament host can assign teams.", show_alert=True)
        return
    team = await get_team(tid, team_code)
    if not team or team["owner_user_id"]:
        await app.answer_callback_query(callback_query["id"], "That team is already assigned.", show_alert=True)
        return
    target = await fetchrow("SELECT * FROM users WHERE user_id=$1 LIMIT 1;", target_id)
    if not target:
        await app.answer_callback_query(callback_query["id"], "That user is not registered with the bot.", show_alert=True)
        return
    existing = await get_user_registration(tid, target_id)
    if existing:
        await app.answer_callback_query(callback_query["id"], f"That user already owns {existing['team_code']}.", show_alert=True)
        return

    await app.edit_message_text(
        int((callback_query.get("message") or {}).get("chat", {}).get("id") or 0),
        int((callback_query.get("message") or {}).get("message_id") or 0),
        "<b>╭━━〔 👑 ASSIGN FRANCHISE 〕━━╮</b>\n\n"
        f"👤 <b>User</b> : {esc(target.get('first_name') or target.get('username') or target_id)}\n"
        f"🆔 <b>ID</b>   : <code>{target_id}</code>\n"
        f"🏷️ <b>Team</b> : {esc(IPL_TEAM_MAP.get(team_code, team_code))}\n\n"
        f"<b>Are you sure you want to assign this user to {esc(IPL_TEAM_MAP.get(team_code, team_code))}?</b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>",
        parse_mode="HTML",
        reply_markup=teamown_confirm_keyboard(tid, target_id, team_code),
    )
    await app.answer_callback_query(callback_query["id"], "Review the assignment.")


@register_callback("tour_teamown_confirm")
async def on_teamown_confirm(callback_query: dict):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 4:
        return
    tid, target_id, team_code = int(parts[1]), int(parts[2]), parts[3].upper()
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "Only the host can assign teams.", show_alert=True)
        return
    target = await fetchrow("SELECT * FROM users WHERE user_id=$1 LIMIT 1;", target_id)
    if not target:
        await app.answer_callback_query(callback_query["id"], "Target user no longer exists.", show_alert=True)
        return
    success, reason = await assign_team(
        tid,
        team_code,
        target_id,
        target.get("username"),
        target.get("first_name"),
        "manual",
    )
    if not success:
        await app.answer_callback_query(callback_query["id"], reason, show_alert=True)
        return
    await update_group_board(tid)
    mid = int((callback_query.get("message") or {}).get("message_id") or 0)
    cid = int((callback_query.get("message") or {}).get("chat", {}).get("id") or 0)
    if mid:
        await app.edit_message_text(
            cid,
            mid,
            f"<b>✅ TEAM ASSIGNED</b>\n\n{esc(target.get('first_name') or target.get('username') or target_id)} now owns <b>{esc(IPL_TEAM_MAP.get(team_code, team_code))}</b>.",
            parse_mode="HTML",
            reply_markup=NO_KEYBOARD,
        )
    await app.answer_callback_query(callback_query["id"], "Team assigned.")


@register_callback("tour_teamown_remove")
async def on_teamown_remove(callback_query: dict):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 4:
        return
    tid, target_id, team_code = int(parts[1]), int(parts[2]), parts[3].upper()
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    tournament = await get_tournament(tid)
    if not _tournament_owner_ok(tournament, uid):
        await app.answer_callback_query(callback_query["id"], "Only the host can remove teams.", show_alert=True)
        return
    success, reason = await remove_team_owner(tid, team_code, target_id)
    if not success:
        await app.answer_callback_query(callback_query["id"], reason, show_alert=True)
        return
    await update_group_board(tid)
    mid = int((callback_query.get("message") or {}).get("message_id") or 0)
    cid = int((callback_query.get("message") or {}).get("chat", {}).get("id") or 0)
    if mid:
        await app.edit_message_text(
            cid,
            mid,
            f"<b>✅ TEAM OWNER REMOVED</b>\n\n<b>{esc(IPL_TEAM_MAP.get(team_code, team_code))}</b> is available again.",
            parse_mode="HTML",
            reply_markup=NO_KEYBOARD,
        )
    await app.answer_callback_query(callback_query["id"], "Team owner removed.")
