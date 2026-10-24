import inspect
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)


def _b(text, data, style):
    if "style" in _PARAMS:
        return InlineKeyboardButton(text, callback_data=data, style=style)
    prefix = "🟢" if style == "success" else "🔴"
    return InlineKeyboardButton(f"{prefix} {text}", callback_data=data)


def delete_confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_b("✅ YES, DELETE", f"delp_confirm:{token}", "success"), _b("❌ CANCEL", f"delp_cancel:{token}", "danger")]])
