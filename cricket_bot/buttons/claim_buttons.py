import inspect

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# style="success"/"danger" (green/red buttons) is a Bot API 9.4 field.
# Mainline pyrogram's InlineKeyboardButton doesn't know this parameter
# and raises TypeError if you pass it directly - that's exactly the
# crash this file used to cause. "kurigram" (see requirements.txt) is a
# maintained drop-in fork that DOES support it, under the exact same
# `pyrogram` import name, so installing it is all that's needed to get
# real green/red buttons - no code change required.
#
# Until/unless kurigram is installed, this file detects that at import
# time and falls back to plain buttons (with a colored-circle emoji
# hint instead) so /claim keeps working either way.
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
