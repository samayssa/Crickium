"""Player card model."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class PlayerCard:
    player_id: int | None
    name: str
    country: str | None
    role: str
    bat_level: int
    bowl_level: int
    created_at: datetime | None = None
    uploaded_by: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.created_at is not None:
            data["created_at"] = self.created_at.isoformat()
        return data
