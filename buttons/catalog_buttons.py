import inspect
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)


def _b(text, data, style):
    if "style" in _PARAMS:
        return InlineKeyboardButton(text, callback_data=data, style=style)
    prefix = {"success": "🟢", "danger": "🔴", "primary": "🔵"}.get(style, "")
    return InlineKeyboardButton(f"{prefix} {text}".strip(), callback_data=data)


def catalog_page_keyboard(token: str, page: int, total: int) -> InlineKeyboardMarkup:
    pages = max(1, int(total))
    return InlineKeyboardMarkup([
        [_b(f"📚 {pages} Players • Page {page + 1}/{pages}", f"{token}:noop", "danger")],
        [
            _b("◀️ Previous", f"{token}:prev", "primary"),
            _b("Next ▶️", f"{token}:next", "success"),
        ],
    ])


def buy_catalog_keyboard(token: str, page: int, total: int, sign_callback: str, decline_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_b("Yes, Sign", sign_callback, "success"), _b("Maybe Later", decline_callback, "danger")],
        [_b(f"📚 {max(1, int(total))} Players • Page {page + 1}/{max(1, int(total))}", f"buy_page:{token}:noop", "danger")],
        [_b("◀️ Previous", f"buy_page:{token}:prev", "primary"), _b("Next ▶️", f"buy_page:{token}:next", "success")],
    ])
