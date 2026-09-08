from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PlaySOSession:
    match_id: int
    chat_id: int
    challenger_id: int
    opponent_id: int
    innings_no: int = 1
    ball_no: int = 0
    legal_balls: int = 0
    runs: int = 0
    wickets: int = 0
    target: int | None = None
    batting_user_id: int = 0
    bowling_user_id: int = 0
    batters: list[dict[str, Any]] = field(default_factory=list)
    bowler: dict[str, Any] | None = None
    length: str | None = None
    delivery: str | None = None
    line: str | None = None
    foot: str | None = None
    intent: str | None = None
    shot: str | None = None
    previous_bowler_by_team: dict[str, int | None] = field(default_factory=dict)
