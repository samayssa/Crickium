from __future__ import annotations
import asyncio, json
from handlers.registry import register_callback
from app import app
from database.playint_repo import get_match,set_xi,set_xi_confirmed
from database.playint_repo import get_team_players
from database.playint_teams_repo import team_flag,team_name,team_label
from buttons.playint_buttons import xi_keyboard
from utils.mentions import mention_html
from utils.country_flags import flag_for

NO_KEYBOARD={'inline_keyboard':[]}

def _decode_ids(value):
    if isinstance(value,str):
        try: return [int(x) for x in json.loads(value)]
        except Exception: return []
    return [int(x) for x in (value or [])]

def _role_counts(players):
    c={'Batsman':0,'AllRounder':0,'Bowler':0,'Wicketkeeper':0}
    for p in players:
        role=str(p.get('role',''))
        if role.lower() in {'wicketkeeper','wicket-keeper','wicket keeper','wk'}: role='Wicketkeeper'
        if role in c: c[role]+=1
    return c

def _valid(players):
    c=_role_counts(players)
    return len(players)==11 and 3<=c['Batsman']<=4 and 3<=c['AllRounder']<=4 and 3<=c['Bowler']<=4 and 1<=c['Wicketkeeper']<=2

def _build_text(code,players,selected):
    c=_role_counts([p for p in players if int(p.get('player_id') or 0) in selected])
    status='✅ Team Valid' if _valid([p for p in players if int(p.get('player_id') or 0) in selected]) else '⚠️ Team Invalid'
    lines=['<b>╭━━〔 🏏 BUILD YOUR PLAYING XI 〕━━╮</b>','',f"{team_label(code).split(' ',1)[0]} <b>{team_name(code).upper()}</b>",'','<blockquote>',f"<b>Playing XI: {len(selected)}/11</b>",'']
    chosen=[p for p in players if int(p.get('player_id') or 0) in selected]
    lines.extend([p.get('name','Player') for p in chosen])
    lines += ['',f"🏏 Batsmen: {c['Batsman']}/3–4",f"🔄 All-Rounders: {c['AllRounder']}/3–4",f"⚡ Bowlers: {c['Bowler']}/3–4",f"🧤 Wicketkeeper: {c['Wicketkeeper']}/1–2",'',f"{status}",'</blockquote>','', '<b>╰━━━━━━━━━━━━━━━━━━╯</b>']
    return '\n'.join(lines)

async def _players(code):
    ps=await get_team_players(code)
    for p in ps: p['_flag']=team_flag(code)
    return ps

async def send_build_messages(chat_id,match):
    for uid,field,code,is_ch in [(match['challenger_id'],'challenger_xi',match['challenger_team_code'],True),(match['opponent_id'],'opponent_xi',match['opponent_team_code'],False)]:
        players=await _players(code); sent=await app.send_message(chat_id,_build_text(code,players,set()),parse_mode='HTML',reply_markup=xi_keyboard(match['match_id'],code,players,set(),is_ch));
        # Separate build messages are retained; store latest one for cleanup only.
        if is_ch:
            await app.send_message(chat_id,'<b>🏏 Select your 11 players.</b>',parse_mode='HTML')

@register_callback('playint_xi')
async def playint_xi(callback_query):
    _,mid_s,code,pid_s=callback_query['data'].split(':'); mid=int(mid_s); pid=int(pid_s); uid=int(callback_query['from']['id']); match=await get_match(mid)
    if not match: return
    if uid==int(match['challenger_id']): field='challenger_xi'; is_ch=True
    elif uid==int(match['opponent_id']): field='opponent_xi'; is_ch=False
    else:
        await app.answer_callback_query(callback_query['id'],'You are not part of this match.',show_alert=True); return
    expected=match.get('challenger_team_code') if is_ch else match.get('opponent_team_code')
    if code!=expected:
        await app.answer_callback_query(callback_query['id'],'Invalid team.',show_alert=True); return
    selected=set(_decode_ids(match.get(field)))
    players=await _players(code)
    if pid not in {int(p.get('player_id') or 0) for p in players}:
        await app.answer_callback_query(callback_query['id'],'Player not found.',show_alert=True); return
    if pid in selected: selected.remove(pid); msg='Player removed.'
    elif len(selected)>=11: await app.answer_callback_query(callback_query['id'],'You can select maximum 11 players.',show_alert=True); return
    else: selected.add(pid); msg='Player added.'
    await set_xi(mid,uid,sorted(selected)); await app.answer_callback_query(callback_query['id'],msg)
    await app.edit_message_text(callback_query['message']['chat']['id'],callback_query['message']['message_id'],_build_text(code,players,selected),parse_mode='HTML',reply_markup=xi_keyboard(mid,code,players,selected,is_ch))

@register_callback('playint_xi_confirm')
async def playint_xi_confirm(callback_query):
    _,mid_s,code=callback_query['data'].split(':'); mid=int(mid_s); uid=int(callback_query['from']['id']); match=await get_match(mid)
    if not match: return
    is_ch=uid==int(match['challenger_id'])
    if not is_ch and uid!=int(match['opponent_id']):
        await app.answer_callback_query(callback_query['id'],'You are not part of this match.',show_alert=True); return
    field='challenger_xi' if is_ch else 'opponent_xi'; selected=set(_decode_ids(match.get(field))); players=await _players(code); chosen=[p for p in players if int(p.get('player_id') or 0) in selected]
    if not _valid(chosen):
        await app.answer_callback_query(callback_query['id'],'Team is invalid. Complete the required role limits first.',show_alert=True); return
    await set_xi_confirmed(mid,uid); await app.answer_callback_query(callback_query['id'],'Playing XI confirmed!')
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
           f"{team_label(code).split(' ',1)[0]} <b>{player_mention} :{team.upper()}</b>\n\n"
           "<blockquote><b>🏏 PLAYING XI</b>\n"+"\n".join(f"{i+1}. {p.get('name')} • OVR {max(int(p.get('bat_level') or 0),int(p.get('bowl_level') or 0))}" for i,p in enumerate(chosen))+"</blockquote>\n\n"
           "<blockquote><b>🪑 BENCH / SUBSTITUTES</b>\n"+"\n".join(f"• {p.get('name')} • OVR {max(int(p.get('bat_level') or 0),int(p.get('bowl_level') or 0))}" for p in bench)+"</blockquote>\n\n🔒 <b>Playing XI Locked</b>\n\n╰━━━━━━━━━━━━━━━━━━╯")
    await app.send_message(callback_query['message']['chat']['id'],final,parse_mode='HTML')
    m=await get_match(mid)
    if m.get('challenger_xi_confirmed') and m.get('opponent_xi_confirmed'):
        from .pitch import send_pitch_selection
        await send_pitch_selection(callback_query['message']['chat']['id'],m)
