"""Inline keyboards for the player-upgrade system."""
from __future__ import annotations

import inspect
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
_HAS_STYLE = "style" in _PARAMS
_FALLBACK = {"success": "🟢", "danger": "🔴", "primary": "🔵"}


def styled(text: str, data: str, style: str) -> InlineKeyboardButton:
    if _HAS_STYLE:
        return InlineKeyboardButton(text, callback_data=data, style=style)
    return InlineKeyboardButton(f"{_FALLBACK.get(style, '')} {text}".strip(), callback_data=data)


def shop_filters(filter_name: str, page: int, total_pages: int, user_id: int) -> InlineKeyboardMarkup:
    rows = [[
        styled("🏏 BATTING", f"upgrade_filter:batting:{page}:{int(user_id)}", "success"),
        styled("🎯 BOWLING", f"upgrade_filter:bowling:{page}:{int(user_id)}", "danger"),
    ]]
    if total_pages > 1:
        previous_page = max(0, page - 1)
        next_page = min(total_pages - 1, page + 1)
        rows.append([
            styled("◀ PREVIOUS", f"upgrade_page:{filter_name}:{previous_page}:{int(user_id)}", "primary"),
            styled("NEXT ▶", f"upgrade_page:{filter_name}:{next_page}:{int(user_id)}", "primary"),
        ])
    return InlineKeyboardMarkup(rows)


def category_upgrade_buttons(items: list[dict], category: str, page: int, total_pages: int, token: str, token_user_id: int) -> InlineKeyboardMarkup:
    rows = [[
        styled("🏏 BATTING", f"ubuy_filter:batting:0:{int(token_user_id)}", "success"),
        styled("🎯 BOWLING", f"ubuy_filter:bowling:0:{int(token_user_id)}", "danger"),
    ]]
    for item in items:
        label = f"{item['name']} • {item['price']:,} 💎"
        rows.append([styled(label, f"ubuy_select:{token}:{item['upgrade_key']}:{item['tier']}", "danger")])
    if total_pages > 1:
        previous_page = max(0, page - 1)
        next_page = min(total_pages - 1, page + 1)
        rows.append([
            styled("◀ PREVIOUS", f"ubuy_page:{category}:{previous_page}:{int(token_user_id)}", "primary"),
            styled("NEXT ▶", f"ubuy_page:{category}:{next_page}:{int(token_user_id)}", "primary"),
        ])
    return InlineKeyboardMarkup(rows)


def purchase_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [styled("✅ YES, I WANT TO BUY", f"ubuy_confirm:{token}", "success")],
        [styled("❌ CANCEL", f"ubuy_cancel:{token}", "danger"), styled("↩ BACK", f"ubuy_back:{token}", "primary")],
    ])


def direct_purchase_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        styled("✅ YES, BUY", f"ubuy_confirm:{token}", "success"),
        styled("❌ CANCEL", f"ubuy_cancel:{token}", "danger"),
    ]])


def equip_choices(items: list[dict], token: str) -> InlineKeyboardMarkup:
    rows = []
    for item in items:
        rows.append([styled(item["display_label"], f"equip_select:{token}:{item['upgrade_key']}:{item['tier']}", "danger")])
    rows.append([styled("❌ CANCEL", f"equip_cancel:{token}", "danger")])
    return InlineKeyboardMarkup(rows)


def equip_confirm(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        styled("✅ YES, EQUIP", f"equip_confirm:{token}", "success"),
        styled("❌ CANCEL", f"equip_cancel:{token}", "danger"),
    ]])


def unequip_choices(items: list[dict], token: str) -> InlineKeyboardMarkup:
    rows = []
    for item in items:
        rows.append([styled(item["display_label"], f"unequip_select:{token}:{item['slot']}", "danger")])
    rows.append([styled("❌ CANCEL", f"unequip_cancel:{token}", "danger")])
    return InlineKeyboardMarkup(rows)


def unequip_confirm(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        styled("✅ YES, UNEQUIP", f"unequip_confirm:{token}", "success"),
        styled("❌ CANCEL", f"unequip_cancel:{token}", "danger"),
    ]])
