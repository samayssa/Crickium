"""
Delivery engine for the cricket bot.

Compatibility layer that re-exports the canonical shot/probability vocabulary
without duplicating the shot pools in two separate files.
"""

from __future__ import annotations

from engines.probability_engine import (
    get_available_shots as _get_available_shots,
    get_length_options as _get_length_options,
    get_line_options as _get_line_options,
    get_delivery_options as _get_delivery_options,
)
from engines.shot_engine import (
    DELIVERY_TYPES,
    SKIP_LENGTH_FOR,
    LINES,
    LENGTHS,
    FOOT_MOVEMENTS,
    STROKE_TYPES,
    STROKE_INTENTS,
    SHOT_LIBRARY,
    infer_bowler_type as _infer_bowler_type,
)


def get_available_shots(foot_movement: str, stroke_type: str, bowler_type: str) -> list[str]:
    return _get_available_shots(foot_movement, stroke_type, bowler_type)


def get_length_options(delivery_type: str) -> list[str] | None:
    return _get_length_options(delivery_type)


def get_line_options() -> list[str]:
    return _get_line_options()


def get_delivery_options() -> list[str]:
    return _get_delivery_options()


def is_length_skipped(delivery_type: str) -> bool:
    return delivery_type in SKIP_LENGTH_FOR


def get_bowler_type(delivery_type: str) -> str:
    return _infer_bowler_type(delivery_type)


__all__ = [
    "DELIVERY_TYPES",
    "SKIP_LENGTH_FOR",
    "LINES",
    "LENGTHS",
    "FOOT_MOVEMENTS",
    "STROKE_TYPES",
    "STROKE_INTENTS",
    "SHOT_LIBRARY",
    "get_available_shots",
    "get_length_options",
    "get_line_options",
    "get_delivery_options",
    "is_length_skipped",
    "get_bowler_type",
]
