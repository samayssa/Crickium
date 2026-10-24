"""Rotate the strike with low-risk accumulation."""
from __future__ import annotations

from .base import BallContext, BallOutcome, resolve_generic

BASE_WEIGHTS = {
    "dot": 0.30,
    "single": 0.34,
    "double": 0.16,
    "triple": 0.03,
    "four": 0.10,
    "six": 0.02,
    "wicket": 0.05,
    "wide": 0.01,
    "no_ball": 0.01,
    "bye": 0.01,
    "leg_bye": 0.01,
}


def resolve(context: BallContext) -> BallOutcome:
    return resolve_generic(context, BASE_WEIGHTS)
