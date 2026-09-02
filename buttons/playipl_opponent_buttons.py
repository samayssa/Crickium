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

def _build(match_id, team_code, player_list, selected_ids, button_style):
    selected = set(int(x) for x in (selected_ids or []))
    rows=[[_b('▶️ Play with your last Playing 11',f'playipl_recent_xi:{match_id}:{team_code}',button_style)]]
    for i in range(0,len(player_list),2):
        row=[]
        for p in player_list[i:i+2]:
            pid=int(p.get('player_id') or 0)
            name=p.get('name','Player')
            ovr=max(int(p.get('bat_level') or 0),int(p.get('bowl_level') or 0))
            mark='✅ ' if pid in selected else ''
            style='success' if pid in selected else button_style
            row.append(_b(f'{mark}{name} • {ovr}', f'playipl_xi:{match_id}:{team_code}:{pid}', style))
        rows.append(row)
    rows.append([_b('✅ CONFIRM PLAYING XI',f'playipl_xi_confirm:{match_id}:{team_code}',button_style)])
    return InlineKeyboardMarkup(rows)

def opponent_xi_keyboard(match_id, team_code, player_list, selected_ids):
    return _build(match_id, team_code, player_list, selected_ids, 'danger')
