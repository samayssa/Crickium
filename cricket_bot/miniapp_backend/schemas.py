
from __future__ import annotations

from pydantic import BaseModel


class Profile(BaseModel):
    id: int
    display_name: str
    first_name: str | None = None
    username: str | None = None
    photo_url: str | None = None


class Wallet(BaseModel):
    coins: int = 0
    rubies: int = 0
    total_spent: int = 0


class League(BaseModel):
    label: str
    progress_percent: int
    progress_text: str


class Stats(BaseModel):
    total_members: int = 0
    total_players: int = 0
    total_matches: int = 0
    active_matches: int = 0
    active_users: int = 0
    matches_played: int = 0
    matches_won: int = 0
    win_percentage: float = 0.0


class RewardState(BaseModel):
    streak: int = 0
    total_claimed: int = 0
    available: bool = False
    seconds_until_available: int = 0


class HomeResponse(BaseModel):
    profile: Profile
    wallet: Wallet
    league: League
    stats: Stats
    daily_reward: RewardState
    banner_title: str
    banner_subtitle: str
    primary_cta: str


class PlayerSummary(BaseModel):
    player_id: int
    name: str
    country: str | None = None
    role: str
    role_icon: str
    bat_level: int
    bowl_level: int
    overall: int
    rarity: str
    batting_style: str
    bowling_style: str
    buy_price: int
    owned: bool = False
    card_image_url: str | None = None


class PlayerDetail(PlayerSummary):
    description: str | None = None
    ball_level: int | None = None
    card_type: str | None = None


class PlayerSearchResponse(BaseModel):
    query: str
    count: int
    results: list[PlayerSummary]
