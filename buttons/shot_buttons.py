"""
Keyboards for the batsman's shot-selection stage.
Not wired into a live command yet - ready for when /startgame's
ball-by-ball flow is built on top of engines/shot_engine.py.
"""

from engines.shot_engine import FOOT_MOVEMENTS, STROKE_TYPES, get_available_shots


def _chunk(items, size=2):
    return [items[i:i + size] for i in range(0, len(items), size)]


def foot_movement_keyboard(match_id) -> dict:
    buttons = [{"text": f, "callback_data": f"foot:{match_id}:{f}"} for f in FOOT_MOVEMENTS]
    return {"inline_keyboard": _chunk(buttons, 1)}


def stroke_type_keyboard(match_id) -> dict:
    buttons = [{"text": s, "callback_data": f"stroke_type:{match_id}:{s}"} for s in STROKE_TYPES]
    return {"inline_keyboard": _chunk(buttons, 2)}


def shot_keyboard(match_id, foot_movement: str, stroke_type: str, bowler_type: str) -> dict:
    shots = get_available_shots(foot_movement, stroke_type, bowler_type)
    buttons = [{"text": s, "callback_data": f"shot:{match_id}:{s}"} for s in shots]
    return {"inline_keyboard": _chunk(buttons, 2)}
