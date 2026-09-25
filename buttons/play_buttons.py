from utils.PremiumEmoji import impact_player_name_html
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


def bowler_selection_keyboard(match_id, bowlers, selected_id=None, auto_enabled=False, impact_enabled=True) -> InlineKeyboardMarkup:
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
    rows.append(runtime_bowler_actions(match_id, auto_enabled, impact_enabled))
    return InlineKeyboardMarkup(rows)


def strategy_keyboard(match_id, auto_enabled=False, impact_enabled=True) -> InlineKeyboardMarkup:
    rows = [
        [_styled_button("🛡️ DEFENSIVE", f"play_strategy:{match_id}:defensive", "primary")],
        [_styled_button("🔄 ROTATE", f"play_strategy:{match_id}:rotate", "primary")],
        [_styled_button("⚖️ NEUTRAL", f"play_strategy:{match_id}:neutral", "primary")],
        [_styled_button("⚔️ AGGRESSIVE", f"play_strategy:{match_id}:aggressive", "primary")],
        [_styled_button("🚀 ULTRA AGGRESSIVE", f"play_strategy:{match_id}:ultra_aggressive", "primary")],
    ]
    rows.append(runtime_batting_actions(match_id, auto_enabled, impact_enabled))
    return InlineKeyboardMarkup(rows)


def bowler_tactic_keyboard(match_id, bowler=None, auto_enabled=False, impact_enabled=True) -> InlineKeyboardMarkup:
    style = str((bowler or {}).get("bowling_hand") or "").strip().upper()
    if style in {"RAO", "LAO"} or "OFF BREAK" in style or "OFFSPIN" in style:
        pairs = [("🌀 OFF BREAK BALL", "off_break"), ("🔄 DOOSRA BALL", "doosra"), ("➡️ ARM BALL", "arm_ball"), ("🎯 CARROM BALL", "carrom_ball"), ("⬆️ TOP SPIN BALL", "top_spin")]
    elif style in {"RAL", "LAL"} or "LEG SPIN" in style or "LEGSPIN" in style:
        pairs = [("🌀 LEG BREAKER BALL", "leg_breaker"), ("⬆️ TOP SPINNER BALL", "top_spinner"), ("↔️ SLIDER BALL", "slider"), ("💨 FLIPPER BALL", "flipper"), ("🔀 GOOGLY BALL", "googly_ball")]
    else:
        pairs = [("🛡️ DEFENSIVE", "defensive"), ("🌀 SWINGING", "swinging"), ("⚡ PACE UP", "pace_up"), ("📏 BACK OF LENGTH", "back_of_length"), ("🎯 VARIATION", "variation")]
    rows = [[_styled_button(lbl, f"play_tactic:{match_id}:{val}", "success")] for lbl, val in pairs]
    rows.append(runtime_bowler_actions(match_id, auto_enabled, impact_enabled))
    return InlineKeyboardMarkup(rows)


def runtime_bowler_actions(match_id, auto_enabled=False, impact_enabled=True):
    first = _styled_button(
        "⏹ OFF AUTO BOWLER" if auto_enabled else "✅ SET NEXT BOWLER",
        f"play_auto_bowler_off:{match_id}" if auto_enabled else f"play_set_next_bowler:{match_id}",
        "danger" if auto_enabled else "success",
    )
    if impact_enabled:
        second = _styled_button("⚡ IMPACT PLAYER", f"play_impact_runtime:{match_id}", "danger")
        return [first, second]
    return [first]


def runtime_batting_actions(match_id, auto_enabled=False, impact_enabled=True):
    first = _styled_button(
        "⏹ OFF AUTO PLAY" if auto_enabled else "✅ SET NEXT BATSMAN",
        f"play_auto_batsman_off:{match_id}" if auto_enabled else f"play_set_next_batsman:{match_id}",
        "danger" if auto_enabled else "success",
    )
    if impact_enabled:
        second = _styled_button("⚡ IMPACT PLAYER", f"play_impact_runtime:{match_id}", "danger")
        return [first, second]
    return [first]


def schedule_bowler_keyboard(match_id, bowlers, selected_id=None, auto_enabled=False, impact_enabled=True):
    rows = []
    for player in bowlers:
        pid = int(player.get("player_id") or 0)
        lvl = int(player.get("bowl_level") or 0)
        left = int(player.get("_overs_left") or 0)
        style = "success" if selected_id is not None and pid == int(selected_id) else "danger"
        rows.append([
            _styled_button(
                f"🥎 {player.get('name','Bowler')} • {lvl} • Left {left} Ov",
                f"play_schedule_bowler:{match_id}:{pid}",
                style,
            )
        ])
    if selected_id is not None:
        rows.append([_styled_button("✅ CONFIRM NEXT BOWLER", f"play_confirm_next_bowler:{match_id}", "success")])
    rows.append([_styled_button("▶️ START AUTO PLAY", f"play_start_auto_bowler:{match_id}", "success")])
    rows.append(runtime_bowler_actions(match_id, auto_enabled, impact_enabled))
    return InlineKeyboardMarkup(rows)


def schedule_batsman_keyboard(match_id, players, selected_ids=None, impact_enabled=True):
    selected = {int(x) for x in (selected_ids or [])}
    rows = []
    pair = []
    for player in players:
        pid = int(player.get("player_id") or 0)
        label = f"#{int(player.get('position') or 0)} • {player.get('name','Batter')}"
        pair.append(_styled_button(
            label,
            f"play_schedule_batsman:{match_id}:{pid}",
            "success" if pid in selected else "primary",
        ))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    if selected:
        rows.append([_styled_button("✅ CONFIRM BATSMAN ORDER", f"play_confirm_batsman:{match_id}", "success")])
    rows.append([_styled_button("❌ CANCEL", f"play_cancel_batsman_schedule:{match_id}", "danger")])
    return InlineKeyboardMarkup(rows)


def impact_player_keyboard(prefix, match_id, team_id, players, selected_id=None, stage="out", show_cancel=False):
    rows = []
    pair = []
    action = "impact_out" if stage == "out" else "impact_in"
    for index, player in enumerate(players, start=1):
        pid = int(player.get("player_id") or 0)
        name = player.get('name','Player')
        if selected_id is not None and pid == int(selected_id):
            name = impact_player_name_html(name, "in" if stage == "in" else "out")
        label = f"{index}. {name}"
        pair.append(_styled_button(
            label,
            f"{prefix}_{action}:{match_id}:{int(team_id)}:{pid}",
            "success" if selected_id is not None and pid == int(selected_id) else "danger",
        ))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    if selected_id is not None:
        cb = "impact_confirm_out" if stage == "out" else "impact_confirm_in"
        label = "✅ CONFIRM OUT PLAYER" if stage == "out" else "✅ CONFIRM IMPACT PLAYER"
        rows.append([_styled_button(label, f"{prefix}_{cb}:{match_id}:{int(team_id)}", "danger")])
    if stage == "out" and show_cancel:
        rows.append([_styled_button("❌ CANCEL IMPACT", f"{prefix}_cancel_impact:{match_id}:{int(team_id)}", "danger")])
    return InlineKeyboardMarkup(rows)


def impact_batting_position_keyboard(prefix, match_id, players, selected_position=None):
    rows = []
    pair = []
    for player in players:
        pos = int(player.get("position") or 0)
        name = player.get('name','Batter')
        if pos == 1:
            label = f"🏏 STRIKER • {name}"
        elif pos == 2:
            label = f"🏏 NON-STRIKER • {name}"
        else:
            label = f"#{pos} • {name}"
        pair.append(_styled_button(
            label,
            f"{prefix}_impact_batpos:{match_id}:{pos}",
            "success" if selected_position == pos else "primary",
        ))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    if selected_position is not None:
        rows.append([_styled_button("✅ CONFIRM BATTING POSITION", f"{prefix}_impact_confirm_batpos:{match_id}", "success")])
    return InlineKeyboardMarkup(rows)


def impact_batting_role_keyboard(prefix, match_id):
    return InlineKeyboardMarkup([
        [
            _styled_button("🟦 STRIKER", f"{prefix}_impact_role:{match_id}:striker", "primary"),
            _styled_button("🟦 NON-STRIKER", f"{prefix}_impact_role:{match_id}:non_striker", "primary"),
        ]
    ])
