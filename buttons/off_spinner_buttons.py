from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from buttons.play_buttons import _styled_button
except Exception:
    def _styled_button(text: str, callback_data: str, style: str):
        return InlineKeyboardButton(text, callback_data=callback_data, style=style)

def off_spinner_keyboard(match_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_styled_button("🌀 OFF BREAK BALL", f"play_tactic:{match_id}:off_break", "success")],
        [_styled_button("🔄 DOOSRA BALL", f"play_tactic:{match_id}:doosra", "success")],
        [_styled_button("➡️ ARM BALL", f"play_tactic:{match_id}:arm_ball", "success")],
        [_styled_button("🎯 CARROM BALL", f"play_tactic:{match_id}:carrom_ball", "success")],
        [_styled_button("⬆️ TOP SPIN BALL", f"play_tactic:{match_id}:top_spin", "success")],
    ])
