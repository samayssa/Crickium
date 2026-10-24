from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from buttons.play_buttons import _styled_button
except Exception:
    def _styled_button(text: str, callback_data: str, style: str):
        return InlineKeyboardButton(text, callback_data=callback_data, style=style)

def leg_spinner_keyboard(match_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_styled_button("🌀 LEG BREAKER BALL", f"play_tactic:{match_id}:leg_breaker", "success")],
        [_styled_button("⬆️ TOP SPINNER BALL", f"play_tactic:{match_id}:top_spinner", "success")],
        [_styled_button("↔️ SLIDER BALL", f"play_tactic:{match_id}:slider", "success")],
        [_styled_button("💨 FLIPPER BALL", f"play_tactic:{match_id}:flipper", "success")],
        [_styled_button("🔀 GOOGLY BALL", f"play_tactic:{match_id}:googly_ball", "success")],
    ])
