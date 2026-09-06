from __future__ import annotations

import inspect
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_BUTTON_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
SUPPORTS_BUTTON_STYLE = "style" in _BUTTON_PARAMS
_FALLBACK = {"success": "🟢", "danger": "🔴", "primary": "🔵"}

def _b(text: str, data: str, style: str = "primary") -> InlineKeyboardButton:
    if SUPPORTS_BUTTON_STYLE:
        return InlineKeyboardButton(text, callback_data=data, style=style)
    return InlineKeyboardButton(f"{_FALLBACK.get(style, '')} {text}".strip(), callback_data=data)

def squad_player_keyboard(trade_id: int, players: list[dict], stage: str) -> InlineKeyboardMarkup:
    rows = []
    for p in players:
        pid = int(p.get("player_id") or 0)
        label = f"🏏 {p.get('name') or 'Player'} • OVR {max(int(p.get('bat_level') or 0), int(p.get('bowl_level') or 0))}"
        rows.append([_b(label, f"trade_select:{trade_id}:{stage}:{pid}", "danger")])
    return InlineKeyboardMarkup(rows)

def sender_confirm_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_b("✅ Yes, I want", f"trade_sender_yes:{trade_id}", "success"), _b("❌ Cancel", f"trade_sender_cancel:{trade_id}", "danger")],
        [_b("⬅️ Back", f"trade_sender_back:{trade_id}", "primary")],
    ])

def recipient_confirm_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_b("✅ Yes, Confirm", f"trade_recipient_yes:{trade_id}", "success"), _b("❌ Cancel", f"trade_recipient_cancel:{trade_id}", "danger")],
        [_b("⬅️ Back", f"trade_recipient_back:{trade_id}", "primary")],
    ])

