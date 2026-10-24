def endgame_confirm_keyboard(chat_id) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Yes, Abandon", "callback_data": f"endgame_yes:{chat_id}"},
            {"text": "❌ No, Cancel", "callback_data": f"endgame_cancel:{chat_id}"},
        ]]
    }
import inspect

try:
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    _PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
    _SUPPORTS_STYLE = "style" in _PARAMS
except Exception:
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None
    _SUPPORTS_STYLE = False

_HINT = {"success": "🟢", "danger": "🔴"}


def _abandon_button(text: str, callback_data: str, style: str):
    if _SUPPORTS_STYLE:
        return InlineKeyboardButton(text, callback_data=callback_data, style=style)
    hint = _HINT.get(style, "")
    return InlineKeyboardButton(f"{hint} {text}".strip(), callback_data=callback_data)


def abandon_confirm_keyboard(chat_id: int) -> "InlineKeyboardMarkup":
    """Owner-only /abond confirmation buttons using the same green/red
    Bot API button styling used by the project's other keyboards."""
    return InlineKeyboardMarkup([
        [
            _abandon_button("✅ Yes, Abandon", f"abandon_yes:{chat_id}", "success"),
            _abandon_button("❌ No, Cancel", f"abandon_cancel:{chat_id}", "danger"),
        ]
    ])
