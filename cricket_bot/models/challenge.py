"""Challenge model for reply-based match invites."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class ChallengeState:
    challenge_id: int | None
    chat_id: int
    challenger_id: int
    opponent_id: int | None
    challenger_username: str | None = None
    opponent_username: str | None = None
    status: str = "pending"
    format: str = "T20"
    message_id: int | None = None
    expires_at: datetime | None = None
