from __future__ import annotations

import asyncio
import html
from handlers.registry import register_callback
from app import app
from buttons.playso_buttons import bowler_selection_keyboard, batter_selection_keyboard
from database.playso_repo import get_match, update_locked, set_message_id, set_basic
from engines.lineup_engine import load_current_xi
from handlers.playso.common import current_xi, bowling_family, player_line, mention_html, role_emoji, match_lock, callback_message_is_current, dedup_selection_click, bowling_family_label

NO_KEYBOARD = {"inline_keyboard": []}


def _users_for_innings(match: dict, innings_no: int):
    challenger = int(match["challenger_id"]); opponent = int(match["opponent_id"])
    state = match.get("state") or {}
    if innings_no == 1 and state.get("next_super_over_batting_user"):
        return int(state.get("next_super_over_batting_user")), int(state.get("next_super_over_bowling_user"))
    if innings_no == 1:
        toss_winner = int(match.get("toss_winner_id") or 0)
        decision = str(match.get("decision") or "").lower()
        if decision == "bat":
            return toss_winner, (opponent if toss_winner == challenger else challenger)
        return (opponent if toss_winner == challenger else challenger), toss_winner
    return int(state.get("second_batting_user") or state.get("first_bowling_user") or opponent), int(state.get("second_bowling_user") or state.get("first_batting_user") or challenger)


def _bowler_text(match: dict, batting_id: int, bowling_id: int, players: list[dict], selected: int | None = None, locked: int | None = None) -> str:
    bowling_mention = mention_html(bowling_id, match.get("challenger_username") if bowling_id == int(match["challenger_id"]) else match.get("opponent_username"), match.get("challenger_name") if bowling_id == int(match["challenger_id"]) else match.get("opponent_name"))
    lines = ["<b>╭━━〔 🎯 PLAYSO • BOWLER SELECT 〕━━╮</b>", "", f"👤 {bowling_mention}", "", "<b>Choose your one bowler for this Super Over.</b>", ""]
    if selected:
        p = next((x for x in players if int(x.get("player_id") or 0) == int(selected)), None)
        if p:
            lines += [f"<b>✅ Selected Bowler ➤ {html.escape(str(p.get('name')))}</b>", f"<b>🎳 Type ➤ {bowling_family_label(p)}</b>", ""]
    lines += ["<i>🔴 Select your bowler • ✅ Selected bowler</i>", "", "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"]
    return "\n".join(lines)


def _bat_text(match: dict, batting_id: int, bowling_id: int, players: list[dict], selected: list[int]) -> str:
    batting_mention = mention_html(batting_id, match.get("challenger_username") if batting_id == int(match["challenger_id"]) else match.get("opponent_username"), match.get("challenger_name") if batting_id == int(match["challenger_id"]) else match.get("opponent_name"))
    slot = {pid: i+1 for i,pid in enumerate(selected)}
    chosen = [next((p for p in players if int(p.get("player_id") or 0)==pid), None) for pid in selected]
    lines = ["<b>╭━━〔 🏏 PLAYSO • BATTERS 〕━━╮</b>", "", f"👤 {batting_mention}", "", "<b>Select your three batters:</b>", ""]
    for i, p in enumerate(chosen, start=1):
        if p:
            role = "STRIKER" if i==1 else "NON-STRIKER" if i==2 else "ONE-DOWN"
            lines.append(f"<b>{i}. {html.escape(str(p.get('name')))} • OVR {int(p.get('bat_level') or 0)} ➤ {role}</b>")
    if selected:
        lines.append("")
    lines += [f"<b>Selected ➤ {len(selected)}/3</b>", "", "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"]
    return "\n".join(lines)


async def start_setup(chat_id: int, match: dict, innings_no: int = 1):
    match = dict(match); batting_id, bowling_id = _users_for_innings(match, innings_no)
    batting_xi = await current_xi(batting_id); bowling_xi = await current_xi(bowling_id)
    state = match.get("state") or {}
    previous = state.get("previous_bowlers") or {}
    locked = previous.get(str(bowling_id)) if innings_no == 1 and state.get("is_repeat_super_over") else None
    state.update({"innings_no": innings_no, "batting_user": batting_id, "bowling_user": bowling_id,
                  "bowling_xi": bowling_xi, "batting_xi": batting_xi, "selected_bowler": None,
                  "selected_batters": [], "locked_bowler": locked, "stage": "bowler_select",
                  "this_over": [], "commentary": [], "partnership_runs": 0, "partnership_balls": 0,
                  "current_striker_id": None, "current_non_striker_id": None, "batter_stats": {}, "bowler_stats": {}})
    await set_basic(int(match["match_id"]), innings_no=innings_no)
    await from_update_state(int(match["match_id"]), state, "lineup")
    sent = await app.send_message(chat_id, _bowler_text(match, batting_id, bowling_id, bowling_xi, None, locked), parse_mode="HTML", reply_markup=bowler_selection_keyboard(int(match["match_id"]), bowling_xi, None, locked))
    await set_message_id(int(match["match_id"]), int(sent["message_id"]))

async def from_update_state(match_id: int, state: dict, status: str):
    from database.playso_repo import set_state
    await set_state(match_id, state, status=status)


@register_callback("playso_bowler")
async def playso_bowler(callback_query):
    _, mid, pid = callback_query["data"].split(":"); mid=int(mid); pid=int(pid); uid=int(callback_query["from"]["id"]); msg=callback_query["message"]
    async with match_lock(mid):
        match = await get_match(mid)
        if not match or match["status"] != "lineup":
            await app.answer_callback_query(callback_query["id"], "This selection is no longer active.", show_alert=True); return
        if not callback_message_is_current(dict(match), callback_query):
            await app.answer_callback_query(callback_query["id"], "This action is no longer active.", show_alert=True); return
        state = match.get("state") or {}
        if uid != int(state.get("bowling_user") or 0):
            await app.answer_callback_query(callback_query["id"], "This action is not for you.", show_alert=True); return
        if not dedup_selection_click(mid, "bowler", uid, pid):
            await app.answer_callback_query(callback_query["id"], "Selection already received.", show_alert=False); return
        xi = state.get("bowling_xi") or []
        player = next((p for p in xi if int(p.get("player_id") or 0)==pid), None)
        if not player:
            await app.answer_callback_query(callback_query["id"], "That player is not in the current Playing XI.", show_alert=True); return
        if state.get("locked_bowler") is not None and int(state["locked_bowler"]) == pid:
            await app.answer_callback_query(callback_query["id"], "This bowler cannot bowl this Super Over.", show_alert=True); return
        if str(player.get("role") or "") not in {"Bowler", "AllRounder"}:
            await app.answer_callback_query(callback_query["id"], "This player is not an eligible bowler.", show_alert=True); return
        state["selected_bowler"] = None if state.get("selected_bowler") == pid else pid
        await from_update_state(mid, state, "lineup")
        await app.answer_callback_query(callback_query["id"], "Bowler selected." if state["selected_bowler"] else "Bowler unselected.")
        await app.edit_message_text(msg["chat"]["id"], msg["message_id"], _bowler_text(match, int(state["batting_user"]), uid, xi, state.get("selected_bowler"), state.get("locked_bowler")), parse_mode="HTML", reply_markup=bowler_selection_keyboard(mid, xi, state.get("selected_bowler"), state.get("locked_bowler")))


@register_callback("playso_bowler_confirm")
async def playso_bowler_confirm(callback_query):
    mid=int(callback_query["data"].split(":")[1]); uid=int(callback_query["from"]["id"]); msg=callback_query["message"]
    async with match_lock(mid):
        match=await get_match(mid); state=(match.get("state") or {}) if match else {}
        if not match or match["status"]!="lineup":
            await app.answer_callback_query(callback_query["id"], "This selection is no longer active.", show_alert=True); return
        if uid != int(state.get("bowling_user") or 0):
            await app.answer_callback_query(callback_query["id"], "This action is not for you.", show_alert=True); return
        if not state.get("selected_bowler"):
            await app.answer_callback_query(callback_query["id"], "Select a bowler first.", show_alert=True); return
        state["stage"]="batter_select"
        await from_update_state(mid, state, "lineup")
        await app.answer_callback_query(callback_query["id"], "Bowler confirmed!")
        try: await app.delete_message(msg["chat"]["id"], msg["message_id"])
        except Exception: pass
        xi=state.get("batting_xi") or []
        sent=await app.send_message(msg["chat"]["id"], _bat_text(match, int(state["batting_user"]), uid, xi, []), parse_mode="HTML", reply_markup=batter_selection_keyboard(mid, xi, []))
        await set_message_id(mid, int(sent["message_id"]))


@register_callback("playso_batter")
async def playso_batter(callback_query):
    _, mid, pid = callback_query["data"].split(":"); mid=int(mid); pid=int(pid); uid=int(callback_query["from"]["id"]); msg=callback_query["message"]
    async with match_lock(mid):
        match=await get_match(mid); state=(match.get("state") or {}) if match else {}
        if not match or match["status"]!="lineup" or state.get("stage")!="batter_select":
            await app.answer_callback_query(callback_query["id"], "This selection is no longer active.", show_alert=True); return
        if uid != int(state.get("batting_user") or 0):
            await app.answer_callback_query(callback_query["id"], "This action is not for you.", show_alert=True); return
        if not dedup_selection_click(mid, "batter", uid, pid):
            await app.answer_callback_query(callback_query["id"], "Selection already received.", show_alert=False); return
        xi=state.get("batting_xi") or []
        if not any(int(p.get("player_id") or 0)==pid for p in xi):
            await app.answer_callback_query(callback_query["id"], "That player is not in the current Playing XI.", show_alert=True); return
        selected=[int(x) for x in state.get("selected_batters") or []]
        if pid in selected:
            selected.remove(pid)
        elif len(selected) < 3:
            selected.append(pid)
        else:
            await app.answer_callback_query(callback_query["id"], "Three batters are already selected. Unselect one first.", show_alert=True); return
        state["selected_batters"]=selected
        await from_update_state(mid, state, "lineup")
        await app.answer_callback_query(callback_query["id"], "Selection updated.")
        await app.edit_message_text(msg["chat"]["id"], msg["message_id"], _bat_text(match, uid, int(state["bowling_user"]), xi, selected), parse_mode="HTML", reply_markup=batter_selection_keyboard(mid, xi, selected))


@register_callback("playso_batter_confirm")
async def playso_batter_confirm(callback_query):
    mid=int(callback_query["data"].split(":")[1]); uid=int(callback_query["from"]["id"]); msg=callback_query["message"]
    async with match_lock(mid):
        match=await get_match(mid); state=(match.get("state") or {}) if match else {}
        if not match or match["status"]!="lineup" or state.get("stage")!="batter_select":
            await app.answer_callback_query(callback_query["id"], "This selection is no longer active.", show_alert=True); return
        if uid != int(state.get("batting_user") or 0):
            await app.answer_callback_query(callback_query["id"], "This action is not for you.", show_alert=True); return
        selected=[int(x) for x in state.get("selected_batters") or []]
        if len(selected)!=3:
            await app.answer_callback_query(callback_query["id"], "Select exactly three batters.", show_alert=True); return
        state["stage"]="length"; state["ball_no"]=0; state["legal_balls"]=0; state["runs"]=0; state["wickets"]=0
        state["current_striker_id"]=selected[0]; state["current_non_striker_id"]=selected[1]
        await from_update_state(mid,state,"live")
        await app.answer_callback_query(callback_query["id"], "Batters confirmed!")
        try: await app.delete_message(msg["chat"]["id"], msg["message_id"])
        except Exception: pass
        from .live import send_bowler_length
        await send_bowler_length(msg["chat"]["id"], match, state)
