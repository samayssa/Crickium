"""Strategy-specific ball outcome engines for /play."""
from .base import BallContext, BallOutcome, resolve_outcome, outcome_symbol, outcome_runs
from .defensive import resolve as defensive_resolve
from .rotate import resolve as rotate_resolve
from .neutral import resolve as neutral_resolve
from .aggressive import resolve as aggressive_resolve
from .ultra_aggressive import resolve as ultra_aggressive_resolve

STRATEGIES = {
    "defensive": defensive_resolve,
    "rotate": rotate_resolve,
    "neutral": neutral_resolve,
    "aggressive": aggressive_resolve,
    "ultra_aggressive": ultra_aggressive_resolve,
}

STRATEGY_LABELS = {
    "defensive": "DEFENSIVE",
    "rotate": "ROTATE",
    "neutral": "NEUTRAL",
    "aggressive": "AGGRESSIVE",
    "ultra_aggressive": "ULTRA AGGRESSIVE",
}
