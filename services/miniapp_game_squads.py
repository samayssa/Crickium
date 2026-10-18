"""Squad helpers for the Mini App "Play Game" mode.

* turns the user's saved squad cards into compact in-game player dicts
* builds the computer's Playing XI (balanced side, strength set by the
  difficulty and the strength of the user's own XI)

Pure functions - the database is touched only by database/miniapp_game_repo.py.
"""

from __future__ import annotations

import random
import re
from typing import Any

import engines.miniapp_tactics_engine as eng

AI_TEAMS = {
    "easy": ("Rookie XI", "RKX"),
    "medium": ("Challengers XI", "CHX"),
    "hard": ("Titans XI", "TTN"),
    "expert": ("Legends XI", "LGD"),
}

# batting order of the computer's side: 4 batters, keeper, 2 all-rounders, 4 bowlers
AI_TEMPLATE = ("Batsman", "Batsman", "Wicketkeeper", "Batsman", "AllRounder",
               "Batsman", "AllRounder", "Bowler", "Bowler", "Bowler", "Bowler")

_TAG_RE = re.compile(r"<[^>]*>")
_SPACE_RE = re.compile(r"\s+")

FALLBACK_NAMES = [
    "A. Verma", "R. Khanna", "S. Iyer", "M. Rao", "K. Singh", "D. Patel", "N. Joshi",
    "T. Menon", "V. Kapoor", "P. Desai", "H. Malik", "J. Bhat", "L. Sethi", "G. Nair",
    "C. Bose", "F. Ansari", "B. Rawat", "E. Dutta", "O. Gill", "U. Pillai",
]
FALLBACK_STYLES = ["RAF", "LAF", "RAM", "LAM", "RAO", "LAO", "RAL", "LAL"]


def plain(text: Any, limit: int = 40) -> str:
    """Strip markup / control characters from a display string."""
    value = _TAG_RE.sub("", str(text or ""))
    value = "".join(ch for ch in value if ch.isprintable())
    value = _SPACE_RE.sub(" ", value).strip()
    return value[:limit]


def role_key(role: Any) -> str:
    raw = str(role or "").strip().lower()
    if "keeper" in raw or raw in {"wk", "wicketkeeper"}:
        return "Wicketkeeper"
    if "all" in raw:
        return "AllRounder"
    if "bowl" in raw:
        return "Bowler"
    return "Batsman"


def can_bowl(player: dict) -> bool:
    return bool(player.get("bw")) and int(player.get("bowl") or 0) > 0


def make_player(raw: dict, slot: int, *, entity_id: int | None = None, kind: str = "global",
                upg_bat: dict | None = None, upg_bowl: dict | None = None) -> dict:
    """Compact in-game copy of a squad card."""
    bat = max(1, min(99, int(raw.get("bat_level") or raw.get("bat") or 1)))
    bowl = max(0, min(99, int(raw.get("bowl_level") or raw.get("bowl") or 0)))
    style = str(raw.get("bowling_hand") or raw.get("bw") or "").strip().upper() or None
    if style and style not in {"RAF", "LAF", "RAM", "LAM", "RAO", "LAO", "RAL", "LAL"}:
        # unknown label -> treat by its words, default to medium pace
        low = style.lower()
        if "off" in low:
            style = "RAO"
        elif "leg" in low:
            style = "RAL"
        else:
            style = "RAM"
    return {
        "slot": int(slot),
        "pid": int(raw.get("player_id") or 0),
        "eid": int(entity_id if entity_id is not None else (raw.get("player_id") or 0)),
        "kind": kind,
        "name": plain(raw.get("name") or "Player", 28) or "Player",
        "role": role_key(raw.get("role")),
        "bat": bat,
        "bowl": bowl,
        "bh": (str(raw.get("batting_hand") or "").strip().upper() or None),
        "bw": style,
        "ub": upg_bat,
        "uw": upg_bowl,
    }


def team_code(name: str) -> str:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", plain(name, 40)) if w]
    if not words:
        return "YOU"
    if len(words) == 1:
        return words[0][:3].upper()
    return "".join(w[0] for w in words[:3]).upper()


def _level_for(role: str, bat: int, bowl: int) -> float:
    if role == "Bowler":
        return float(bowl)
    if role == "AllRounder":
        return (bat + bowl) / 2.0
    return float(bat)


def _mean(values: list[float], default: float) -> float:
    return sum(values) / len(values) if values else default


def synthetic_pool(rng: random.Random, size: int = 60) -> list[dict]:
    """Used only when the players table cannot supply enough cards."""
    pool = []
    for i in range(size):
        role = rng.choice(["Batsman", "Batsman", "Wicketkeeper", "AllRounder", "Bowler", "Bowler"])
        bat = rng.randint(35, 88) if role != "Bowler" else rng.randint(12, 40)
        bowl = rng.randint(55, 90) if role == "Bowler" else (rng.randint(40, 80) if role == "AllRounder" else 0)
        pool.append({
            "player_id": -(100000 + i),
            "name": f"{rng.choice(FALLBACK_NAMES)}",
            "role": role, "bat_level": bat, "bowl_level": bowl,
            "batting_hand": rng.choice(["RH", "LH"]),
            "bowling_hand": rng.choice(FALLBACK_STYLES) if bowl else None,
        })
    return pool


def build_ai_squad(user_xi: list[dict], difficulty: str, pool: list[dict],
                   rng: random.Random) -> list[dict]:
    """Eleven balanced players for the computer.

    Strength follows the user's own XI (so every user gets a fair game), then
    the difficulty's ``ai_squad_delta`` pushes it down (Easy) or up (Hard,
    Expert).  Names come from the real player pool when it is available.
    """
    profile = eng.difficulty_profile(difficulty)
    delta = float(profile.get("ai_squad_delta", 0))

    bats = [float(p["bat"]) for p in user_xi if p["role"] in {"Batsman", "Wicketkeeper"}]
    all_bat = [float(p["bat"]) for p in user_xi]
    bowls = [float(p["bowl"]) for p in user_xi if p["role"] == "Bowler" and p["bowl"] > 0]
    ar_b = [float(p["bat"]) for p in user_xi if p["role"] == "AllRounder"]
    ar_w = [float(p["bowl"]) for p in user_xi if p["role"] == "AllRounder" and p["bowl"] > 0]
    mean_bat = _mean(bats, _mean(all_bat, 60.0))
    mean_bowl = _mean(bowls, _mean([float(p["bowl"]) for p in user_xi if p["bowl"] > 0], 60.0))
    mean_ar = _mean([(a + b) / 2 for a, b in zip(ar_b, ar_w)], (mean_bat + mean_bowl) / 2)

    usable = [dict(p) for p in (pool or []) if p.get("name")]
    if len(usable) < 30:
        usable = usable + synthetic_pool(rng, 60)
    by_role: dict[str, list[dict]] = {}
    for p in usable:
        p["_role"] = role_key(p.get("role"))
        by_role.setdefault(p["_role"], []).append(p)

    used: set[str] = set()
    squad: list[dict] = []
    # top order gets a touch more punch, tail a touch less
    spread = {0: 4, 1: 3, 2: 1, 3: 2, 4: 0, 5: -1, 6: -2, 7: 0, 8: 0, 9: -1, 10: -3}
    for slot, role in enumerate(AI_TEMPLATE):
        base = {"Batsman": mean_bat, "Wicketkeeper": mean_bat - 4, "AllRounder": mean_ar,
                "Bowler": mean_bowl}[role]
        target = max(25.0, min(97.0, base + delta + spread[slot] + rng.uniform(-3.0, 3.0)))
        candidates = [p for p in by_role.get(role, []) if p["name"] not in used]
        if not candidates:
            candidates = [p for p in usable if p["name"] not in used]
        candidates.sort(key=lambda p: abs(_level_for(role, int(p.get("bat_level") or 0), int(p.get("bowl_level") or 0)) - target))
        top = candidates[:4] or candidates
        choice = rng.choice(top) if top else synthetic_pool(rng, 1)[0]
        used.add(choice["name"])
        squad.append(make_player(choice, slot, entity_id=int(choice.get("player_id") or 0), kind="global"))
    # make sure at least five of them can bowl (4 overs each -> 20 overs)
    if sum(1 for p in squad if can_bowl(p)) < 5:
        for p in squad:
            if not can_bowl(p) and p["role"] == "AllRounder":
                p["bw"] = rng.choice(["RAM", "RAO"])
                p["bowl"] = max(p["bowl"], 45)
    return squad


def user_team_name(first_name: str | None, franchise: str | None) -> str:
    name = plain(franchise, 26)
    if name:
        return name
    first = plain(first_name, 16)
    return f"{first}'s XI" if first else "Your XI"
