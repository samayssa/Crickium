import inspect

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

                                                                      
                                                                      
                                                                   
                                                                       
                                                                    
                                                                      
                                                   
 
                                                                      
                                                                   
                                                   
_BUTTON_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
SUPPORTS_BUTTON_STYLE = "style" in _BUTTON_PARAMS

_FALLBACK_HINT = {"success": "🟢", "danger": "🔴"}


def _styled_button(text: str, callback_data: str, style: str) -> InlineKeyboardButton:
    if SUPPORTS_BUTTON_STYLE:
        return InlineKeyboardButton(text, callback_data=callback_data, style=style)
    hint = _FALLBACK_HINT.get(style, "")
    label = f"{hint} {text}".strip()
    return InlineKeyboardButton(label, callback_data=callback_data)


def retain_release_keyboard(claim_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                _styled_button("Retain", f"claim_retain:{claim_id}", "success"),
                _styled_button("Release", f"claim_release:{claim_id}", "danger"),
            ]
        ]
    )
