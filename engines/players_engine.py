"""
players_engine.py
Handles player loading, lookup, validation and random selection.
"""
from __future__ import annotations

from typing import List, Dict, Optional
import random


class PlayersEngine:
    def __init__(self, repository):
        self.repo = repository

    async def get_player(self, name: str) -> Optional[Dict]:
        return await self.repo.get_player(name)

    async def get_by_role(self, role: str) -> List[Dict]:
        return await self.repo.get_players_by_role(role)

    async def get_players_by_role(self, role: str) -> List[Dict]:
        return await self.get_by_role(role)

    async def random_players(self, role: str, count: int, min_level: int = 0) -> List[Dict]:
        players = [
            p for p in await self.get_by_role(role)
            if max(p.get("bat_level", 0), p.get("bowl_level", 0)) >= min_level
        ]
        random.shuffle(players)
        return players[:count]

    async def get_random_players(self, role: str, count: int, min_level: int = 0) -> List[Dict]:
        return await self.random_players(role, count, min_level)

    def validate(self, player: Dict) -> bool:
        return all(k in player for k in ("name", "role", "bat_level", "bowl_level"))
