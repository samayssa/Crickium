"""Ball-by-ball commentary for the Mini App "Play Game" mode.

All real lines are written by `engines/commentary_studio.py` (the one commentary
engine used by every Crickium game mode).  This module keeps the small API the
Mini App engine already calls: ball_line / over_summary / milestone_line.
Plain text only (the Mini App renders it itself).
"""

from __future__ import annotations

import random

from engines import commentary_banks as B
from engines.commentary_studio import miniapp_ball_commentary, simple_ball_line, surname

_NAME_KIND = {0: "dot", 1: "single", 2: "double", 3: "triple", 4: "four", 6: "six", "W": "wicket", "WD": "wide", "NB": "no_ball", "BY": "bye", "LB": "leg_bye"}


def ball_line(outcome, *, bat: str, bowl: str, runs: int = 0, wicket_kind: str | None = None, free_hit: bool = False,
              rng: random.Random | None = None, ctx: dict | None = None) -> str:
    """`ctx` carries the match state (see deliver_one); without it only a names-only line is possible."""
    if ctx is not None:
        try:
            return miniapp_ball_commentary(rng=rng, free_hit=free_hit, outcome=outcome, **ctx)
        except Exception as exc:                                                  
            print(f"[miniapp commentary] studio failed, simple line used: {exc!r}")
    return simple_ball_line(_NAME_KIND.get(outcome, "dot"), bat, bowl, runs)


def over_summary(over_no: int, runs: int, wickets: int, bowler: str, maiden: bool, rng: random.Random | None = None) -> str:
    rnd = rng or random
    w = bowler or "the bowler"
    rw = f"{runs} run{'s' if runs != 1 else ''}"
    if maiden:
        line = rnd.choice(B.MAIDEN)
    elif wickets >= 3:
        line = rnd.choice(B.OVER_WICKETS_3)
    elif wickets == 2:
        line = rnd.choice(B.OVER_WICKETS_2)
    elif wickets == 1:
        line = rnd.choice(B.OVER_WICKET) if runs <= 8 else "{W} gets a wicket, but {orunw} come from the over."
    elif runs >= 14:
        line = rnd.choice(B.OVER_EXPENSIVE)
    elif runs <= 4:
        line = rnd.choice(B.OVER_TIGHT)
    else:
        line = f"{{W}} concedes {{orunw}} in that over."
    line = line.replace("{W}", w).replace("{orunw}", rw).replace("{orun}", str(runs)).replace("{w}", surname(w))
    return f"Over {over_no}: {line}"


def milestone_line(kind: str, name: str, value: int | str = "", rng: random.Random | None = None) -> str:
    rnd = rng or random
    pools = {"fifty": B.MILE_FIFTY, "hundred": B.MILE_HUNDRED, "hat_trick": B.HAT_TRICK, "partnership": B.WK_PARTNERSHIP,
             "three_wickets": B.WK_BOWLER[3], "five_wickets": B.WK_BOWLER[5]}
    pool = pools.get(kind)
    if not pool:
        return ""
    line = rnd.choice(pool)
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    runs = "100" if kind == "hundred" else "50"
    if not digits and "{balls}" in line:                                                               
        line = f"{'A CENTURY' if kind == 'hundred' else 'FIFTY'} for {{B}}!"
    for k, v in {"{B}": name, "{W}": name, "{b}": surname(name), "{w}": surname(name), "{runs}": runs, "{balls}": digits, "{p}": digits}.items():
        line = line.replace(k, v)
    return line
