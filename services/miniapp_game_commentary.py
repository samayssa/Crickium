"""Ball-by-ball commentary for the Mini App "Play Game" mode.

Plain text only (the Mini App renders it itself).  Pure functions - every line
is picked with the match's own random generator so a replayed seed gives the
same commentary.
"""

from __future__ import annotations

import random

_DOT = [
    "{bat} defends it solidly, no run.",
    "Dot ball. {bowl} keeps it tight to {bat}.",
    "{bat} leaves that one alone.",
    "Beaten! {bowl} gets one past the outside edge.",
    "Good length from {bowl}, {bat} pushes it back.",
    "{bat} plays and misses, great delivery.",
    "Straight to the fielder, no run.",
    "{bowl} hits the seam, {bat} cannot get it away.",
    "Tight line from {bowl}. Nothing on offer for {bat}.",
]
_SINGLE = [
    "{bat} nudges it into the gap for a single.",
    "Worked away for one by {bat}.",
    "Quick single taken, the strike changes.",
    "{bat} pushes it to the off side and scampers one.",
    "Dropped at the feet and {bat} sets off for a run.",
    "Turned to leg for a single, {bat} keeps the board moving.",
]
_DOUBLE = [
    "{bat} places it in the gap and comes back for two.",
    "Good running! Two runs for {bat}.",
    "Pushed hard into the deep, they get back for the second.",
]
_TRIPLE = [
    "Superb running! {bat} picks up three.",
    "In the gap and they run three, the fielder hauls it in at the rope.",
]
_FOUR = [
    "FOUR! {bat} threads the gap beautifully.",
    "FOUR! Crisp drive from {bat}, races to the fence.",
    "FOUR! {bat} pulls it away, no stopping that.",
    "FOUR! Short from {bowl} and {bat} cuts it past point.",
    "FOUR! Timed to perfection by {bat}.",
    "FOUR! Edged, but it flies past the keeper for a boundary.",
    "FOUR! {bat} lofts it over the infield and it runs away.",
]
_SIX = [
    "SIX! {bat} launches {bowl} into the stands!",
    "SIX! Massive hit from {bat}, that is out of the ground!",
    "SIX! {bat} clears the rope with ease.",
    "SIX! Pure power from {bat}, over long-on!",
    "SIX! {bowl} errs in length and {bat} sends it flying.",
    "SIX! What a strike, that is miles back!",
]
_WIDE = [
    "Wide! {bowl} strays down the leg side.",
    "Wide ball, {bowl} loses the line.",
    "Too far outside off, the umpire signals a wide.",
]
_NOBALL = [
    "No ball! {bowl} oversteps. Free hit coming up.",
    "No ball called, and a free hit follows.",
]
_BYE = [
    "Byes! The keeper cannot gather and they scamper through.",
    "Misses everything, the ball runs away for a bye.",
]
_LEGBYE = [
    "Leg bye! Off the pads and they take a run.",
    "Struck on the pad, they sneak a leg bye.",
]
_WICKET = {
    "bowled": [
        "BOWLED! {bowl} crashes through {bat}'s defences!",
        "BOWLED! The stumps are shattered, {bat} has to go.",
        "CLEAN BOWLED! {bat} is beaten all ends up.",
    ],
    "lbw": [
        "LBW! {bowl} traps {bat} in front, the finger goes up.",
        "Plumb in front! {bat} is out lbw.",
    ],
    "caught": [
        "OUT! {bat} goes for the big shot and is caught.",
        "CAUGHT! {bat} finds the fielder, {bowl} strikes.",
        "OUT! A mistimed hit from {bat}, and the catch is taken.",
    ],
    "stumped": [
        "STUMPED! {bat} is out of the crease and the keeper whips off the bails.",
        "Lightning work behind the stumps, {bat} is stumped.",
    ],
    "run_out": [
        "RUN OUT! A mix-up in the middle and {bat} is short of the crease.",
        "Direct hit! {bat} is run out.",
    ],
}
_FREE_HIT = {
    0: ["Free hit, but only a dot."],
    1: ["Free hit, they take a single."],
}


def _pick(rng: random.Random, lines: list[str]) -> str:
    return rng.choice(lines)


def ball_line(outcome: str, *, bat: str, bowl: str, runs: int = 0,
              wicket_kind: str | None = None, free_hit: bool = False,
              rng: random.Random | None = None) -> str:
    """One commentary sentence for a delivery.

    outcome is the engine code: 0,1,2,3,4,6,'W','WD','NB','LB','BY'.
    """
    rnd = rng or random
    fmt = {"bat": bat or "The batter", "bowl": bowl or "the bowler"}
    key = outcome
    if key == "W":
        kind = wicket_kind if wicket_kind in _WICKET else "caught"
        return _pick(rnd, _WICKET[kind]).format(**fmt)
    table = {0: _DOT, 1: _SINGLE, 2: _DOUBLE, 3: _TRIPLE, 4: _FOUR, 6: _SIX,
             "WD": _WIDE, "NB": _NOBALL, "BY": _BYE, "LB": _LEGBYE}
    text = _pick(rnd, table.get(key, _DOT)).format(**fmt)
    if free_hit and key in (0, 1):
        text = _pick(rnd, _FREE_HIT[key]) + " " + text
    elif free_hit and key in (4, 6):
        text = "Free hit! " + text
    return text


def over_summary(over_no: int, runs: int, wickets: int, bowler: str, maiden: bool) -> str:
    if maiden:
        return f"Over {over_no}: MAIDEN by {bowler}. Superb pressure."
    if wickets >= 2:
        return f"Over {over_no}: {bowler} strikes twice, {runs} run{'s' if runs != 1 else ''} and {wickets} wickets."
    if wickets == 1:
        return f"Over {over_no}: {runs} run{'s' if runs != 1 else ''} and a wicket for {bowler}."
    if runs >= 18:
        return f"Over {over_no}: {runs} runs! {bowler} is taken apart."
    if runs >= 12:
        return f"Over {over_no}: an expensive one, {runs} runs off {bowler}."
    if runs <= 4:
        return f"Over {over_no}: tidy bowling by {bowler}, just {runs}."
    return f"Over {over_no}: {runs} runs off {bowler}."


def milestone_line(kind: str, name: str, value: int | str = "") -> str:
    lines = {
        "fifty": f"FIFTY for {name}! A superb knock, {value} off it.",
        "hundred": f"HUNDRED! {name} reaches a magnificent century!",
        "three_wickets": f"{name} picks up his third wicket!",
        "five_wickets": f"FIVE-WICKET HAUL for {name}! Unbelievable spell!",
        "hat_trick": f"HAT-TRICK! {name} takes three in three balls!",
        "partnership": f"A {value}-run stand has taken the game forward.",
    }
    return lines.get(kind, "")
