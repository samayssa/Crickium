from __future__ import annotations
import inspect
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
try:
    from pyrogram.enums import ButtonStyle
except Exception:
    ButtonStyle = None

_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
_SUPPORTS_STYLE = 'style' in _PARAMS
_SUPPORTS_ICON = 'icon_custom_emoji_id' in _PARAMS
from utils.PremiumEmoji import country_flag_emoji_id
from utils.country_flags import flag_for

_HINT = {'success':'🟢','danger':'🔴','primary':'🔵'}

def _style_value(style):
    if ButtonStyle is None:
        return style
    return {
        'primary': ButtonStyle.PRIMARY,
        'success': ButtonStyle.SUCCESS,
        'danger': ButtonStyle.DANGER,
    }.get(str(style).lower(), ButtonStyle.DEFAULT)


def _b(text, data, style='primary', icon_custom_emoji_id=None):
    if _SUPPORTS_ICON and icon_custom_emoji_id:
        kwargs = {'callback_data': data, 'icon_custom_emoji_id': str(icon_custom_emoji_id)}
        if _SUPPORTS_STYLE:
            kwargs['style'] = _style_value(style)
        return InlineKeyboardButton(text, **kwargs)
    if _SUPPORTS_STYLE:
        return InlineKeyboardButton(text, callback_data=data, style=_style_value(style))
    return InlineKeyboardButton(f"{_HINT.get(style,'')} {text}".strip(), callback_data=data)


def short_player_name(name):
    parts = str(name or 'Player').split()
    if len(parts) < 2:
        return parts[0] if parts else 'Player'
    return f"{parts[0][0].upper()}. {' '.join(parts[1:])}"


_ROLE_EMOJI = {'Batsman': '🏏', 'Bowler': '⚡', 'AllRounder': '🔄', 'Wicketkeeper': '🧤'}


def _build(match_id, team_code, player_list, selected_ids, button_style):
    selected = set(int(x) for x in (selected_ids or []))
    rows=[[_b('▶️ Play with your last Playing 11',f'playilt_recent_xi:{match_id}:{team_code}',button_style)]]
    for i in range(0,len(player_list),2):
        row=[]
        for p in player_list[i:i+2]:
            pid=int(p.get('player_id') or 0)
            name=short_player_name(p.get('name','Player'))
            role_icon=_ROLE_EMOJI.get(str(p.get('role') or ''),'🏏')
            ovr=max(int(p.get('bat_level') or 0),int(p.get('bowl_level') or 0))
            mark='✅ ' if pid in selected else ''
            style='success' if pid in selected else button_style
            flag_id=country_flag_emoji_id(p.get('country'))
            if flag_id and _SUPPORTS_ICON:
                label=f'{mark}{role_icon} {name} • {ovr}'
            else:
                label=f"{mark}{role_icon} {flag_for(p.get('country'))} {name} • {ovr}"
                flag_id=None
            row.append(_b(label, f'playilt_xi:{match_id}:{team_code}:{pid}', style, icon_custom_emoji_id=flag_id))
        rows.append(row)
    rows.append([_b('✅ CONFIRM PLAYING XI',f'playilt_xi_confirm:{match_id}:{team_code}',button_style)])
    return InlineKeyboardMarkup(rows)

def opponent_xi_keyboard(match_id, team_code, player_list, selected_ids):
    return _build(match_id, team_code, player_list, selected_ids, 'danger')
