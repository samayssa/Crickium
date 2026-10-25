from __future__ import annotations

from services.tournament_virtual import base_code as _vbase

TEAM_MAP = {
    'IND': 'India', 'AUS': 'Australia', 'ENG': 'England', 'NZ': 'New Zealand',
    'SA': 'South Africa', 'BAN': 'Bangladesh', 'PAK': 'Pakistan', 'SL': 'Sri Lanka',
    'AFG': 'Afghanistan', 'WI': 'West Indies', 'IRE': 'Ireland', 'ZIM': 'Zimbabwe',
    'NED': 'Netherlands', 'SCO': 'Scotland', 'NAM': 'Namibia', 'USA': 'USA',
    'CAN': 'Canada', 'NEP': 'Nepal',
}

TEAMS_PAGE_1 = ['IND','AUS','ENG','NZ','SA','BAN','PAK','SL']
TEAMS_PAGE_2 = ['AFG','WI','IRE','ZIM','NED','SCO','NAM','USA','CAN','NEP']


def normalize_team_keyword(value: str):
    raw = str(value or '').strip().upper()
    if raw.startswith('T20I-'):
        raw = raw[5:]
    return raw if raw in TEAM_MAP else None


def team_name(code):
    # Tournament teams use virtual codes (IND12): show the nation they are dressed as.
    return TEAM_MAP[_vbase(code)]


def team_flag(code):
    from utils.country_flags import flag_for
    return flag_for(team_name(code))


def team_label(code):
    return f"{team_flag(code)} {team_name(code)}"
