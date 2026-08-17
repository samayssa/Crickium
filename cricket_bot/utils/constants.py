"""Shared constants for the cricket bot."""

BOT_NAME = "Cricket Bot"
DEFAULT_MATCH_FORMAT = "T20"
DEFAULT_CHALLENGE_TIMEOUT = 60
DEFAULT_TOSS_TIMEOUT = 90
DEFAULT_DECISION_TIMEOUT = 90

ROLE_BATSMAN = "Batsman"
ROLE_BOWLER = "Bowler"
ROLE_ALLROUNDER = "AllRounder"

MATCH_STAGES = [
    "pending",
    "accepted",
    "toss_call",
    "toss_done",
    "lineup",
    "lineup_batting",
    "lineup_bowling",
    "playing",
    "innings_complete",
    "completed",
]
