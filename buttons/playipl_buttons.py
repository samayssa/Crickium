from __future__ import annotations
import inspect
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
_SUPPORTS_STYLE = 'style' in _PARAMS
_HINT = {'success': '🟢', 'danger': '🔴', 'primary': '🔵'}


def _b(text, data, style='primary'):
    if _SUPPORTS_STYLE:
        return InlineKeyboardButton(text, callback_data=data, style=style)
    return InlineKeyboardButton(f"{_HINT.get(style, '')} {text}".strip(), callback_data=data)


def challenge_keyboard(match_id):
    return InlineKeyboardMarkup([
        [
            _b('✅ Accept Challenge', f'playipl_accept:{match_id}', 'success'),
            _b('❌ Decline', f'playipl_decline:{match_id}', 'danger'),
        ]
    ])


def team_keyboard(match_id):
    from database.playipl_teams_repo import TEAM_ORDER, team_button_label
    # Ten franchises: five compact buttons per row, no pagination.
    rows = []
    for i in range(0, len(TEAM_ORDER), 5):
        rows.append([
            _b(team_button_label(code), f'playipl_team:{match_id}:{code}', 'primary')
            for code in TEAM_ORDER[i:i + 5]
        ])
    return InlineKeyboardMarkup(rows)


def pitch_keyboard(match_id):
    pairs = [
        ('🌿 GREEN', 'green'), ('🏜️ DRY', 'dry'),
        ('🌪️ DUSTY', 'dusty'), ('🛣️ FLAT', 'flat'),
        ('🪨 HARD', 'hard'), ('⚖️ EVEN', 'even'),
        ('🏀 BOUNCY', 'bouncy'), ('🐢 SLOW', 'slow'),
    ]
    rows = []
    for i in range(0, len(pairs), 2):
        rows.append([
            _b(pairs[i][0], f'playipl_pitch:{match_id}:{pairs[i][1]}', 'primary'),
            _b(pairs[i + 1][0], f'playipl_pitch:{match_id}:{pairs[i + 1][1]}', 'primary'),
        ])
    return InlineKeyboardMarkup(rows)


def toss_call_keyboard(match_id):
    return InlineKeyboardMarkup([[
        _b('🗿 HEADS', f'playipl_toss_call:{match_id}:heads', 'primary'),
        _b('🦅 TAILS', f'playipl_toss_call:{match_id}:tails', 'primary'),
    ]])


def decision_keyboard(match_id):
    return InlineKeyboardMarkup([[
        _b('🏏 BAT', f'playipl_decision:{match_id}:bat', 'primary'),
        _b('🎯 BOWL', f'playipl_decision:{match_id}:bowl', 'primary'),
    ]])


def bowler_selection_keyboard(match_id, bowlers):
    rows = []
    for p in bowlers:
        pid = int(p.get('player_id') or 0)
        lvl = int(p.get('bowl_level') or 0)
        left = p.get('_overs_left', 0)
        rows.append([_b(
            f'🥎 {p.get("name", "Bowler")} • {lvl} • Left {left} Ov',
            f'playipl_bowler:{match_id}:{pid}',
            'danger',
        )])
    return InlineKeyboardMarkup(rows)


def bowler_tactic_keyboard(match_id, bowler=None):
    style = str((bowler or {}).get('bowling_hand') or '').strip().upper()
    if style in {'RAO', 'LAO'} or 'OFF BREAK' in style or 'OFFSPIN' in style:
        pairs = [
            ('🌀 OFF BREAK BALL', 'off_break'), ('🔄 DOOSRA BALL', 'doosra'),
            ('➡️ ARM BALL', 'arm_ball'), ('🎯 CARROM BALL', 'carrom_ball'),
            ('⬆️ TOP SPIN BALL', 'top_spin'),
        ]
    elif style in {'RAL', 'LAL'} or 'LEG SPIN' in style or 'LEGSPIN' in style:
        pairs = [
            ('🌀 LEG BREAKER BALL', 'leg_breaker'), ('⬆️ TOP SPINNER BALL', 'top_spinner'),
            ('↔️ SLIDER BALL', 'slider'), ('💨 FLIPPER BALL', 'flipper'),
            ('🔀 GOOGLY BALL', 'googly_ball'),
        ]
    else:
        pairs = [
            ('🛡️ DEFENSIVE', 'defensive'), ('🌀 SWINGING', 'swinging'),
            ('⚡ PACE UP', 'pace_up'), ('📏 BACK OF LENGTH', 'back_of_length'),
            ('🎯 VARIATION', 'variation'),
        ]
    return InlineKeyboardMarkup([
        [_b(lbl, f'playipl_tactic:{match_id}:{val}', 'success')]
        for lbl, val in pairs
    ])


def strategy_keyboard(match_id):
    vals = [
        ('🛡️ DEFENSIVE', 'defensive'), ('🔄 ROTATE', 'rotate'),
        ('⚖️ NEUTRAL', 'neutral'), ('⚔️ AGGRESSIVE', 'aggressive'),
        ('🚀 ULTRA AGGRESSIVE', 'ultra_aggressive'),
    ]
    return InlineKeyboardMarkup([
        [_b(lbl, f'playipl_strategy:{match_id}:{val}', 'primary')]
        for lbl, val in vals
    ])


def impact_out_keyboard(match_id, team_code, players, selected_id=None):
    rows = []
    for index, player in enumerate(players, start=1):
        pid = int(player.get('player_id') or 0)
        name = str(player.get('name') or 'Player')
        style = 'success' if pid == int(selected_id or -1) else 'danger'
        rows.append([_b(
            f'{index}. {name}',
            f'playipl_impact_out:{match_id}:{team_code}:{pid}',
            style,
        )])
    if selected_id is not None:
        rows.append([_b(
            '✅ Confirm Out Player',
            f'playipl_impact_confirm_out:{match_id}:{team_code}',
            'danger',
        )])
    return rows


def impact_in_keyboard(match_id, team_code, players, selected_id=None):
    rows = []
    for player in players:
        pid = int(player.get('player_id') or 0)
        name = str(player.get('name') or 'Player')
        style = 'success' if pid == int(selected_id or -1) else 'danger'
        rows.append([_b(
            name,
            f'playipl_impact_in:{match_id}:{team_code}:{pid}',
            style,
        )])
    if selected_id is not None:
        rows.append([_b(
            '✅ Confirm In Player',
            f'playipl_impact_confirm_in:{match_id}:{team_code}',
            'danger',
        )])
    return rows
