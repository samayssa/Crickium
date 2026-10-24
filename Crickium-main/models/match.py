"""Match session model."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class MatchState:
    match_id: int | None
    chat_id: int
    challenger_id: int
    opponent_id: int
    format: str = "T20"
    status: str = "pending"
    toss_winner_id: int | None = None
    toss_call: str | None = None
    toss_result: str | None = None
    decision: str | None = None
    runs: int = 0
    wickets: int = 0
    overs: int = 0
    balls: int = 0
    created_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)
