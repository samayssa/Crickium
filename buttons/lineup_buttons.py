"""Inline keyboards for lineup selection."""
from __future__ import annotations

from typing import Any, Iterable


def _chunk(items: list[dict[str, str]], size: int = 2) -> list[list[dict[str, str]]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _player_label(player: dict[str, Any], selected: bool = False) -> str:
    name = str(player.get("name", "Player"))
    role = str(player.get("role", ""))
    bat = player.get("bat_level")
    bowl = player.get("bowl_level")

    if role == "Batsman":
        meta = f"BAT {bat}"
    elif role == "Bowler":
        meta = f"BOWL {bowl}"
    elif role == "AllRounder":
        meta = f"BAT {bat}/BOWL {bowl}"
    else:
        meta = role or "PLAYER"

    prefix = "✅ " if selected else ""
    return f"{prefix}{name} · {meta}"


def player_selection_keyboard(prefix: str, challenge_id: int, players: Iterable[dict[str, Any]], selected_ids: set[int] | None = None, columns: int = 2) -> dict:
    selected_ids = selected_ids or set()
    buttons = []
    for player in players:
        player_id = int(player.get("player_id") or 0)
        buttons.append({
            "text": _player_label(player, selected=player_id in selected_ids),
            "callback_data": f"{prefix}:{challenge_id}:{player_id}",
        })
    return {"inline_keyboard": _chunk(buttons, columns)}
