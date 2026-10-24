"""Defensive batting approach."""
from __future__ import annotations

from .base import BallContext, BallOutcome, resolve_generic

BASE_WEIGHTS = {
    "dot": 0.52,
    "single": 0.20,
    "double": 0.07,
    "triple": 0.01,
    "four": 0.11,
    "six": 0.02,
    "wicket": 0.08,
    "wide": 0.01,
    "no_ball": 0.01,
    "bye": 0.005,
    "leg_bye": 0.005,
}


def resolve(context: BallContext) -> BallOutcome:
    return resolve_generic(context, BASE_WEIGHTS)
