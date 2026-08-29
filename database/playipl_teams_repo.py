from __future__ import annotations

TEAM_MAP = {
    'CSK': 'Chennai Super Kings',
    'DC': 'Delhi Capitals',
    'GT': 'Gujarat Titans',
    'KKR': 'Kolkata Knight Riders',
    'LSG': 'Lucknow Super Giants',
    'MI': 'Mumbai Indians',
    'PBKS': 'Punjab Kings',
    'RR': 'Rajasthan Royals',
    'RCB': 'Royal Challengers Bengaluru',
    'SRH': 'Sunrisers Hyderabad',
}

TEAM_ORDER = ['CSK', 'DC', 'GT', 'KKR', 'LSG', 'MI', 'PBKS', 'RR', 'RCB', 'SRH']

# One emoji is used only as a lightweight franchise-theme marker in buttons/text.
TEAM_COLOR = {
    'CSK': '🟡',
    'DC': '🔵',
    'GT': '🔷',
    'KKR': '🟣',
    'LSG': '🔴',
    'MI': '🔵',
    'PBKS': '🔴',
    'RR': '🩷',
    'RCB': '🔴',
    'SRH': '🟠',
}


def normalize_team_keyword(value: str):
    raw = str(value or '').strip().upper()
    if raw.startswith('IPL-'):
        raw = raw[4:]
    return raw if raw in TEAM_MAP else None


def team_name(code: str) -> str:
    return TEAM_MAP[str(code).upper()]


def team_short(code: str) -> str:
    return str(code).upper()


def team_color(code: str) -> str:
    return TEAM_COLOR.get(str(code).upper(), '🏏')


def team_label(code: str) -> str:
    code = str(code).upper()
    return f"{team_color(code)} {team_name(code)}"


def team_button_label(code: str) -> str:
    code = str(code).upper()
    return f"{team_color(code)} {team_short(code)}"
