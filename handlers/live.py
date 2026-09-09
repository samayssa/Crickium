from __future__ import annotations

import html
import asyncio
from handlers.registry import register_callback
from app import app
from buttons.playso_buttons import length_keyboard, delivery_keyboard, line_keyboard, foot_keyboard, intent_keyboard, shot_keyboard
from database.playso_repo import get_match, set_state, set_basic
from engines.playso_probability import deliveries_for_family, shots_for
from engines.playso_engine import resolve_ball
from engines.commentary_play_engine import get_commentary
from database.query import transaction
from engines.level_engine import add_xp, WIN_XP
from handlers.playso.common import match_lock, bowling_family, bowling_family_label, role_emoji, mention_html, callback_message_is_current

NO_KEYBOARD = {"inline_keyboard": []}


def _state(match: dict) -> dict:
    return match.get("state") or {}


def _player(state: dict, key: str, pid: int) -> dict | None:
    return next((p for p in state.get(key) or [] if int(p.get("player_id") or 0)==int(pid)), None)


def _innings_score(state: dict) -> str:
    return f"{int(state.get('runs') or 0)}/{int(state.get('wickets') or 0)}  ({int(state.get('legal_balls') or 0)//6}.{int(state.get('legal_balls') or 0)%6} ov)"


def _bowler_flow_text(match: dict, state: dict, title: str, body: list[str]) -> str:
    user= int(state.get("bowling_user") or 0)
    mname = match.get("challenger_name") if user == int(match["challenger_id"]) else match.get("opponent_name")
    mun = match.get("challenger_username") if user == int(match["challenger_id"]) else match.get("opponent_username")
    mention=mention_html(user, mun, mname)
    header=f"<b>╭━━〔 ⚡ PLAYSO • {title} 〕━━╮</b>\n\n👤 {mention}\n\n"
    return header + "\n".join(body) + "\n\n<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"


def _batter_flow_text(match: dict, state: dict, title: str, body: list[str]) -> str:
    user=int(state.get("batting_user") or 0)
    mention=mention_html(user, match.get("challenger_username") if user == int(match["challenger_id"]) else match.get("opponent_username"), match.get("challenger_name") if user == int(match["challenger_id"]) else match.get("opponent_name"))
    return f"<b>╭━━〔 🏏 PLAYSO • {title} 〕━━╮</b>\n\n👤 {mention}\n\n" + "\n".join(body) + "\n\n<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"


def _score_lines(state: dict) -> list[str]:
    return [f"<b>📊 Score ➤ {_innings_score(state)}</b>", f"<b>📈 CRR: {(int(state.get('runs') or 0) * 6 / int(state.get('legal_balls') or 1)):.2f}</b>"]


def _team_mention(match: dict, uid: int) -> str:
    if uid == int(match["challenger_id"]):
        return mention_html(uid, match.get("challenger_username"), match.get("challenger_name"))
    return mention_html(uid, match.get("opponent_username"), match.get("opponent_name"))


def _player_by_id(state: dict, key: str, pid: int) -> dict:
    return _player(state, key, pid) or {}


def _commentary_lines(state: dict) -> list[str]:
    return list(state.get("commentary") or [])


def _this_over(state: dict) -> list[str]:
    return list(state.get("this_over") or [])


def _outcome_symbol(result) -> str:
    if result.wicket:
        return "W"
    if result.outcome == "WIDE":
        return "Wd"
    if result.outcome == "NO_BALL":
        return "Nb"
    if result.outcome == "LEG_BYE":
        return "Lb"
    if result.outcome == "BYE":
        return "B"
    return str(result.runs)


def _commentary_outcome_key(result) -> str:
    if result.wicket:
        return "wicket"
    mapping = {"WIDE": "wide", "NO_BALL": "no_ball", "LEG_BYE": "leg_bye", "BYE": "bye",
               "0": "dot", "1": "single", "2": "double", "3": "triple", "4": "four", "6": "six"}
    return mapping.get(str(result.outcome).upper(), str(result.outcome).lower())


def _render_live_score(match: dict, state: dict) -> str:
    batting_user = int(state.get("batting_user") or match.get("challenger_id") or 0)
    bowling_user = int(state.get("bowling_user") or match.get("opponent_id") or 0)
    batting_mention = _team_mention(match, batting_user)
    bowling_mention = _team_mention(match, bowling_user)
    sel = [int(x) for x in state.get("selected_batters") or []]
    striker_id = int(state.get("current_striker_id") or (sel[0] if sel else 0))
    non_id = int(state.get("current_non_striker_id") or (sel[1] if len(sel) > 1 else 0))
    striker = _player_by_id(state, "batting_xi", striker_id)
    non = _player_by_id(state, "batting_xi", non_id)
    bowler = _player_by_id(state, "bowling_xi", int(state.get("selected_bowler") or 0))
    bs = state.get("batter_stats") or {}
    b1 = bs.get(str(striker_id), {"runs": 0, "balls": 0})
    b2 = bs.get(str(non_id), {"runs": 0, "balls": 0})
    bws = (state.get("bowler_stats") or {}).get(str(int(state.get("selected_bowler") or 0)), {"wickets": 0, "runs": 0, "balls": 0})
    legal = int(state.get("legal_balls") or 0)
    crr = (int(state.get("runs") or 0) * 6 / legal) if legal else 0.0
    commentary = _commentary_lines(state)
    over = _this_over(state)
    over_html = " • ".join(html.escape(str(x)) for x in over) if over else "—"
    comment_block = ""
    if commentary:
        lines=[]
        for i, line in enumerate(commentary, 1):
            lines.append(f"<b>{i}️⃣</b> {html.escape(str(line))}")
        comment_block = "\n\n<b>🗣️ COMMENTARY</b>\n<blockquote expandable>" + "\n\n".join(lines) + "</blockquote>"
    bowler_name = html.escape(str(bowler.get("name") or "Jasprit Bumrah"))
    bowling_family_name = html.escape(str(state.get("delivery") or "—"))
    length = str(state.get("length") or "—").replace("_", " ").title()
    line = str(state.get("line") or "—").replace("_", " ").title()
    body = (
        "<b>╭━━━〔 🏏 S-O • LIVE SCORE 〕━━━╮</b>\n\n"
        f"<b>🏏 {batting_mention} XI</b>\n\n"
        f"<b>📊 Score ➤ {int(state.get('runs') or 0)}/{int(state.get('wickets') or 0)} ({legal//6}.{legal%6} Ov)</b>\n"
        f"<b>📈 CRR: {crr:.2f}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>◉ {html.escape(str(striker.get('name') or 'Striker'))}    {int(b1.get('runs') or 0)} ({int(b1.get('balls') or 0)})</b>\n"
        f"<b>  {html.escape(str(non.get('name') or 'Non-Striker'))}    {int(b2.get('runs') or 0)} ({int(b2.get('balls') or 0)})</b>\n\n"
        "<b>🤝 Partnership</b>\n"
        f"<b>{int(state.get('partnership_runs') or 0)} Runs • {int(state.get('partnership_balls') or 0)} Balls</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🎯 {bowling_mention} XI</b>\n\n"
        f"<b>🥎 {bowler_name}</b>\n"
        f"<b>{int(bws.get('wickets') or 0)}W • {int(bws.get('runs') or 0)}R • {int(bws.get('balls') or 0)//6}.{int(bws.get('balls') or 0)%6} Ov</b>\n\n"
        f"<b>🎯 {length} • {bowling_family_name} • {line}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>This Over [ {over_html} ]</b>\n\n"
    )
    if comment_block:
        body += comment_block + "\n\n"
    body += "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    return body



async def send_bowler_length(chat_id: int, match: dict, state: dict):
    text = _render_live_score(match, state)
    sent=await app.send_message(chat_id,text,parse_mode="HTML",reply_markup=length_keyboard(int(match["match_id"])))
    from database.playso_repo import set_message_id
    await set_message_id(int(match["match_id"]),int(sent["message_id"]))


@register_callback("playso_length")
async def playso_length(callback_query):
    _,mid,length=callback_query["data"].split(":"); mid=int(mid); uid=int(callback_query["from"]["id"]); msg=callback_query["message"]
    async with match_lock(mid):
        match=await get_match(mid); state=_state(match) if match else {}
        if not match or match["status"]!="live" or state.get("stage")!="length":
            await app.answer_callback_query(callback_query["id"],"This action is no longer active.",show_alert=True); return
        if not callback_message_is_current(dict(match), callback_query):
            await app.answer_callback_query(callback_query["id"],"This action is no longer active.",show_alert=True); return
        if uid != int(state.get("bowling_user") or 0):
            await app.answer_callback_query(callback_query["id"],"This action is not for you.",show_alert=True); return
        family=bowling_family(_player(state,"bowling_xi",int(state["selected_bowler"] or 0)) or {})
        if family != "pace" and length == "short":
            await app.answer_callback_query(callback_query["id"],"Spin bowlers use Full or Back of Length only.",show_alert=True); return
        state["length"]=length; state["stage"]="delivery"
        await set_state(mid,state,"live")
        deliveries=deliveries_for_family(family, str(state.get("length") or "full"))
        await app.answer_callback_query(callback_query["id"],"Length selected!")
        await app.edit_message_text(msg["chat"]["id"],msg["message_id"],_render_live_score(match,state),parse_mode="HTML",reply_markup=delivery_keyboard(mid,deliveries))


@register_callback("playso_delivery")
async def playso_delivery(callback_query):
    _,mid,index=callback_query["data"].split(":"); mid=int(mid); index=int(index); uid=int(callback_query["from"]["id"]); msg=callback_query["message"]
    async with match_lock(mid):
        match=await get_match(mid); state=_state(match) if match else {}
        if not match or state.get("stage")!="delivery":
            await app.answer_callback_query(callback_query["id"],"This action is no longer active.",show_alert=True); return
        if not callback_message_is_current(dict(match), callback_query):
            await app.answer_callback_query(callback_query["id"],"This action is no longer active.",show_alert=True); return
        if uid != int(state.get("bowling_user") or 0):
            await app.answer_callback_query(callback_query["id"],"This action is not for you.",show_alert=True); return
        bowler=_player(state,"bowling_xi",int(state["selected_bowler"])); family=bowling_family(bowler or {})
        deliveries=deliveries_for_family(family, str(state.get("length") or "full"))
        if index < 0 or index >= len(deliveries):
            await app.answer_callback_query(callback_query["id"],"Invalid delivery.",show_alert=True); return
        state["delivery"]=deliveries[index]; state["stage"]="line"; await set_state(mid,state,"live")
        await app.answer_callback_query(callback_query["id"],"Delivery selected!")
        await app.edit_message_text(msg["chat"]["id"],msg["message_id"],_render_live_score(match,state),parse_mode="HTML",reply_markup=line_keyboard(mid))


@register_callback("playso_line")
async def playso_line(callback_query):
    _,mid,line=callback_query["data"].split(":"); mid=int(mid); uid=int(callback_query["from"]["id"]); msg=callback_query["message"]
    async with match_lock(mid):
        match=await get_match(mid); state=_state(match) if match else {}
        if not match or state.get("stage")!="line":
            await app.answer_callback_query(callback_query["id"],"This action is no longer active.",show_alert=True); return
        if not callback_message_is_current(dict(match), callback_query):
            await app.answer_callback_query(callback_query["id"],"This action is no longer active.",show_alert=True); return
        if uid != int(state.get("bowling_user") or 0):
            await app.answer_callback_query(callback_query["id"],"This action is not for you.",show_alert=True); return
        state["line"]=line; state["stage"]="foot"; await set_state(mid,state,"live")
        await app.answer_callback_query(callback_query["id"],"Line selected!")
        try: await app.delete_message(msg["chat"]["id"],msg["message_id"])
        except Exception: pass
        await send_batter_foot(msg["chat"]["id"],match,state)


async def send_batter_foot(chat_id:int, match:dict, state:dict):
    text = _render_live_score(match, state)
    sent=await app.send_message(chat_id,text,parse_mode="HTML",reply_markup=foot_keyboard(int(match["match_id"])))
    from database.playso_repo import set_message_id
    await set_message_id(int(match["match_id"]),int(sent["message_id"]))


@register_callback("playso_foot")
async def playso_foot(callback_query):
    _,mid,foot=callback_query["data"].split(":"); mid=int(mid); uid=int(callback_query["from"]["id"]); msg=callback_query["message"]
    async with match_lock(mid):
        match=await get_match(mid); state=_state(match) if match else {}
        if not match or state.get("stage")!="foot":
            await app.answer_callback_query(callback_query["id"],"This action is no longer active.",show_alert=True); return
        if not callback_message_is_current(dict(match), callback_query):
            await app.answer_callback_query(callback_query["id"],"This action is no longer active.",show_alert=True); return
        if uid != int(state.get("batting_user") or 0):
            await app.answer_callback_query(callback_query["id"],"This action is not for you.",show_alert=True); return
        state["foot"]=foot; state["stage"]="intent"; await set_state(mid,state,"live")
        await app.answer_callback_query(callback_query["id"],"Foot movement selected!")
        await app.edit_message_text(msg["chat"]["id"],msg["message_id"],_render_live_score(match,state),parse_mode="HTML",reply_markup=intent_keyboard(mid))


@register_callback("playso_intent")
async def playso_intent(callback_query):
    _,mid,intent=callback_query["data"].split(":"); mid=int(mid); uid=int(callback_query["from"]["id"]); msg=callback_query["message"]
    async with match_lock(mid):
        match=await get_match(mid); state=_state(match) if match else {}
        if not match or state.get("stage")!="intent":
            await app.answer_callback_query(callback_query["id"],"This action is no longer active.",show_alert=True); return
        if not callback_message_is_current(dict(match), callback_query):
            await app.answer_callback_query(callback_query["id"],"This action is no longer active.",show_alert=True); return
        if uid != int(state.get("batting_user") or 0):
            await app.answer_callback_query(callback_query["id"],"This action is not for you.",show_alert=True); return
        state["intent"]=intent; state["stage"]="shot"; await set_state(mid,state,"live")
        shots=shots_for(state.get("foot") or "front",intent)
        await app.answer_callback_query(callback_query["id"],"Intent selected!")
        await app.edit_message_text(msg["chat"]["id"],msg["message_id"],_render_live_score(match,state),parse_mode="HTML",reply_markup=shot_keyboard(mid,shots))


@register_callback("playso_shot")
async def playso_shot(callback_query):
    _,mid,index=callback_query["data"].split(":"); mid=int(mid); index=int(index); uid=int(callback_query["from"]["id"]); msg=callback_query["message"]
    async with match_lock(mid):
        match=await get_match(mid); state=_state(match) if match else {}
        if not match or state.get("stage")!="shot":
            await app.answer_callback_query(callback_query["id"],"This action is no longer active.",show_alert=True); return
        if not callback_message_is_current(dict(match), callback_query):
            await app.answer_callback_query(callback_query["id"],"This action is no longer active.",show_alert=True); return
        if uid != int(state.get("batting_user") or 0):
            await app.answer_callback_query(callback_query["id"],"This action is not for you.",show_alert=True); return
        shots=shots_for(state.get("foot") or "front",state.get("intent") or "grounded")
        if index<0 or index>=len(shots):
            await app.answer_callback_query(callback_query["id"],"Invalid shot.",show_alert=True); return
        shot=shots[index]
        bowler=_player(state,"bowling_xi",int(state["selected_bowler"])); batter=_player(state,"batting_xi",int((state.get("selected_batters") or [0])[0]))
        # The first selected batter is the striker at the start of the over; rotate below.
        current_striker_id=int(state.get("current_striker_id") or (state.get("selected_batters") or [0])[0])
        batter=_player(state,"batting_xi",current_striker_id) or batter
        if not bowler or not batter:
            await app.answer_callback_query(callback_query["id"],"Player state is invalid.",show_alert=True); return
        state["shot"]=shot
        result=resolve_ball(pitch=str(match.get("pitch") or "even"),bowler=bowler,batter=batter,family=bowling_family(bowler),delivery=str(state.get("delivery")),line=str(state.get("line")),length=str(state.get("length")),foot=str(state.get("foot")),intent=str(state.get("intent")),shot=shot)
        state["runs"]=int(state.get("runs") or 0)+result.runs
        if result.legal: state["legal_balls"]=int(state.get("legal_balls") or 0)+1
        state["ball_no"]=int(state.get("ball_no") or 0)+1
        if result.wicket: state["wickets"]=int(state.get("wickets") or 0)+1
        # Persistent Super Over ball timeline and commentary. Never clear it between ball prompts.
        over = list(state.get("this_over") or [])
        over.append(_outcome_symbol(result))
        state["this_over"] = over
        comments = list(state.get("commentary") or [])
        innings_ball_number = len(comments) + 1
        commentary = get_commentary(
            _commentary_outcome_key(result),
            over_number=1,
            balls_faced=int(((state.get("batter_stats") or {}).get(str(current_striker_id)) or {}).get("balls") or 0),
            result={
                "outcome": _commentary_outcome_key(result), "runs": int(result.runs or 0),
                "batter_name": batter.get("name"), "bowler_name": bowler.get("name"),
                "shot_name": shot, "delivery_name": state.get("delivery"),
                "line": state.get("line"), "length": state.get("length"),
            },
        )
        comments.append(commentary)
        state["commentary"] = comments
        if result.legal:
            state["partnership_balls"] = int(state.get("partnership_balls") or 0) + 1
        state["partnership_runs"] = int(state.get("partnership_runs") or 0) + int(result.runs or 0)
        # stats for summary
        bst=state.setdefault("batter_stats",{})
        key=str(current_striker_id); bs=bst.setdefault(key,{"name":batter.get("name"),"runs":0,"balls":0,"fours":0,"sixes":0})
        if result.legal: bs["balls"] += 1
        if result.outcome not in {"WIDE","NO_BALL","LEG_BYE","BYE","OUT"}: bs["runs"] += result.runs
        if result.outcome=="4": bs["fours"] += 1
        if result.outcome=="6": bs["sixes"] += 1
        bwst=state.setdefault("bowler_stats",{}); bk=str(bowler.get("player_id")); bws=bwst.setdefault(bk,{"name":bowler.get("name"),"runs":0,"balls":0,"wickets":0})
        if result.legal: bws["balls"] += 1
        bws["runs"] += result.runs
        if result.wicket: bws["wickets"] += 1
        # rotation for runs/wicket
        sel=[int(x) for x in state.get("selected_batters") or []]
        if result.wicket:
            if len(sel) > 2:
                dismissed=current_striker_id
                remaining=[x for x in sel if x!=dismissed]
                new_ids=[x for x in sel if x not in remaining]
                if len(remaining)>=2:
                    # one-down enters as striker when wicket falls
                    state["current_striker_id"]=next((x for x in sel if x not in {current_striker_id, int(state.get('current_non_striker_id') or 0)}), remaining[0])
                else:
                    state["current_striker_id"]=remaining[0] if remaining else current_striker_id
        elif result.runs % 2 == 1:
            a=state.get("current_striker_id") or current_striker_id; n=state.get("current_non_striker_id") or (sel[1] if len(sel)>1 else a)
            state["current_striker_id"]=n; state["current_non_striker_id"]=a
        if not state.get("current_non_striker_id") and len(sel)>1: state["current_non_striker_id"]=sel[1]
        state["stage"]="result"
        await set_state(mid,state,"live")
        await app.answer_callback_query(callback_query["id"],"Shot resolved!")
        text=_render_live_score(match,state)
        await app.edit_message_text(msg["chat"]["id"],msg["message_id"],text,parse_mode="HTML",reply_markup=NO_KEYBOARD)
        await asyncio.sleep(1.2)
        try: await app.delete_message(msg["chat"]["id"],msg["message_id"])
        except Exception: pass
        # End of innings, else next ball.
        end = int(state.get("legal_balls") or 0)>=6 or int(state.get("wickets") or 0)>=2
        if state.get("target") is not None and int(state.get("runs") or 0)>=int(state["target"]): end=True
        if end:
            await finish_innings(msg["chat"]["id"],match,state)
            return
        state["stage"]="length"; state["length"]=state["delivery"]=state["line"]=state["foot"]=state["intent"]=state["shot"]=None
        await set_state(mid,state,"live")
        await send_bowler_length(msg["chat"]["id"],match,state)


async def finish_innings(chat_id:int, match:dict, state:dict):
    innings_no=int(match.get("innings_no") or state.get("innings_no") or 1)
    snap={"innings_number":innings_no,"batting_team_id":int(state.get("batting_user") or 0),"bowling_team_id":int(state.get("bowling_user") or 0),"batting_team_display": (match.get("challenger_username") if int(state.get("batting_user") or 0)==int(match["challenger_id"]) else match.get("opponent_username")) or (match.get("challenger_name") if int(state.get("batting_user") or 0)==int(match["challenger_id"]) else match.get("opponent_name")), "bowling_team_display": (match.get("challenger_username") if int(state.get("bowling_user") or 0)==int(match["challenger_id"]) else match.get("opponent_username")) or (match.get("challenger_name") if int(state.get("bowling_user") or 0)==int(match["challenger_id"]) else match.get("opponent_name")),"runs":int(state.get("runs") or 0),"wickets":int(state.get("wickets") or 0),"legal_balls":int(state.get("legal_balls") or 0),"over_text":f"{int(state.get('legal_balls') or 0)//6}.{int(state.get('legal_balls') or 0)%6}","batters":list(state.get("batter_stats",{}).values()),"bowlers":list(state.get("bowler_stats",{}).values())}
    history=list(state.get("innings_history") or []); history.append(snap)
    if innings_no==1:
        target=int(state.get("runs") or 0)+1
        first_bat=int(state.get("batting_user") or 0); first_bowl=int(state.get("bowling_user") or 0)
        first_bowler=int(state.get("selected_bowler") or 0)
        new_state={"innings_history":history,"target":target,"first_batting_user":first_bat,"first_bowling_user":first_bowl,"first_bowler_id":first_bowler,"second_batting_user":first_bowl,"second_bowling_user":first_bat,"previous_bowlers":{},"innings_no":2,"this_over":[],"commentary":[],"partnership_runs":0,"partnership_balls":0}
        await set_state(int(match["match_id"]),new_state,"innings_break")
        await app.send_message(chat_id,f"<b>╭━━〔 🔄 PLAYSO • INNINGS BREAK 〕━━╮\n\n🏏 First Innings ➤ {snap['runs']}/{snap['wickets']} ({snap['over_text']})\n🎯 Target ➤ {target}\n\n🔥 Best Batter ➤ {max((snap['batters'] or [{"name":"—","runs":0}]), key=lambda x:int(x.get('runs') or 0)).get('name')}\n🎯 Best Bowler ➤ {max((snap['bowlers'] or [{"name":"—","wickets":0}]), key=lambda x:int(x.get('wickets') or 0)).get('name')}\n\n<b>Second Super Over innings begins.</b>\n\n╰━━━━━━━━━━━━━━━━━━━━╯</b>",parse_mode="HTML")
        await asyncio.sleep(1.5)
        from .setup import start_setup
        fresh=await get_match(int(match["match_id"]))
        await start_setup(chat_id,dict(fresh),2)
        return
    # final result
    history_first=history[0]; second=snap
    first_runs=int(history_first["runs"]); second_runs=int(second["runs"])
    if second_runs==first_runs:
        previous_bowlers={
            str(int(history_first["bowling_team_id"])): int((state.get("first_bowler_id") or 0)),
            str(int(second["bowling_team_id"])): int(state.get("selected_bowler") or 0),
        }
        await set_state(int(match["match_id"]), {
            "innings_history": history,
            "previous_bowlers": previous_bowlers,
            "next_super_over_batting_user": int(second["batting_team_id"]),
            "next_super_over_bowling_user": int(second["bowling_team_id"]),
            "is_repeat_super_over": True,
        }, "innings_break")
        await app.send_message(chat_id,"<b>⚖️ PLAYSO TIED\n\nBoth sides finished level.\n\n⚡ The second Super Over starts in 5 seconds.</b>",parse_mode="HTML")
        await asyncio.sleep(5)
        fresh=await get_match(int(match["match_id"]))
        # Keep the teams in the same roles they had in the last innings; next over starts with last innings' batting team.
        st=dict(fresh.get("state") or {}); st["previous_bowlers"]=state.get("previous_bowlers") or {}; st["innings_history"]=[]; st["ball_no"]=0
        st["next_super_over_batting_user"]=int(second["batting_team_id"]); st["next_super_over_bowling_user"]=int(second["bowling_team_id"])
        await set_state(int(match["match_id"]),st,status="lineup"); await set_basic(int(match["match_id"]),innings_no=1)
        stmatch=await get_match(int(match["match_id"]))
        # Override setup role users with explicit next-over roles.
        from .setup import start_setup
        await start_setup(chat_id,dict(stmatch),1)
        return
    winner=int(second["batting_team_id"] if second_runs>first_runs else history_first["batting_team_id"])
    loser=int(match["opponent_id"] if winner==int(match["challenger_id"]) else match["challenger_id"])
    from database.user_stats_repo import record_match_result, record_h2h_result
    async def _award_playso_rewards(winner_id:int, loser_id:int):
        async def _tx(conn):
            for uid, coins, xp_gain, won in ((winner_id,10000,WIN_XP,True),(loser_id,5000,0,False)):
                row = await conn.fetchrow("SELECT level, xp FROM users WHERE user_id=$1 FOR UPDATE;", uid)
                level = int(row["level"] or 1) if row else 1
                xp = int(row["xp"] or 0) if row else 0
                new_level, new_xp = add_xp(level, xp, xp_gain)
                await conn.execute("UPDATE users SET balance=balance+$1, level=$2, xp=$3 WHERE user_id=$4;", coins, new_level, new_xp, uid)
                await conn.execute("INSERT INTO player_stats (user_id,matches,wins,losses) VALUES ($1,1,$2,$3) ON CONFLICT (user_id) DO UPDATE SET matches=player_stats.matches+1,wins=player_stats.wins+$2,losses=player_stats.losses+$3;", uid, 1 if won else 0, 0 if won else 1)
        await transaction(_tx)
    try:
        await _award_playso_rewards(winner, loser)
    except Exception as exc: print(f"[playso] rewards failed: {exc!r}")
    try:
        await record_h2h_result(900_000_000_000_000_000 + int(match["match_id"]), int(match["challenger_id"]), int(match["opponent_id"]), winner)
    except Exception as exc: print(f"[playso] h2h failed: {exc!r}")
    winner_display=(match.get("challenger_username") if winner==int(match["challenger_id"]) else match.get("opponent_username")) or (match.get("challenger_name") if winner==int(match["challenger_id"]) else match.get("opponent_name")) or "Winner"
    winner_mention=mention_html(winner, match.get("challenger_username") if winner==int(match["challenger_id"]) else match.get("opponent_username"), match.get("challenger_name") if winner==int(match["challenger_id"]) else match.get("opponent_name"))
    margin=f"{max(0,abs(second_runs-first_runs))} run(s)"
    all_batters=(history_first.get("batters") or [])+(second.get("batters") or [])
    all_bowlers=(history_first.get("bowlers") or [])+(second.get("bowlers") or [])
    potm=max(all_batters, key=lambda p: (int(p.get("runs") or 0), int(p.get("fours") or 0)+2*int(p.get("sixes") or 0)), default={"name":"—","runs":0,"balls":0,"wickets":0})
    if not all_batters and all_bowlers:
        potm=max(all_bowlers, key=lambda p: (int(p.get("wickets") or 0), -int(p.get("runs") or 0)))
    try:
        await app.send_message(chat_id,f"<b>🏆 PLAYSO COMPLETE\n\n{winner_mention} wins the Super Over!\n\nFirst Innings ➤ {first_runs}/{history_first['wickets']}\nSecond Innings ➤ {second_runs}/{second['wickets']}\n\n🥇 Winner ➤ {winner_mention}\n⚔️ Margin ➤ {margin}\n\nThe battle is over. ⚡</b>",parse_mode="HTML")
        await asyncio.sleep(.5)
    except Exception: pass
    try:
        loser_display_id = loser
        winner_mention = mention_html(winner, match.get("challenger_username") if winner==int(match["challenger_id"]) else match.get("opponent_username"), match.get("challenger_name") if winner==int(match["challenger_id"]) else match.get("opponent_name"))
        loser_mention = mention_html(loser_display_id, match.get("challenger_username") if loser_display_id==int(match["challenger_id"]) else match.get("opponent_username"), match.get("challenger_name") if loser_display_id==int(match["challenger_id"]) else match.get("opponent_name"))
        reward_caption = ("<b>🏆 MATCH REWARDS</b>\n\n"
                          f"🥇 Winner ➤ {winner_mention}\n"
                          "🪙 +10,000 Coins • ⭐ +50 XP\n\n"
                          f"🥈 Loser ➤ {loser_mention}\n"
                          "🪙 +5,000 Coins • ⭐ 0 XP")
        await send_match_summary(app,chat_id,[history_first,second],winner=winner_display,margin=margin,potm=potm,caption=reward_caption)
    except Exception as exc: print(f"[playso] summary card failed: {exc!r}")
    await set_state(int(match["match_id"]),{**state,"innings_history":history,"winner_id":winner},"completed")
