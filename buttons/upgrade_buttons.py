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
        styled("🏏 BATTING", f"upgrade_filter:batting:{page}:{int(user_id)}", "primary"),
        styled("🎯 BOWLING", f"upgrade_filter:bowling:{page}:{int(user_id)}", "success"),
    ]]
    nav = []
    if page > 0:
        nav.append(styled("◀ PREVIOUS", f"upgrade_page:{filter_name}:{page-1}:{int(user_id)}", "primary"))
    if page + 1 < total_pages:
        nav.append(styled("NEXT ▶", f"upgrade_page:{filter_name}:{page+1}:{int(user_id)}", "primary"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


def category_upgrade_buttons(items: list[dict], category: str, page: int, total_pages: int, token: str, token_user_id: int) -> InlineKeyboardMarkup:
    rows = [[
        styled("🏏 BATTING", f"upgrade_filter:{category}:0:{int(token_user_id)}", "primary"),
        styled("🎯 BOWLING", f"upgrade_filter:{'bowling' if category == 'batting' else 'batting'}:0:{int(token_user_id)}", "success"),
    ]]
    for item in items:
        label = f"{item['name']} • {item['price']:,} 💎"
        rows.append([styled(label, f"ubuy_select:{token}:{item['upgrade_key']}:{item['tier']}", "danger")])
    nav = []
    if page > 0:
        nav.append(styled("◀ PREVIOUS", f"upgrade_page:{category}:{page-1}:{int(token_user_id)}", "primary"))
    if page + 1 < total_pages:
        nav.append(styled("NEXT ▶", f"upgrade_page:{category}:{page+1}:{int(token_user_id)}", "primary"))
    if nav:
        rows.append(nav)
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
