from __future__ import annotations
import asyncio, json
from handlers.registry import register_callback
from app import app
from database.playipl_repo import get_match,set_xi,set_xi_confirmed,get_recent_playing_xi,save_recent_playing_xi
from database.playipl_repo import get_team_players
from database.playipl_teams_repo import team_name,team_label
from buttons.playipl_challenger_buttons import challenger_xi_keyboard
from buttons.playipl_opponent_buttons import opponent_xi_keyboard
from utils.mentions import mention_html

NO_KEYBOARD={'inline_keyboard':[]}

def _decode_ids(value):
    if isinstance(value,str):
        try: return [int(x) for x in json.loads(value)]
        except Exception: return []
    return [int(x) for x in (value or [])]

def _chosen_in_order(players, selected_ids):
    by_id = {int(p.get('player_id') or 0): p for p in players}
    return [by_id[pid] for pid in selected_ids if pid in by_id]

def _xi_keyboard(match_id, code, players, selected_ids, is_challenger):
    return (challenger_xi_keyboard if is_challenger else opponent_xi_keyboard)(match_id, code, players, selected_ids)

def _role_counts(players):
    c={'Batsman':0,'AllRounder':0,'Bowler':0,'Wicketkeeper':0}
    for p in players:
        role=str(p.get('role',''))
        if role.lower() in {'wicketkeeper','wicket-keeper','wicket keeper','wk'}: role='Wicketkeeper'
        if role in c: c[role]+=1
    return c

def _valid(players):
    c=_role_counts(players)
    return len(players)==11 and c['Batsman']>=1 and 2<=c['AllRounder']<=4 and 3<=c['Bowler']<=4 and c['Wicketkeeper']>=1

def _build_text(code,players,selected):
    c=_role_counts([p for p in players if int(p.get('player_id') or 0) in selected])
    status='✅ Team Valid' if _valid([p for p in players if int(p.get('player_id') or 0) in selected]) else '⚠️ Team Invalid'
    lines=['<b>╭━━〔 🏏 BUILD YOUR PLAYING XI 〕━━╮</b>','',f"<b>{team_name(code).upper()} ({code})</b>",'','<blockquote>',f"<b>Playing XI: {len(selected)}/11</b>",'']
    chosen=_chosen_in_order(players, selected)
    lines.extend([p.get('name','Player') for p in chosen])
    lines += ['',f"🏏 Batsmen: {c['Batsman']}/1+",f"🔄 All-Rounders: {c['AllRounder']}/2–4",f"⚡ Bowlers: {c['Bowler']}/3–4",f"🧤 Wicketkeeper: {c['Wicketkeeper']}/1+",'',f"{status}",'</blockquote>','', '<b>╰━━━━━━━━━━━━━━━━━━╯</b>']
    return '\n'.join(lines)

async def _players(code):
    ps=await get_team_players(code)
    return ps

async def send_build_messages(chat_id,match):
    for uid,field,code,is_ch in [(match['challenger_id'],'challenger_xi',match['challenger_team_code'],True),(match['opponent_id'],'opponent_xi',match['opponent_team_code'],False)]:
        players=await _players(code)
        if len(players) < 11:
            await app.send_message(chat_id, f'<b>⚠️ {team_name(code)} ({code}) does not have at least 11 uploaded players.</b>', parse_mode='HTML')
            return
        sent=await app.send_message(chat_id,_build_text(code,players,[]),parse_mode='HTML',reply_markup=_xi_keyboard(match['match_id'],code,players,[],is_ch));
        # Separate build messages are retained; store latest one for cleanup only.
        if is_ch:
            await app.send_message(chat_id,'<b>🏏 Select your 11 players.</b>',parse_mode='HTML')

@register_callback('playipl_recent_xi')
async def playipl_recent_xi(callback_query):
    parts=callback_query['data'].split(':')
    if len(parts) != 3:
        await app.answer_callback_query(callback_query['id'],'Invalid Playing 11 request.',show_alert=True)
        return
    _,mid_s,code=parts
    mid=int(mid_s)
    uid=int(callback_query['from']['id'])
    match=await get_match(mid)
    if not match:
        await app.answer_callback_query(callback_query['id'],'This match is no longer active.',show_alert=True)
        return
    if uid not in {int(match['challenger_id']),int(match['opponent_id'])}:
        await app.answer_callback_query(callback_query['id'],'You are not part of this match.',show_alert=True)
        return
    is_ch = uid == int(match['challenger_id'])
    expected = match.get('challenger_team_code') if is_ch else match.get('opponent_team_code')
    if code != expected:
        await app.answer_callback_query(callback_query['id'],'Invalid team.',show_alert=True)
        return
    saved = await get_recent_playing_xi(uid, code)
    if not saved:
        await app.answer_callback_query(callback_query['id'],"You don't have any Playing 11 record. You need to make first.",show_alert=True)
        return
    players=await _players(code)
    current_ids={int(p.get('player_id') or 0) for p in players}
    if len(saved) != 11 or len(set(saved)) != 11 or any(pid not in current_ids for pid in saved):
        await app.answer_callback_query(callback_query['id'],'Your saved Playing 11 is no longer available. You need to make a new one.',show_alert=True)
        return
    chosen=_chosen_in_order(players,saved)
    if not _valid(chosen):
        await app.answer_callback_query(callback_query['id'],'Your saved Playing 11 is no longer valid. You need to make a new one.',show_alert=True)
        return
    field='challenger_xi' if is_ch else 'opponent_xi'
    await set_xi(mid,uid,saved,is_challenger=is_ch)
    await app.answer_callback_query(callback_query['id'],'Last Playing 11 restored.')
    await app.edit_message_text(callback_query['message']['chat']['id'],callback_query['message']['message_id'],_build_text(code,players,saved),parse_mode='HTML',reply_markup=_xi_keyboard(mid,code,players,saved,is_ch))

@register_callback('playipl_xi')
async def playipl_xi(callback_query):
    _,mid_s,code,pid_s=callback_query['data'].split(':'); mid=int(mid_s); pid=int(pid_s); uid=int(callback_query['from']['id']); match=await get_match(mid)
    if not match: return
    if uid==int(match['challenger_id']): field='challenger_xi'; is_ch=True
    elif uid==int(match['opponent_id']): field='opponent_xi'; is_ch=False
    else:
        await app.answer_callback_query(callback_query['id'],'You are not part of this match.',show_alert=True); return
    expected=match.get('challenger_team_code') if is_ch else match.get('opponent_team_code')
    if code!=expected:
        await app.answer_callback_query(callback_query['id'],'Invalid team.',show_alert=True); return
    selected=_decode_ids(match.get(field))
    players=await _players(code)
    player_ids={int(p.get('player_id') or 0) for p in players}
    if pid not in player_ids:
        await app.answer_callback_query(callback_query['id'],'Player not found.',show_alert=True); return
    if pid in selected:
        selected.remove(pid); msg='Player unselected.'
    elif len(selected)>=11:
        await app.answer_callback_query(callback_query['id'],'You can select maximum 11 players.',show_alert=True); return
    else:
        selected.append(pid); msg='Player selected.'
    # Answer immediately so Telegram clears the button spinner at once; the
    # short DB write and message edit then happen without blocking the UI.
    await app.answer_callback_query(callback_query['id'],msg)
    await set_xi(mid,uid,selected,is_challenger=is_ch)
    await app.edit_message_text(callback_query['message']['chat']['id'],callback_query['message']['message_id'],_build_text(code,players,selected),parse_mode='HTML',reply_markup=_xi_keyboard(mid,code,players,selected,is_ch))

@register_callback('playipl_xi_confirm')
async def playipl_xi_confirm(callback_query):
    _,mid_s,code=callback_query['data'].split(':'); mid=int(mid_s); uid=int(callback_query['from']['id']); match=await get_match(mid)
    if not match: return
    is_ch=uid==int(match['challenger_id'])
    if not is_ch and uid!=int(match['opponent_id']):
        await app.answer_callback_query(callback_query['id'],'You are not part of this match.',show_alert=True); return
    field='challenger_xi' if is_ch else 'opponent_xi'; selected=_decode_ids(match.get(field)); players=await _players(code); chosen=_chosen_in_order(players, selected)
    if not _valid(chosen):
        await app.answer_callback_query(callback_query['id'],'Team is invalid. Complete the required role limits first.',show_alert=True); return
    await set_xi_confirmed(mid,uid); await save_recent_playing_xi(uid,code,selected)
    await app.answer_callback_query(callback_query['id'],'Playing XI confirmed!')
    await app.delete_message(callback_query['message']['chat']['id'],callback_query['message']['message_id'])
    role_emoji={'Batsman':'🏏','Bowler':'⚡','AllRounder':'🔄','Wicketkeeper':'🧤'}
    preview='<b>🏏 PLAYING XI</b>\n\n'+'\n'.join(f"{i+1}. {role_emoji.get(str(p.get('role')),'🏏')} {p.get('name')} • OVR {max(int(p.get('bat_level') or 0),int(p.get('bowl_level') or 0))}" for i,p in enumerate(chosen))
    await app.send_message(callback_query['message']['chat']['id'],preview,parse_mode='HTML')
    await asyncio.sleep(1)
    player_mention=mention_html(uid, callback_query['from'].get('username'), callback_query['from'].get('first_name'))
    team=team_name(code); bench=[]
    for p in players:
        if int(p.get('player_id') or 0) not in selected: bench.append(p)
        if len(bench)>=5: break
    final=("<b>╭━━〔 🏏 PLAYING XI CONFIRMED 〕━━╮</b>\n\n"
           f"🏏 <b>{player_mention} • {team.upper()} ({code})</b>\n\n"
           "<blockquote><b>🏏 PLAYING XI</b>\n"+"\n".join(f"{i+1}. {p.get('name')} • OVR {max(int(p.get('bat_level') or 0),int(p.get('bowl_level') or 0))}" for i,p in enumerate(chosen))+"</blockquote>\n\n"
           "<blockquote><b>🪑 BENCH / SUBSTITUTES</b>\n"+"\n".join(f"• {p.get('name')} • OVR {max(int(p.get('bat_level') or 0),int(p.get('bowl_level') or 0))}" for p in bench)+"</blockquote>\n\n🔒 <b>Playing XI Locked</b>\n\n╰━━━━━━━━━━━━━━━━━━╯")
    await app.send_message(callback_query['message']['chat']['id'],final,parse_mode='HTML')
    m=await get_match(mid)
    if m.get('challenger_xi_confirmed') and m.get('opponent_xi_confirmed'):
        from .pitch import send_pitch_selection
        await send_pitch_selection(callback_query['message']['chat']['id'],m)
