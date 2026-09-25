from __future__ import annotations
import inspect
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
try:
    from pyrogram.enums import ButtonStyle
except Exception:
    ButtonStyle = None

from utils.PremiumEmoji import get_ipl_team_emoji

_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
_SUPPORTS_STYLE = 'style' in _PARAMS
_SUPPORTS_ICON = 'icon_custom_emoji_id' in _PARAMS
_HINT = {'success': '🟢', 'danger': '🔴', 'primary': '🔵'}


def _style_value(style):
    if ButtonStyle is None:
        return style
    return {
        'primary': ButtonStyle.PRIMARY,
        'success': ButtonStyle.SUCCESS,
        'danger': ButtonStyle.DANGER,
    }.get(str(style).lower(), ButtonStyle.DEFAULT)


def _b(text, data, style='primary', icon_custom_emoji_id=None, fallback_text=None):
    """Build a button using Telegram's real button style enum when available."""
    if _SUPPORTS_ICON and icon_custom_emoji_id:
        kwargs = {'callback_data': data, 'icon_custom_emoji_id': str(icon_custom_emoji_id)}
        if _SUPPORTS_STYLE:
            kwargs['style'] = _style_value(style)
        return InlineKeyboardButton(text, **kwargs)

    label = fallback_text if fallback_text is not None else text
    if _SUPPORTS_STYLE:
        return InlineKeyboardButton(label, callback_data=data, style=_style_value(style))
    return InlineKeyboardButton(f"{_HINT.get(style, '')} {label}".strip(), callback_data=data)


def challenge_keyboard(match_id):
    return InlineKeyboardMarkup([
        [
            _b('✅ Accept Challenge', f'playipl_accept:{match_id}', 'success'),
            _b('❌ Decline', f'playipl_decline:{match_id}', 'danger'),
        ]
    ])


def team_keyboard(match_id):
    from database.playipl_teams_repo import TEAM_ORDER, team_button_label

    # Ten franchises: two compact buttons per row, no pagination.
    # Prefer the configured Telegram custom-emoji logo for each team.
    # If the installed client does not expose icon_custom_emoji_id, the
    # original Unicode team emoji remains the exact fallback.
    rows = []
    for i in range(0, len(TEAM_ORDER), 2):
        row = []
        for code in TEAM_ORDER[i:i + 2]:
            code = str(code).upper()
            custom_id = get_ipl_team_emoji(code)
            fallback_label = team_button_label(code)
            label = code if custom_id and _SUPPORTS_ICON else fallback_label
            row.append(_b(
                label,
                f'playipl_team:{match_id}:{code}',
                'primary',
                icon_custom_emoji_id=custom_id,
                fallback_text=fallback_label,
            ))
        rows.append(row)

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


def bowler_selection_keyboard(match_id, bowlers, selected_id=None, auto_enabled=False, impact_enabled=True):
    rows = []
    for p in bowlers:
        pid = int(p.get('player_id') or 0)
        lvl = int(p.get('bowl_level') or 0)
        left = p.get('_overs_left', 0)
        style = 'success' if selected_id is not None and pid == int(selected_id) else 'danger'
        rows.append([_b(
            f'🥎 {p.get("name", "Bowler")} • {lvl} • Left {left} Ov',
            f'playipl_bowler:{match_id}:{pid}',
            style,
        )])
    rows.append(runtime_bowler_actions(match_id, auto_enabled, impact_enabled))
    return InlineKeyboardMarkup(rows)

def bowler_tactic_keyboard(match_id,bowler=None,auto_enabled=False,impact_enabled=True):
    style = str((bowler or {}).get('bowling_hand') or '').strip().upper()
    if style in {'RAO','LAO'} or 'OFF BREAK' in style or 'OFFSPIN' in style:
        pairs=[
            ('🌀 OFF BREAK BALL','off_break'), ('🔄 DOOSRA BALL','doosra'),
            ('➡️ ARM BALL','arm_ball'), ('🎯 CARROM BALL','carrom_ball'),
            ('⬆️ TOP SPIN BALL','top_spin'),
        ]
    elif style in {'RAL','LAL'} or 'LEG SPIN' in style or 'LEGSPIN' in style:
        pairs=[
            ('🌀 LEG BREAKER BALL','leg_breaker'), ('⬆️ TOP SPINNER BALL','top_spinner'),
            ('↔️ SLIDER BALL','slider'), ('💨 FLIPPER BALL','flipper'), ('🔀 GOOGLY BALL','googly_ball'),
        ]
    else:
        pairs=[
            ('🛡️ DEFENSIVE','defensive'), ('🌀 SWINGING','swinging'),
            ('⚡ PACE UP','pace_up'), ('📏 BACK OF LENGTH','back_of_length'), ('🎯 VARIATION','variation'),
        ]
    rows=[[_b(lbl,f'playipl_tactic:{match_id}:{val}','success')] for lbl,val in pairs]
    rows.append(runtime_bowler_actions(match_id, auto_enabled, impact_enabled))
    return InlineKeyboardMarkup(rows)

def strategy_keyboard(match_id, auto_enabled=False, impact_enabled=True):
    vals=[
        ('🛡️ DEFENSIVE','defensive'), ('🔄 ROTATE','rotate'),
        ('⚖️ NEUTRAL','neutral'), ('⚔️ AGGRESSIVE','aggressive'),
        ('🚀 ULTRA AGGRESSIVE','ultra_aggressive'),
    ]
    rows=[[_b(lbl,f'playipl_strategy:{match_id}:{val}','primary')] for lbl,val in vals]
    rows.append(runtime_batting_actions(match_id, auto_enabled, impact_enabled))
    return InlineKeyboardMarkup(rows)


def exit_confirm_keyboard(match_id):
    return InlineKeyboardMarkup([[
        _b('✅ Yes, Exit Game', f'playipl_exit_yes:{match_id}', 'success'),
        _b('❌ No, Cancel', f'playipl_exit_cancel:{match_id}', 'danger'),
    ]])


def runtime_bowler_actions(match_id, auto_enabled=False, impact_enabled=True):
    first = _b('⏹ OFF AUTO BOWLER' if auto_enabled else '✅ SET NEXT BOWLER', f'playipl_auto_bowler_off:{match_id}' if auto_enabled else f'playipl_set_next_bowler:{match_id}', 'danger' if auto_enabled else 'success')
    return [first, _b('⚡ IMPACT PLAYER', f'playipl_impact_runtime:{match_id}', 'danger')] if impact_enabled else [first]


def runtime_batting_actions(match_id, auto_enabled=False, impact_enabled=True):
    first = _b('⏹ OFF AUTO PLAY' if auto_enabled else '✅ SET NEXT BATSMAN', f'playipl_auto_batsman_off:{match_id}' if auto_enabled else f'playipl_set_next_batsman:{match_id}', 'danger' if auto_enabled else 'success')
    return [first, _b('⚡ IMPACT PLAYER', f'playipl_impact_runtime:{match_id}', 'danger')] if impact_enabled else [first]


def schedule_bowler_keyboard(match_id, bowlers, selected_id=None, auto_enabled=False, impact_enabled=True):
    rows = []
    for player in bowlers:
        pid = int(player.get('player_id') or 0)
        lvl = int(player.get('bowl_level') or 0)
        left = int(player.get('_overs_left') or 0)
        style = 'success' if selected_id is not None and pid == int(selected_id) else 'danger'
        rows.append([_b(f'🥎 {player.get("name", "Bowler")} • {lvl} • Left {left} Ov', f'playipl_schedule_bowler:{match_id}:{pid}', style)])
    if selected_id is not None:
        rows.append([_b('✅ CONFIRM NEXT BOWLER', f'playipl_confirm_next_bowler:{match_id}', 'success')])
    rows.append([_b('▶️ START AUTO PLAY', f'playipl_start_auto_bowler:{match_id}', 'success')])
    rows.append(runtime_bowler_actions(match_id, auto_enabled, impact_enabled))
    return InlineKeyboardMarkup(rows)


def schedule_batsman_keyboard(match_id, players, selected_ids=None):
    selected = {int(x) for x in (selected_ids or [])}
    rows = []
    pair = []
    for player in players:
        pid = int(player.get('player_id') or 0)
        label = f"#{int(player.get('position') or 0)} • {player.get('name', 'Batter')}"
        style = 'success' if pid in selected else 'primary'
        pair.append(_b(label, f'playipl_schedule_batsman:{match_id}:{pid}', style))
        if len(pair) == 2:
            rows.append(pair); pair = []
    if pair:
        rows.append(pair)
    if selected:
        rows.append([_b('✅ CONFIRM BATSMAN ORDER', f'playipl_confirm_batsman:{match_id}', 'success')])
    rows.append([_b('❌ CANCEL', f'playipl_cancel_batsman_schedule:{match_id}', 'danger')])
    return InlineKeyboardMarkup(rows)


def impact_player_keyboard(prefix, match_id, team_id, players, selected_id=None, stage='out'):
    rows = []
    pair = []
    action = 'impact_out' if stage == 'out' else 'impact_in'
    for index, player in enumerate(players, start=1):
        pid = int(player.get('player_id') or 0)
        label = f'{index}. {player.get("name", "Player")}'
        style = 'success' if selected_id is not None and pid == int(selected_id) else 'danger'
        pair.append(_b(label, f'{prefix}_{action}:{match_id}:{int(team_id)}:{pid}', style))
        if len(pair) == 2:
            rows.append(pair); pair = []
    if pair:
        rows.append(pair)
    if selected_id is not None:
        label = '✅ CONFIRM OUT PLAYER' if stage == 'out' else '✅ CONFIRM IMPACT PLAYER'
        cb = 'impact_confirm_out' if stage == 'out' else 'impact_confirm_in'
        rows.append([_b(label, f'{prefix}_{cb}:{match_id}:{int(team_id)}', 'danger')])
    return InlineKeyboardMarkup(rows)


def impact_batting_position_keyboard(prefix, match_id, players, selected_position=None):
    rows = []
    pair = []
    for player in players:
        pos = int(player.get('position') or 0)
        style = 'success' if selected_position == pos else 'primary'
        pair.append(_b(f'#{pos} • {player.get("name", "Batter")}', f'{prefix}_impact_batpos:{match_id}:{pos}', style))
        if len(pair) == 2:
            rows.append(pair); pair = []
    if pair:
        rows.append(pair)
    if selected_position is not None:
        rows.append([_b('✅ CONFIRM BATTING POSITION', f'{prefix}_impact_confirm_batpos:{match_id}', 'success')])
    return InlineKeyboardMarkup(rows)


def impact_batting_role_keyboard(prefix, match_id):
    return InlineKeyboardMarkup([
        [_b('🟦 STRIKER', f'{prefix}_impact_role:{match_id}:striker', 'primary'), _b('🟦 NON-STRIKER', f'{prefix}_impact_role:{match_id}:non_striker', 'primary')]
    ])
