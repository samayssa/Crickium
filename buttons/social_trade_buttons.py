from __future__ import annotations

import inspect
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_BUTTON_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
SUPPORTS_BUTTON_STYLE = "style" in _BUTTON_PARAMS
_FALLBACK = {"success": "🟢", "danger": "🔴", "primary": "🔵"}


def _b(text: str, data: str, style: str = "primary") -> InlineKeyboardButton:
    # Pyrogram/Telegram versions differ on native button-style support.  Keep
    # callback_data identical in both paths so the trade callback dispatcher
    # can never depend on a visual-only feature.
    if SUPPORTS_BUTTON_STYLE:
        try:
            return InlineKeyboardButton(text, callback_data=data, style=style)
        except TypeError:
            pass
    return InlineKeyboardButton(f"{_FALLBACK.get(style, '')} {text}".strip(), callback_data=data)


def _player_label(player: dict) -> str:
    name = str(player.get("name") or "Player")
    try:
        ovr = max(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))
    except (TypeError, ValueError):
        ovr = 0
    return f"🏏 {name} • OVR {ovr}"


def squad_player_keyboard(trade_id: int, players: list[dict], stage: str, *, confirm: bool = False) -> InlineKeyboardMarkup:
    """One full-width player per row.

    Callback payloads intentionally stay short and numeric.  `stage` is only
    's' (sender) or 'r' (recipient), avoiding accidental parsing ambiguity.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for player in players:
        pid = int(player.get("player_id") or 0)
        rows.append([_b(_player_label(player), f"trade_pick:{int(trade_id)}:{stage}:{pid}", "danger")])
    if confirm:
        rows.append([_b("✅ Confirm Player", f"trade_confirm_pick:{int(trade_id)}:{stage}", "success")])
    return InlineKeyboardMarkup(rows)



def selected_player_keyboard(trade_id: int, stage: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_b("✅ Confirm Player", f"trade_confirm_pick:{int(trade_id)}:{stage}", "success")],
        [_b("⬅️ Back", f"trade_sender_back:{int(trade_id)}" if stage == "s" else f"trade_recipient_back:{int(trade_id)}", "primary")],
    ])

def confirm_selected_player_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            _b("✅ Yes, go ahead", f"trade_sender_yes:{int(trade_id)}", "success"),
            _b("❌ Cancel", f"trade_sender_cancel:{int(trade_id)}", "danger"),
        ],
        [_b("⬅️ Back", f"trade_sender_back:{int(trade_id)}", "primary")],
    ])


def direct_offer_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            _b("✅ Yes, go ahead", f"trade_sender_yes:{int(trade_id)}", "success"),
            _b("❌ Cancel", f"trade_sender_cancel:{int(trade_id)}", "danger"),
        ],
    ])


def recipient_request_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            _b("✅ Accept Trade", f"trade_recipient_accept:{int(trade_id)}", "success"),
            _b("❌ Cancel", f"trade_recipient_cancel:{int(trade_id)}", "danger"),
        ],
    ])


def recipient_confirm_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            _b("✅ Yes, Confirm", f"trade_recipient_yes:{int(trade_id)}", "success"),
            _b("❌ Cancel", f"trade_recipient_cancel:{int(trade_id)}", "danger"),
        ],
        [_b("⬅️ Back", f"trade_recipient_back:{int(trade_id)}", "primary")],
    ])
