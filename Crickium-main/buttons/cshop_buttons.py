import inspect

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_BUTTON_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
SUPPORTS_BUTTON_STYLE = "style" in _BUTTON_PARAMS
_FALLBACK_HINT = {"success": "🟢", "danger": "🔴"}


def _styled_button(text: str, callback_data: str, style: str) -> InlineKeyboardButton:
    if SUPPORTS_BUTTON_STYLE:
        return InlineKeyboardButton(text, callback_data=callback_data, style=style)
    return InlineKeyboardButton(
        f"{_FALLBACK_HINT.get(style, '')} {text}".strip(),
        callback_data=callback_data,
    )


def exchange_confirm_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            _styled_button("✅ YES, CONFIRM", f"cshop_confirm:{request_id}", "success"),
            _styled_button("❌ CANCEL", f"cshop_cancel:{request_id}", "danger"),
        ]]
    )
