"""Squad model used for the auto-generated playing XI."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .player import PlayerCard


@dataclass(slots=True)
class Squad:
    user_id: int
    players: list[PlayerCard] = field(default_factory=list)

    def add(self, player: PlayerCard) -> None:
        self.players.append(player)

    def extend(self, players: Iterable[PlayerCard]) -> None:
        self.players.extend(players)

    def count_by_role(self, role: str) -> int:
        return sum(1 for p in self.players if p.role.lower() == role.lower())

    def to_dict(self) -> dict:
        return {"user_id": self.user_id, "players": [p.to_dict() for p in self.players]}
