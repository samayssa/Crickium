from __future__ import annotations

import inspect
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_BUTTON_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
SUPPORTS_BUTTON_STYLE = "style" in _BUTTON_PARAMS
_FALLBACK_HINT = {"success": "🟢", "danger": "🔴", "primary": "🔵"}


def _styled(text: str, callback: str, style: str) -> InlineKeyboardButton:
    if SUPPORTS_BUTTON_STYLE:
        return InlineKeyboardButton(text, callback_data=callback, style=style)
    return InlineKeyboardButton(f"{_FALLBACK_HINT.get(style, '')} {text}".strip(), callback_data=callback)


def challenge_keyboard(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[\
        _styled("✅ ACCEPT", f"playso_accept:{match_id}", "success"),
        _styled("❌ DECLINE", f"playso_decline:{match_id}", "danger"),
    ]])


def pitch_keyboard(match_id: int) -> InlineKeyboardMarkup:
    pairs = [("🌿 GREEN", "green"), ("🏜️ DRY", "dry"), ("🌪️ DUSTY", "dusty"), ("🛣️ FLAT", "flat"),
             ("🪨 HARD", "hard"), ("⚖️ EVEN", "even"), ("🏀 BOUNCY", "bouncy"), ("🐢 SLOW", "slow")]
    rows = []
    for i in range(0, len(pairs), 2):
        row = [_styled(pairs[i][0], f"playso_pitch:{match_id}:{pairs[i][1]}", "primary")]
        if i + 1 < len(pairs):
            row.append(_styled(pairs[i + 1][0], f"playso_pitch:{match_id}:{pairs[i + 1][1]}", "primary"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def toss_keyboard(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        _styled("🗿 HEADS", f"playso_toss:{match_id}:heads", "primary"),
        _styled("🦅 TAILS", f"playso_toss:{match_id}:tails", "primary"),
    ]])


def decision_keyboard(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        _styled("🏏 BAT", f"playso_decision:{match_id}:bat", "primary"),
        _styled("🎯 BOWL", f"playso_decision:{match_id}:bowl", "primary"),
    ]])


def _role_emoji(role: str) -> str:
    return {"Batsman": "🏏", "Wicketkeeper": "🧤", "AllRounder": "🔄", "Bowler": "🎯"}.get(role, "🏏")


def _player_label(player: dict, selected: bool = False, slot: int | None = None) -> str:
    marker = f"{slot}. " if slot else ("✅ " if selected else "")
    return f"{marker}{_role_emoji(str(player.get('role') or ''))} {player.get('name', 'Player')} • OVR {int(player.get('bat_level') or 0)} / {int(player.get('bowl_level') or 0)}"


def bowler_selection_keyboard(match_id: int, players: list[dict], selected_id: int | None, locked_id: int | None) -> InlineKeyboardMarkup:
    rows = []
    for p in players:
        pid = int(p.get("player_id") or 0)
        role = str(p.get("role") or "")
        eligible = role in {"Bowler", "AllRounder"}
        locked = locked_id is not None and pid == int(locked_id)
        style = "danger" if (locked or not eligible) else "success"
        if locked:
            label = f"🚫 {p.get('name', 'Player')} • OVR {int(p.get('bowl_level') or 0)}"
        else:
            label = f"{_role_emoji(role)} {p.get('name', 'Player')} • OVR {int(p.get('bowl_level') or 0)}"
            if selected_id is not None and pid == int(selected_id):
                label = f"✅ {label}"
        rows.append([_styled(label, f"playso_bowler:{match_id}:{pid}", style)])
    if selected_id is not None:
        rows.append([_styled("✅ CONFIRM BOWLER", f"playso_bowler_confirm:{match_id}", "success")])
    return InlineKeyboardMarkup(rows)


def batter_selection_keyboard(match_id: int, players: list[dict], selected_ids: list[int]) -> InlineKeyboardMarkup:
    selected_map = {int(pid): i + 1 for i, pid in enumerate(selected_ids)}
    rows = []
    for p in players:
        pid = int(p.get("player_id") or 0)
        slot = selected_map.get(pid)
        rows.append([_styled(_player_label(p, selected=slot is not None, slot=slot), f"playso_batter:{match_id}:{pid}", "primary")])
    if len(selected_ids) == 3:
        rows.append([_styled("✅ CONFIRM BATTERS", f"playso_batter_confirm:{match_id}", "success")])
    return InlineKeyboardMarkup(rows)


def length_keyboard(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_styled("🎯 FULL OF LENGTH", f"playso_length:{match_id}:full", "danger")],
        [_styled("📏 BACK OF LENGTH", f"playso_length:{match_id}:back", "danger")],
        [_styled("🔴 SHORT OF LENGTH", f"playso_length:{match_id}:short", "danger")],
    ])


def delivery_keyboard(match_id: int, deliveries: list[str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_styled(d, f"playso_delivery:{match_id}:{i}", "danger")]
        for i, d in enumerate(deliveries)
    ])


def line_keyboard(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_styled("◀️ WIDE OF OFF STUMP", f"playso_line:{match_id}:wide_off", "danger")],
        [_styled("🎯 MIDDLE-OFF", f"playso_line:{match_id}:middle_off", "danger")],
        [_styled("🎯 MIDDLE STUMP", f"playso_line:{match_id}:middle", "danger")],
        [_styled("▶️ LEG STUMP", f"playso_line:{match_id}:leg", "danger")],
    ])


def foot_keyboard(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_styled("⬆️ FRONT FOOT", f"playso_foot:{match_id}:front", "primary")],
        [_styled("⬅️ BACK FOOT", f"playso_foot:{match_id}:back", "primary")],
        [_styled("🚶 ADVANCED STEP OUT", f"playso_foot:{match_id}:advance", "primary")],
    ])


def intent_keyboard(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        _styled("🟢 GROUNDED", f"playso_intent:{match_id}:grounded", "primary"),
        _styled("🔴 LOFTED", f"playso_intent:{match_id}:lofted", "primary"),
    ]])


def shot_keyboard(match_id: int, shots: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(shots), 2):
        row = [_styled(shots[i], f"playso_shot:{match_id}:{i}", "primary")]
        if i + 1 < len(shots):
            row.append(_styled(shots[i + 1], f"playso_shot:{match_id}:{i+1}", "primary"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def exit_confirm_keyboard(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        _styled("✅ Yes, I want", f"playso_exit_yes:{match_id}", "success"),
        _styled("❌ Cancel", f"playso_exit_cancel:{match_id}", "danger"),
    ]])
