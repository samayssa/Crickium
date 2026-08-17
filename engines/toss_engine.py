"""Small toss helper for the challenge flow."""
from __future__ import annotations

import random


def resolve_toss(call: str) -> tuple[str, bool]:
    actual = random.choice(["heads", "tails"])
    return actual, actual == call


def normalize_call(call: str) -> str:
    call = (call or "").strip().lower()
    if call not in {"heads", "tails"}:
        raise ValueError("Toss call must be 'heads' or 'tails'.")
    return call
