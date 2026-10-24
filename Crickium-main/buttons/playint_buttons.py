from __future__ import annotations
import inspect
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
try:
    from pyrogram.enums import ButtonStyle
except Exception:
    ButtonStyle = None

_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
_SUPPORTS_STYLE = 'style' in _PARAMS
_HINT = {'success':'🟢','danger':'🔴','primary':'🔵'}

def _style_value(style):
    if ButtonStyle is None:
        return style
    return {
        'primary': ButtonStyle.PRIMARY,
        'success': ButtonStyle.SUCCESS,
        'danger': ButtonStyle.DANGER,
    }.get(str(style).lower(), ButtonStyle.DEFAULT)


def _b(text, data, style='primary'):
    if _SUPPORTS_STYLE:
        return InlineKeyboardButton(text, callback_data=data, style=_style_value(style))
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

def bowler_selection_keyboard(match_id,bowlers,auto_enabled=False):
    rows=[]
    for p in bowlers:
        pid=int(p.get('player_id') or 0); lvl=int(p.get('bowl_level') or 0); left=p.get('_overs_left',0)
        rows.append([_b(f'🥎 {p.get("name","Bowler")} • {lvl} • Left {left} Ov',f'playint_bowler:{match_id}:{pid}','danger')])
    rows.append(runtime_bowler_actions(match_id, auto_enabled))
    return InlineKeyboardMarkup(rows)

def bowler_tactic_keyboard(match_id,bowler=None,auto_enabled=False):
    style=str((bowler or {}).get('bowling_hand') or '').strip().upper()
    if style in {'RAO','LAO'} or 'OFF BREAK' in style or 'OFFSPIN' in style:
        pairs=[('🌀 OFF BREAK BALL','off_break'),('🔄 DOOSRA BALL','doosra'),('➡️ ARM BALL','arm_ball'),('🎯 CARROM BALL','carrom_ball'),('⬆️ TOP SPIN BALL','top_spin')]
    elif style in {'RAL','LAL'} or 'LEG SPIN' in style or 'LEGSPIN' in style:
        pairs=[('🌀 LEG BREAKER BALL','leg_breaker'),('⬆️ TOP SPINNER BALL','top_spinner'),('↔️ SLIDER BALL','slider'),('💨 FLIPPER BALL','flipper'),('🔀 GOOGLY BALL','googly_ball')]
    else:
        pairs=[('🛡️ DEFENSIVE','defensive'),('🌀 SWINGING','swinging'),('⚡ PACE UP','pace_up'),('📏 BACK OF LENGTH','back_of_length'),('🎯 VARIATION','variation')]
    rows=[[_b(lbl,f'playint_tactic:{match_id}:{val}','success')] for lbl,val in pairs]
    rows.append(runtime_bowler_actions(match_id, auto_enabled))
    return InlineKeyboardMarkup(rows)

def strategy_keyboard(match_id,auto_enabled=False):
    vals=[('🛡️ DEFENSIVE','defensive'),('🔄 ROTATE','rotate'),('⚖️ NEUTRAL','neutral'),('⚔️ AGGRESSIVE','aggressive'),('🚀 ULTRA AGGRESSIVE','ultra_aggressive')]
    rows=[[_b(lbl,f'playint_strategy:{match_id}:{val}','primary')] for lbl,val in vals]
    rows.append(runtime_batting_actions(match_id, auto_enabled))
    return InlineKeyboardMarkup(rows)


def exit_confirm_keyboard(match_id):
    return InlineKeyboardMarkup([[_b("✅ Yes, I want", f"playint_exit_yes:{match_id}", "success"), _b("❌ Cancel", f"playint_exit_cancel:{match_id}", "danger")]])


def runtime_bowler_actions(match_id, auto_enabled=False):
    if auto_enabled:
        return [_b('⏹ OFF AUTO BOWLER', f'playint_auto_bowler_off:{match_id}', 'danger')]
    return [_b('✅ SET NEXT BOWLER', f'playint_set_next_bowler:{match_id}', 'success')]


def runtime_batting_actions(match_id, auto_enabled=False):
    if auto_enabled:
        return [_b('⏹ OFF AUTO PLAY', f'playint_auto_batsman_off:{match_id}', 'danger')]
    return [_b('✅ SET NEXT BATSMAN', f'playint_set_next_batsman:{match_id}', 'success')]


def schedule_bowler_keyboard(match_id, bowlers, selected_id=None, auto_enabled=False):
    rows = []
    for player in bowlers:
        pid = int(player.get('player_id') or 0)
        lvl = int(player.get('bowl_level') or 0)
        left = int(player.get('_overs_left') or 0)
        style = 'success' if selected_id is not None and pid == int(selected_id) else 'danger'
        rows.append([_b(f'🥎 {player.get("name", "Bowler")} • {lvl} • Left {left} Ov', f'playint_schedule_bowler:{match_id}:{pid}', style)])
    if selected_id is not None:
        rows.append([_b('✅ CONFIRM NEXT BOWLER', f'playint_confirm_next_bowler:{match_id}', 'success')])
    rows.append([_b('▶️ START AUTO PLAY', f'playint_start_auto_bowler:{match_id}', 'success')])
    rows.append(runtime_bowler_actions(match_id, auto_enabled))
    return InlineKeyboardMarkup(rows)


def schedule_batsman_keyboard(match_id, players, selected_ids=None):
    selected = {int(x) for x in (selected_ids or [])}
    rows = []
    pair = []
    for player in players:
        pid = int(player.get('player_id') or 0)
        label = f"#{int(player.get('position') or 0)} • {player.get('name', 'Batter')}"
        style = 'success' if pid in selected else 'primary'
        pair.append(_b(label, f'playint_schedule_batsman:{match_id}:{pid}', style))
        if len(pair) == 2:
            rows.append(pair); pair = []
    if pair:
        rows.append(pair)
    if selected:
        rows.append([_b('✅ CONFIRM BATSMAN ORDER', f'playint_confirm_batsman:{match_id}', 'success')])
    rows.append([_b('❌ CANCEL', f'playint_cancel_batsman_schedule:{match_id}', 'danger')])
    return InlineKeyboardMarkup(rows)

