"""
squad_engine.py
Creates and validates playing squads.
"""
from typing import List,Dict
from .players_engine import PlayersEngine

class SquadEngine:
    def __init__(self,players:PlayersEngine):
        self.players=players

    async def generate_default_squad(self):
        bats=await self.players.random_players("Batsman",5,75)
        bowls=await self.players.random_players("Bowler",4,75)
        ars=await self.players.random_players("AllRounder",2,70)
        return bats+bowls+ars

    def validate(self,squad:List[Dict])->bool:
        if len(squad)!=11:return False
        roles=[p["role"] for p in squad]
        return roles.count("Batsman")>=5 and roles.count("Bowler")>=4
