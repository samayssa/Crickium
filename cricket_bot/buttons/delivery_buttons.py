"""
Keyboards for the ball-by-ball delivery stage (bowler side).
Not wired into a live command yet - ready for when /startgame's
ball-by-ball flow is built on top of engines/shot_engine.py.
"""

from engines.shot_engine import DELIVERY_TYPES, LINES, LENGTHS


def _chunk(items, size=2):
    return [items[i:i + size] for i in range(0, len(items), size)]


def delivery_type_keyboard(match_id) -> dict:
    buttons = [{"text": d, "callback_data": f"delivery_type:{match_id}:{d}"} for d in DELIVERY_TYPES]
    return {"inline_keyboard": _chunk(buttons, 2)}


def line_keyboard(match_id) -> dict:
    buttons = [{"text": l, "callback_data": f"delivery_line:{match_id}:{l}"} for l in LINES]
    return {"inline_keyboard": _chunk(buttons, 1)}


def length_keyboard(match_id) -> dict:
    buttons = [{"text": l, "callback_data": f"delivery_length:{match_id}:{l}"} for l in LENGTHS]
    return {"inline_keyboard": _chunk(buttons, 2)}
