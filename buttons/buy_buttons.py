import inspect

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Same colored-button mechanism as buttons/claim_buttons.py - see that
# file for the full explanation. Kept identical here on purpose so both
# stay in sync if the button-styling approach ever changes.
_BUTTON_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
SUPPORTS_BUTTON_STYLE = "style" in _BUTTON_PARAMS

_FALLBACK_HINT = {"success": "🟢", "danger": "🔴"}


def _styled_button(text: str, callback_data: str, style: str) -> InlineKeyboardButton:
    if SUPPORTS_BUTTON_STYLE:
        return InlineKeyboardButton(text, callback_data=callback_data, style=style)
    hint = _FALLBACK_HINT.get(style, "")
    label = f"{hint} {text}".strip()
    return InlineKeyboardButton(label, callback_data=callback_data)


def buy_confirm_keyboard(player_id, buyer_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                _styled_button("Yes, Sign", f"buy_confirm:{player_id}:{buyer_id}", "success"),
                _styled_button("Maybe Later", f"buy_decline:{player_id}:{buyer_id}", "danger"),
            ]
        ]
    )
