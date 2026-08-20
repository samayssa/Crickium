from __future__ import annotations
import inspect
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
_SUPPORTS_STYLE = 'style' in _PARAMS
_HINT = {'success':'🟢','danger':'🔴','primary':'🔵'}

def _b(text, data, style='primary'):
    if _SUPPORTS_STYLE:
        return InlineKeyboardButton(text, callback_data=data, style=style)
    return InlineKeyboardButton(f"{_HINT.get(style,'')} {text}".strip(), callback_data=data)

def challenge_keyboard(match_id):
    return InlineKeyboardMarkup([[_b('✅ Accept Challenge',f'playint_accept:{match_id}','success'),_b('❌ Decline',f'playint_decline:{match_id}','danger')]])

def team_keyboard(match_id, page=1):
    from database.playint_teams_repo import TEAMS_PAGE_1, TEAMS_PAGE_2, team_flag, team_name
    codes=TEAMS_PAGE_1 if int(page)==1 else TEAMS_PAGE_2
    rows=[]
    for i in range(0,len(codes),2):
        row=[]
        for code in codes[i:i+2]:
            row.append(_b(f'{team_flag(code)} {team_name(code)}',f'playint_team:{match_id}:{code}','primary'))
        rows.append(row)
    rows.append([_b('◀️ Previous',f'playint_team_page:{match_id}:prev:{page}','primary'),_b(f'Page {page}/2',f'playint_team_page:{match_id}:noop:{page}','primary'),_b('Next ▶️',f'playint_team_page:{match_id}:next:{page}','primary')])
    return InlineKeyboardMarkup(rows)

def xi_keyboard(match_id, team_code, player_list, selected_ids, is_challenger):
    style='success' if is_challenger else 'danger'
    rows=[]
    for i in range(0,len(player_list),2):
        row=[]
        for p in player_list[i:i+2]:
            pid=int(p.get('player_id') or 0); flag=p.get('_flag') or '🏳️'; name=p.get('name','Player'); ovr=max(int(p.get('bat_level') or 0),int(p.get('bowl_level') or 0))
            mark='✅ ' if pid in selected_ids else ''
            row.append(_b(f'{mark}{flag} {name} • {ovr}',f'playint_xi:{match_id}:{team_code}:{pid}','success' if pid in selected_ids and is_challenger else ('danger' if pid in selected_ids else style)))
        rows.append(row)
    rows.append([_b('✅ CONFIRM PLAYING XI',f'playint_xi_confirm:{match_id}:{team_code}',style)])
    return InlineKeyboardMarkup(rows)

def pitch_keyboard(match_id):
    pairs=[('🌿 GREEN','green'),('🏜️ DRY','dry'),('🌪️ DUSTY','dusty'),('🛣️ FLAT','flat'),('🪨 HARD','hard'),('⚖️ EVEN','even'),('🏀 BOUNCY','bouncy'),('🐢 SLOW','slow')]
    rows=[]
    for i in range(0,len(pairs),2):
        rows.append([_b(pairs[i][0],f'playint_pitch:{match_id}:{pairs[i][1]}','primary'),_b(pairs[i+1][0],f'playint_pitch:{match_id}:{pairs[i+1][1]}','primary')])
    return InlineKeyboardMarkup(rows)

def toss_call_keyboard(match_id):
    return InlineKeyboardMarkup([[_b('🗿 HEADS',f'playint_toss_call:{match_id}:heads','primary'),_b('🦅 TAILS',f'playint_toss_call:{match_id}:tails','primary')]])

def decision_keyboard(match_id):
    return InlineKeyboardMarkup([[_b('🏏 BAT',f'playint_decision:{match_id}:bat','primary'),_b('🎯 BOWL',f'playint_decision:{match_id}:bowl','primary')]])

def bowler_selection_keyboard(match_id,bowlers):
    rows=[]
    for p in bowlers:
        pid=int(p.get('player_id') or 0); lvl=int(p.get('bowl_level') or 0); left=p.get('_overs_left',0)
        rows.append([_b(f'🥎 {p.get("name","Bowler")} • {lvl} • Left {left} Ov',f'playint_bowler:{match_id}:{pid}','danger')])
    return InlineKeyboardMarkup(rows)

def bowler_tactic_keyboard(match_id,bowler=None):
    style=str((bowler or {}).get('bowling_hand') or '').strip().upper()
    if style in {'RAO','LAO'} or 'OFF BREAK' in style or 'OFFSPIN' in style:
        pairs=[('🌀 OFF BREAK BALL','off_break'),('🔄 DOOSRA BALL','doosra'),('➡️ ARM BALL','arm_ball'),('🎯 CARROM BALL','carrom_ball'),('⬆️ TOP SPIN BALL','top_spin')]
    elif style in {'RAL','LAL'} or 'LEG SPIN' in style or 'LEGSPIN' in style:
        pairs=[('🌀 LEG BREAKER BALL','leg_breaker'),('⬆️ TOP SPINNER BALL','top_spinner'),('↔️ SLIDER BALL','slider'),('💨 FLIPPER BALL','flipper'),('🔀 GOOGLY BALL','googly_ball')]
    else:
        pairs=[('🛡️ DEFENSIVE','defensive'),('🌀 SWINGING','swinging'),('⚡ PACE UP','pace_up'),('📏 BACK OF LENGTH','back_of_length'),('🎯 VARIATION','variation')]
    return InlineKeyboardMarkup([[_b(lbl,f'playint_tactic:{match_id}:{val}','success')] for lbl,val in pairs])

def strategy_keyboard(match_id):
    vals=[('🛡️ DEFENSIVE','defensive'),('🔄 ROTATE','rotate'),('⚖️ NEUTRAL','neutral'),('⚔️ AGGRESSIVE','aggressive'),('🚀 ULTRA AGGRESSIVE','ultra_aggressive')]
    return InlineKeyboardMarkup([[_b(lbl,f'playint_strategy:{match_id}:{val}','primary')] for lbl,val in vals])
