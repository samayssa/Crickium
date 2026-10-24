"""In-memory match state store."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GameState:
    match_id: int | None = None
    chat_id: int | None = None
    stage: str = "idle"
    data: dict[str, Any] = field(default_factory=dict)


_ACTIVE_GAMES: dict[int, GameState] = {}


def get_game(chat_id: int) -> GameState | None:
    return _ACTIVE_GAMES.get(chat_id)


def create_game(chat_id: int, match_id: int | None = None) -> GameState:
    game = GameState(match_id=match_id, chat_id=chat_id)
    _ACTIVE_GAMES[chat_id] = game
    return game


def update_game(chat_id: int, **fields: Any) -> GameState:
    game = _ACTIVE_GAMES.get(chat_id) or create_game(chat_id)
    for key, value in fields.items():
        if key == "data" and isinstance(value, dict):
            game.data.update(value)
        else:
            setattr(game, key, value)
    return game


def clear_game(chat_id: int) -> None:
    _ACTIVE_GAMES.pop(chat_id, None)
