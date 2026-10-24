import inspect
from pyrogram.types import InlineKeyboardButton
_BUTTON_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)

def _styled(text, callback_data, style):
    if "style" in _BUTTON_PARAMS:
        return InlineKeyboardButton(text, callback_data=callback_data, style=style)
    return InlineKeyboardButton(("🟢 " if style == "success" else "🔴 ") + text, callback_data=callback_data)

def post_confirm_keyboard(chat_id, message_id):
    return {"inline_keyboard": [[
        _styled("Yes, forward", f"post_yes:{chat_id}:{message_id}", "success"),
        _styled("No, cancel", f"post_no:{chat_id}:{message_id}", "danger"),
    ]]}
