"""Aggressive batting approach."""
from __future__ import annotations

from .base import BallContext, BallOutcome, resolve_generic

BASE_WEIGHTS = {
    "dot": 0.16,
    "single": 0.20,
    "double": 0.07,
    "triple": 0.02,
    "four": 0.25,
    "six": 0.18,
    "wicket": 0.10,
    "wide": 0.015,
    "no_ball": 0.01,
    "bye": 0.005,
    "leg_bye": 0.005,
}


def resolve(context: BallContext) -> BallOutcome:
    return resolve_generic(context, BASE_WEIGHTS)
