import inspect

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Same colored-button mechanism as buttons/claim_buttons.py,
# buttons/buy_buttons.py and buttons/sell_buttons.py - see
# claim_buttons.py for the full explanation. Kept identical here on
# purpose so all of them stay in sync if the styling approach changes.
# Bot API 9.4 styles used across this file: "success" (green),
# "danger" (red), "primary" (blue).
_BUTTON_PARAMS = set(inspect.signature(InlineKeyboardButton.__init__).parameters)
SUPPORTS_BUTTON_STYLE = "style" in _BUTTON_PARAMS

_FALLBACK_HINT = {"success": "🟢", "danger": "🔴", "primary": "🔵"}


def _styled_button(text: str, callback_data: str, style: str) -> InlineKeyboardButton:
    if SUPPORTS_BUTTON_STYLE:
        return InlineKeyboardButton(text, callback_data=callback_data, style=style)
    hint = _FALLBACK_HINT.get(style, "")
    label = f"{hint} {text}".strip()
    return InlineKeyboardButton(label, callback_data=callback_data)


def challenge_keyboard(match_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                _styled_button("✅ ACCEPT", f"play_accept:{match_id}", "success"),
                _styled_button("❌ DECLINE", f"play_decline:{match_id}", "danger"),
            ]
        ]
    )


def pitch_keyboard(match_id) -> InlineKeyboardMarkup:
    pairs = [
        ("🌿 GREEN", "green"), ("🏜️ DRY", "dry"),
        ("🌪️ DUSTY", "dusty"), ("🛣️ FLAT", "flat"),
        ("🪨 HARD", "hard"), ("⚖️ EVEN", "even"),
        ("🏀 BOUNCY", "bouncy"), ("🐢 SLOW", "slow"),
    ]
    rows = [
        [
            _styled_button(pairs[i][0], f"play_pitch:{match_id}:{pairs[i][1]}", "primary"),
            _styled_button(pairs[i + 1][0], f"play_pitch:{match_id}:{pairs[i + 1][1]}", "primary"),
        ]
        for i in range(0, len(pairs), 2)
    ]
    return InlineKeyboardMarkup(rows)


def toss_call_keyboard(match_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                _styled_button("🗿 HEADS", f"play_toss_call:{match_id}:heads", "primary"),
                _styled_button("🦅 TAILS", f"play_toss_call:{match_id}:tails", "primary"),
            ]
        ]
    )


def bat_bowl_keyboard(match_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                _styled_button("🏏 BAT", f"play_decision:{match_id}:bat", "primary"),
                _styled_button("🎯 BOWL", f"play_decision:{match_id}:bowl", "primary"),
            ]
        ]
    )


def start_match_keyboard(match_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                _styled_button("▶️ START MATCH", f"play_start:{match_id}", "success"),
            ]
        ]
    )


def exit_confirm_keyboard(match_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                _styled_button("✅ Yes, I want", f"play_exit_yes:{match_id}", "success"),
                _styled_button("❌ Cancel", f"play_exit_cancel:{match_id}", "danger"),
            ]
        ]
    )


def bowler_selection_keyboard(match_id, bowlers, selected_id=None) -> InlineKeyboardMarkup:
    rows = []
    for player in bowlers:
        pid = int(player.get("player_id") or 0)
        overs_left = player.get("_overs_left")
        bowling_level = int(player.get("bowl_level") or 0)
        label = f"🥎 {player.get('name', 'Bowler')} • {bowling_level}"
        if overs_left is not None:
            label = f"{label} • Left {overs_left} Ov"
        if selected_id is not None and int(selected_id) == pid:
            label = f"✅ {label}"
        rows.append([_styled_button(label, f"play_bowler:{match_id}:{pid}", "danger")])
    return InlineKeyboardMarkup(rows)


def strategy_keyboard(match_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_styled_button("🛡️ DEFENSIVE", f"play_strategy:{match_id}:defensive", "primary")],
            [_styled_button("🔄 ROTATE", f"play_strategy:{match_id}:rotate", "primary")],
            [_styled_button("⚖️ NEUTRAL", f"play_strategy:{match_id}:neutral", "primary")],
            [_styled_button("⚔️ AGGRESSIVE", f"play_strategy:{match_id}:aggressive", "primary")],
            [_styled_button("🚀 ULTRA AGGRESSIVE", f"play_strategy:{match_id}:ultra_aggressive", "primary")],
        ]
    )


def bowler_tactic_keyboard(match_id, bowler=None) -> InlineKeyboardMarkup:
    """Return the appropriate tactic keyboard for the selected bowler.
    Spinner styles get their dedicated delivery buttons; pace/medium keeps
    the existing five fast-bowling tactics unchanged."""
    style = str((bowler or {}).get("bowling_hand") or "").strip().upper()
    if style in {"RAO", "LAO"} or "OFF BREAK" in style or "OFFSPIN" in style:
        from buttons.off_spinner_buttons import off_spinner_keyboard
        return off_spinner_keyboard(match_id)
    if style in {"RAL", "LAL"} or "LEG SPIN" in style or "LEGSPIN" in style:
        from buttons.leg_spinner_buttons import leg_spinner_keyboard
        return leg_spinner_keyboard(match_id)
    return InlineKeyboardMarkup(
        [
            [_styled_button("🛡️ DEFENSIVE", f"play_tactic:{match_id}:defensive", "success")],
            [_styled_button("🌀 SWINGING", f"play_tactic:{match_id}:swinging", "success")],
            [_styled_button("⚡ PACE UP", f"play_tactic:{match_id}:pace_up", "success")],
            [_styled_button("📏 BACK OF LENGTH", f"play_tactic:{match_id}:back_of_length", "success")],
            [_styled_button("🎯 VARIATION", f"play_tactic:{match_id}:variation", "success")],
        ]
    )

