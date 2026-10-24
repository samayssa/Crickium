"""Balanced batting approach."""
from __future__ import annotations

from .base import BallContext, BallOutcome, resolve_generic

BASE_WEIGHTS = {
    "dot": 0.24,
    "single": 0.28,
    "double": 0.12,
    "triple": 0.03,
    "four": 0.17,
    "six": 0.05,
    "wicket": 0.08,
    "wide": 0.01,
    "no_ball": 0.01,
    "bye": 0.01,
    "leg_bye": 0.01,
}


def resolve(context: BallContext) -> BallOutcome:
    return resolve_generic(context, BASE_WEIGHTS)
