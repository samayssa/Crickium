"""Pure tournament logic (no database, no Telegram).

Everything that can be decided with plain data lives here so it can be unit
tested without a bot: money formatting, the IPL bid ladder, auction pool/set
generation, squad rules, fixtures, the points table (with NRR), playoff
brackets, match-statistic extraction and the match-day clock.

Prices are integers in **lakh** (1 Cr = 100 L).
"""
from __future__ import annotations

import math
import random
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
TZ_MIN_IST = 330
BID_SECONDS = 30                  # every bid restarts this countdown
BETWEEN_LOTS_SECONDS = 2          # the next player arrives 2 s after a lot closes (the presenter still respects Telegram's group rate limit)
GOING_ONCE_AT = 10                # seconds left -> "going once"
GOING_TWICE_AT = 5                # seconds left -> "going twice"

POINTS_WIN = 2
POINTS_NO_RESULT = 1
POINTS_TIE = 1
POINTS_WALKOVER = 2
POINTS_ABANDONED = 0              # both players were absent

WINDOW_START_MIN = 17 * 60        # match day opens 5 PM
WINDOW_END_MIN = 21 * 60          # ... and closes 9 PM
REMIND_BEFORE_MIN = 60            # "your time is going" 1 h before the close
LATE_START_GUARD_MIN = 90         # a tournament started later than (end - this) begins tomorrow

WINDOW_PRESETS = (
    (17 * 60, 21 * 60),
    (18 * 60, 22 * 60),
    (19 * 60, 23 * 60),
    (15 * 60, 19 * 60),
)

# Official IPL auction ladder: (exclusive upper bound in lakh, step in lakh).
OFFICIAL_LADDER: tuple[tuple[int | None, int], ...] = ((100, 5), (200, 10), (500, 20), (None, 25))
FAST_LADDER: tuple[tuple[int | None, int], ...] = ((100, 10), (200, 20), (500, 25), (None, 50))
LADDER_PRESETS = {
    "ipl": ("IPL official", OFFICIAL_LADDER),
    "fast": ("Fast auction", FAST_LADDER),
}

ROLE_BAT, ROLE_BOWL, ROLE_AR, ROLE_WK = "Batsman", "Bowler", "AllRounder", "Wicketkeeper"
ROLES = (ROLE_BAT, ROLE_WK, ROLE_AR, ROLE_BOWL)
ROLE_SHORT = {ROLE_BAT: "BAT", ROLE_WK: "WK", ROLE_AR: "AR", ROLE_BOWL: "BOWL"}
ROLE_ICON = {ROLE_BAT: "🏏", ROLE_WK: "🧤", ROLE_AR: "🔄", ROLE_BOWL: "⚡"}
ROLE_LABEL = {ROLE_BAT: "Batter", ROLE_WK: "Wicket-keeper", ROLE_AR: "All-rounder", ROLE_BOWL: "Bowler"}

ENGINE_PROFILES: dict[str, dict[str, Any]] = {
    "PLAYIPL": {
        "engine": "PLAYIPL", "pool_key": "IPL", "preset": "ipl", "short": "IPL",
        "title": "Indian Premier League", "icon": "🏏", "auction": True,
        "purse_lakh": 12000, "purse_presets": (6000, 9000, 10000, 12000, 12500),
        "squad_min": 18, "squad_max": 25, "overseas_squad_max": 8, "xi_overseas_max": 4,
        "min_teams": 2, "max_teams": 10,
        "base_tiers": ((0.03, 200), (0.08, 150), (0.15, 125), (0.25, 100), (0.40, 75), (0.60, 50), (0.80, 30), (1.00, 20)),
    },
    "PLAYWPL": {
        "engine": "PLAYWPL", "pool_key": "WPL", "preset": "wpl", "short": "WPL",
        "title": "Women's Premier League", "icon": "👑", "auction": True,
        "purse_lakh": 1500, "purse_presets": (1000, 1250, 1500, 1800),
        "squad_min": 15, "squad_max": 18, "overseas_squad_max": 6, "xi_overseas_max": 4,
        "min_teams": 2, "max_teams": 5,
        "base_tiers": ((0.05, 50), (0.15, 40), (0.35, 30), (0.65, 20), (1.00, 10)),
    },
    "PLAYINT": {
        "engine": "PLAYINT", "pool_key": "T20I", "preset": "t20wc", "short": "T20 WC",
        "title": "T20 World Cup", "icon": "🌍", "auction": True,
        "purse_lakh": 10000, "purse_presets": (5000, 8000, 10000, 12000),
        "squad_min": 15, "squad_max": 20, "overseas_squad_max": 0, "xi_overseas_max": 0,
        "min_teams": 2, "max_teams": 16,
        "base_tiers": ((0.03, 200), (0.08, 150), (0.15, 125), (0.25, 100), (0.40, 75), (0.60, 50), (0.80, 30), (1.00, 20)),
    },
    "PLAY": {
        "engine": "PLAY", "pool_key": "CUSTOM", "preset": "custom", "short": "Custom",
        "title": "Custom (regular /play game)", "icon": "🎮", "auction": True,
        "purse_lakh": 10000, "purse_presets": (5000, 8000, 10000, 12000),
        "squad_min": 15, "squad_max": 20, "overseas_squad_max": 0, "xi_overseas_max": 0,
        "min_teams": 2, "max_teams": 16,
        "base_tiers": ((0.03, 200), (0.08, 150), (0.15, 125), (0.25, 100), (0.40, 75), (0.60, 50), (0.80, 30), (1.00, 20)),
    },
}
PRESET_ENGINE = {"ipl": "PLAYIPL", "wpl": "PLAYWPL", "t20wc": "PLAYINT", "custom": "PLAY"}

STAGE_LABELS = {"league": "League", "Q1": "Qualifier 1", "EL": "Eliminator", "Q2": "Qualifier 2", "F": "Final"}
STAGE_SHORT = {"league": "League", "Q1": "Q1", "EL": "EL", "Q2": "Q2", "F": "Final"}


def profile(engine: str) -> dict[str, Any]:
    return ENGINE_PROFILES.get(str(engine or "").upper()) or ENGINE_PROFILES["PLAYIPL"]


# ---------------------------------------------------------------------------
# money
# ---------------------------------------------------------------------------
def fmt_money(lakh: int | float | None) -> str:
    """75 -> '₹75 L', 100 -> '₹1 Cr', 125 -> '₹1.25 Cr'."""
    value = int(round(float(lakh or 0)))
    if value >= 100:
        crore = f"{value / 100:.2f}".rstrip("0").rstrip(".")
        return f"₹{crore} Cr"
    return f"₹{value} L"


_MONEY_RE = re.compile(r"^\s*₹?\s*(\d+(?:[.,]\d+)?)\s*(cr|crore|crores|c|l|lac|lacs|lakh|lakhs)?\s*$", re.I)


def parse_money(text: Any, default_unit: str = "cr") -> int | None:
    """'120' (crore) / '2.5cr' / '75 lakh' / '₹5L' -> lakh, or None."""
    match = _MONEY_RE.match(str(text or ""))
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    unit = (match.group(2) or default_unit).lower()
    lakh = number * 100 if unit.startswith("c") else number
    lakh = int(round(lakh))
    return lakh if 1 <= lakh <= 1_000_000 else None


# ---------------------------------------------------------------------------
# bid ladder
# ---------------------------------------------------------------------------
def normalize_ladder(raw: Any) -> tuple[tuple[int | None, int], ...]:
    """Accept lists/tuples (e.g. what JSON gives back) and validate the ladder."""
    if not raw:
        return OFFICIAL_LADDER
    rows: list[tuple[int | None, int]] = []
    try:
        for bound, step in raw:
            rows.append((None if bound is None else int(bound), int(step)))
    except Exception:
        return OFFICIAL_LADDER
    if not rows or any(step <= 0 for _b, step in rows):
        return OFFICIAL_LADDER
    bounds = [b for b, _s in rows[:-1]]
    if any(b is None for b in bounds) or bounds != sorted(bounds) or len(set(bounds)) != len(bounds):
        return OFFICIAL_LADDER
    rows[-1] = (None, rows[-1][1])
    return tuple(rows)


def step_for(price_lakh: int, ladder: Any = OFFICIAL_LADDER) -> int:
    for bound, step in normalize_ladder(ladder):
        if bound is None or price_lakh < bound:
            return step
    return normalize_ladder(ladder)[-1][1]


def next_bid(current_lakh: int, base_lakh: int, has_bid: bool, ladder: Any = OFFICIAL_LADDER) -> int:
    """The only price a team may bid next: the base price for the opening bid, then +step."""
    if not has_bid:
        return int(base_lakh)
    return int(current_lakh) + step_for(int(current_lakh), ladder)


def parse_ladder_text(text: Any) -> tuple[tuple[int | None, int], ...] | None:
    """'10' -> flat 10 L. '5 10 20 25' -> <1Cr, 1-2Cr, 2-5Cr, >5Cr steps (in lakh)."""
    numbers = re.findall(r"\d+(?:\.\d+)?", str(text or ""))
    try:
        values = [int(round(float(n))) for n in numbers]
    except ValueError:
        return None
    if any(v <= 0 or v > 500 for v in values):
        return None
    if len(values) == 1:
        return ((None, values[0]),)
    if len(values) == 4:
        return ((100, values[0]), (200, values[1]), (500, values[2]), (None, values[3]))
    return None


def ladder_rows(ladder: Any = OFFICIAL_LADDER) -> list[tuple[str, str]]:
    """[(range label, step label)] for display."""
    rows = []
    previous = 0
    for bound, step in normalize_ladder(ladder):
        if bound is None:
            rng = f"Above {fmt_money(previous)}" if previous else "Every bid"
        elif previous == 0:
            rng = f"Up to {fmt_money(bound)}"
        else:
            rng = f"{fmt_money(previous)} – {fmt_money(bound)}"
        rows.append((rng, f"+{fmt_money(step)}"))
        if bound is not None:
            previous = bound
    return rows


def ladder_text(ladder: Any = OFFICIAL_LADDER) -> str:
    return " · ".join(f"{a}: {b}" for a, b in ladder_rows(ladder))


def ladder_to_json(ladder: Any) -> list[list[int | None]]:
    return [[b, s] for b, s in normalize_ladder(ladder)]


# ---------------------------------------------------------------------------
# players
# ---------------------------------------------------------------------------
def norm_role(role: Any) -> str:
    raw = re.sub(r"[^a-z]", "", str(role or "").lower())
    if raw in {"wicketkeeper", "wk", "keeper", "wkbatter", "wkbatsman"}:
        return ROLE_WK
    if raw in {"allrounder", "ar", "allround"}:
        return ROLE_AR
    if raw in {"bowler", "bowl"}:
        return ROLE_BOWL
    return ROLE_BAT


def player_key(name: Any) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip()).casefold()


def is_overseas(country: Any) -> bool:
    from utils.overseas import is_overseas_country
    return is_overseas_country(country)


def bowl_kind(player: Mapping[str, Any]) -> str:
    hand = str(player.get("bowling_hand") or "").strip().upper()
    return "spin" if hand and hand[-1] in {"O", "L"} else "pace"


def player_value(player: Mapping[str, Any]) -> float:
    """A single 'how good is this card' number used for base prices and set tiers."""
    bat = float(player.get("bat_level") or 0)
    bowl = float(player.get("bowl_level") or 0)
    role = norm_role(player.get("role"))
    if role == ROLE_BAT:
        return bat + 0.15 * bowl
    if role == ROLE_WK:
        return bat + 3.0
    if role == ROLE_BOWL:
        return bowl + 0.15 * bat
    return max(bat, bowl) + 0.35 * min(bat, bowl)


def player_ovr(player: Mapping[str, Any]) -> int:
    return max(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))


# ---------------------------------------------------------------------------
# auction pool + sets
# ---------------------------------------------------------------------------
_CATEGORY_NAMES = {
    "BA": "Batters", "AL": "All-Rounders", "WK": "Wicket-Keepers", "FA": "Fast Bowlers", "SP": "Spinners",
}
POOL_MARQUEE, POOL_CAPPED, POOL_UNCAPPED, POOL_ACCEL = "Marquee Players", "Capped Players", "Uncapped Players", "Accelerated Round"


def _category(player: Mapping[str, Any]) -> str:
    role = norm_role(player.get("role"))
    if role == ROLE_WK:
        return "WK"
    if role == ROLE_AR:
        return "AL"
    if role == ROLE_BAT:
        return "BA"
    return "SP" if bowl_kind(player) == "spin" else "FA"


def assign_base_prices(players: list[dict[str, Any]], tiers: Sequence[tuple[float, int]], rng: random.Random) -> None:
    """Best players get the highest base price; the cut-offs are percentile based so it
    works for a 60-player WPL pool and a 250-player IPL pool alike."""
    if not players:
        return
    ranked = sorted(players, key=lambda p: (-float(p.get("value") or 0), rng.random()))
    total = len(ranked)
    for index, player in enumerate(ranked):
        fraction = (index + 1) / total
        base = tiers[-1][1]
        for limit, price in tiers:
            if fraction <= limit + 1e-9:
                base = price
                break
        player["base_lakh"] = int(base)


def _split_sizes(total: int, rng: random.Random, low: int = 6, high: int = 10) -> list[int]:
    if total <= 0:
        return []
    if total <= high:
        return [total]
    mean = rng.uniform(low + 1, high - 1)
    count = max(2, int(round(total / mean)))
    base, extra = divmod(total, count)
    sizes = [base + (1 if i < extra else 0) for i in range(count)]
    rng.shuffle(sizes)
    return sizes


def _weighted_sample(items: Sequence[Any], weights: Sequence[float], k: int, rng: random.Random) -> list[Any]:
    """Weighted sampling without replacement (Efraimidis-Spirakis)."""
    keyed = []
    for item, weight in zip(items, weights):
        w = max(float(weight), 1e-6)
        keyed.append((rng.random() ** (1.0 / w), item))
    keyed.sort(key=lambda x: x[0], reverse=True)
    return [item for _k, item in keyed[:k]]


def build_auction_sets(players: Iterable[Mapping[str, Any]], prof: Mapping[str, Any], rng: random.Random | None = None) -> list[dict[str, Any]]:
    """Turn a flat player list into a realistic auction running order.

    Marquee sets first, then the 'capped' sets (batters, all-rounders, keepers,
    fast bowlers, spinners - the five tracks interleave in a random order),
    then the 'uncapped' sets. Every player appears exactly once, and every call
    produces a different order.
    """
    rng = rng or random.SystemRandom()
    seen: set[str] = set()
    pool: list[dict[str, Any]] = []
    for raw in players:
        key = player_key(raw.get("name"))
        if not key or key in seen:
            continue
        seen.add(key)
        item = dict(raw)
        item["key"] = key
        item["role"] = norm_role(item.get("role"))
        item["value"] = player_value(item)
        item["overseas"] = bool(is_overseas(item.get("country")))
        item["cat"] = _category(item)
        pool.append(item)
    if not pool:
        return []
    assign_base_prices(pool, tuple(prof.get("base_tiers") or ((1.0, 20),)), rng)

    sets: list[dict[str, Any]] = []

    def add_set(code: str, name: str, pool_name: str, lots: list[dict[str, Any]]) -> None:
        shuffled = list(lots)
        rng.shuffle(shuffled)
        sets.append({"code": code, "name": name, "pool": pool_name, "lots": shuffled})

    ordered = sorted(pool, key=lambda p: -p["value"])
    remaining = list(pool)

    # --- marquee ---------------------------------------------------------
    marquee_total = 0
    if len(pool) >= 16:
        marquee_total = max(4, min(16, int(round(len(pool) * 0.06))))
        candidates = ordered[: max(marquee_total * 2, marquee_total + 2)]
        weights = [float(len(candidates) - i) for i in range(len(candidates))]
        marquee = _weighted_sample(candidates, weights, marquee_total, rng)
        picked = {p["key"] for p in marquee}
        remaining = [p for p in pool if p["key"] not in picked]
        marquee_sets = max(1, math.ceil(marquee_total / 6))
        chunks = [marquee[i::marquee_sets] for i in range(marquee_sets)]
        for number, chunk in enumerate(chunks, start=1):
            add_set(f"M{number}", f"Marquee Set {number}", POOL_MARQUEE, chunk)

    # --- category tracks -------------------------------------------------
    tracks_capped: dict[str, list[list[dict[str, Any]]]] = {}
    tracks_uncapped: dict[str, list[list[dict[str, Any]]]] = {}
    for cat in _CATEGORY_NAMES:
        members = sorted((p for p in remaining if p["cat"] == cat), key=lambda p: -p["value"])
        if not members:
            continue
        cut = len(members) if len(members) < 10 else int(math.ceil(len(members) * 0.6))
        for bucket, store in ((members[:cut], tracks_capped), (members[cut:], tracks_uncapped)):
            if not bucket:
                continue
            sizes = _split_sizes(len(bucket), rng)
            chunks_for_cat, start = [], 0
            for size in sizes:
                chunks_for_cat.append(bucket[start:start + size])
                start += size
            store[cat] = chunks_for_cat

    def emit(store: dict[str, list[list[dict[str, Any]]]], pool_name: str, prefix: str) -> None:
        depth = max((len(v) for v in store.values()), default=0)
        for level in range(depth):
            cats = [c for c in store if level < len(store[c])]
            rng.shuffle(cats)
            for cat in cats:
                label = _CATEGORY_NAMES[cat]
                add_set(f"{prefix}{cat}{level + 1}", f"{'Uncapped ' if prefix == 'U' else ''}{label} {level + 1}", pool_name, store[cat][level])

    emit(tracks_capped, POOL_CAPPED, "")
    emit(tracks_uncapped, POOL_UNCAPPED, "U")

    for idx, item in enumerate(sets):
        item["idx"] = idx
    return sets


def build_accelerated_sets(unsold: Iterable[Mapping[str, Any]], rng: random.Random | None = None) -> list[dict[str, Any]]:
    """Unsold players come back once, shuffled, at their old base price."""
    rng = rng or random.SystemRandom()
    items = [dict(p) for p in unsold]
    rng.shuffle(items)
    sets, start = [], 0
    for number, size in enumerate(_split_sizes(len(items), rng, 6, 12), start=1):
        sets.append({"code": f"AR{number}", "name": f"Accelerated Round {number}", "pool": POOL_ACCEL, "lots": items[start:start + size]})
        start += size
    return sets


_CARD_KEYS = ("cat", "value", "ovr", "rarity", "image", "photo", "card_id", "team", "age", "style",
              "player_kind", "edition", "special_edition_id", "showcase_card_id", "showcase_set_id", "showcase_name", "player_id")


def flatten_sets(sets: Sequence[Mapping[str, Any]], round_no: int = 1) -> list[dict[str, Any]]:
    """Running order -> one flat row per lot (what the database stores). ``ord`` counts from 1."""
    out: list[dict[str, Any]] = []
    for set_index, auction_set in enumerate(sets):
        lots = list(auction_set.get("lots") or [])
        for position, player in enumerate(lots, start=1):
            card = {k: player[k] for k in _CARD_KEYS if player.get(k) is not None}
            card["ovr"] = player_ovr(player)
            out.append({
                "ord": len(out) + 1,
                "round": int(round_no),
                "set_idx": int(auction_set.get("idx", set_index)),
                "set_code": str(auction_set.get("code") or ""),
                "set_name": str(auction_set.get("name") or ""),
                "pool_name": str(auction_set.get("pool") or ""),
                "set_pos": position,
                "set_size": len(lots),
                "pkey": str(player.get("key") or player.get("pkey") or player_key(player.get("name"))),
                "name": str(player.get("name") or "Player"),
                "role": norm_role(player.get("role")),
                "country": player.get("country"),
                "bat_level": int(player.get("bat_level") or 0),
                "bowl_level": int(player.get("bowl_level") or 0),
                "batting_hand": player.get("batting_hand"),
                "bowling_hand": player.get("bowling_hand"),
                "overseas": bool(player.get("overseas")),
                "card": card,
                "base_lakh": int(player.get("base_lakh") or 20),
            })
    return out


def accelerated_lots(unsold_rows: Iterable[Mapping[str, Any]], rng: random.Random | None = None) -> list[dict[str, Any]]:
    """Lots for the accelerated round, from the lot rows of players nobody bought (base price kept)."""
    sets = build_accelerated_sets(unsold_rows, rng)
    for index, auction_set in enumerate(sets):
        auction_set["idx"] = index
    return flatten_sets(sets)


# ---------------------------------------------------------------------------
# squad rules
# ---------------------------------------------------------------------------
def select_pool(players: Iterable[Mapping[str, Any]], target: int, rng: random.Random | None = None) -> list[dict[str, Any]]:
    """A quality-weighted random subset (a different one every auction) of about ``target`` players.

    The best ~12 % are always in (a real auction always has its stars); the rest is drawn at random with
    better players being likelier. Every player appears at most once (duplicates by name are dropped)."""
    rng = rng or random.SystemRandom()
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for raw in players:
        key = player_key(raw.get("name"))
        if not key or key in seen:
            continue
        seen.add(key)
        item = dict(raw)
        item["_v"] = player_value(item)
        items.append(item)
    target = max(1, int(target))
    if len(items) > target:
        items.sort(key=lambda p: -p["_v"])
        sure = max(6, int(target * 0.12))
        chosen = items[:sure]
        rest = items[sure:]
        low, high = rest[-1]["_v"], rest[0]["_v"]
        span = max(high - low, 1e-6)
        weights = [((p["_v"] - low) / span) ** 1.5 + 0.12 for p in rest]
        chosen += _weighted_sample(rest, weights, target - len(chosen), rng)
        items = chosen
    for item in items:
        item.pop("_v", None)
    rng.shuffle(items)
    return items


def pool_target(n_teams: int, squad_max: int, pool_size: int) -> int:
    """How many players go under the hammer: enough for every squad to fill (with some left unsold)."""
    wanted = int(math.ceil(int(n_teams) * int(squad_max) * 1.5))
    return max(1, min(int(pool_size), max(60, wanted), 320))


def pool_is_enough(n_teams: int, squad_min: int, pool_size: int) -> bool:
    return int(pool_size) >= int(math.ceil(int(n_teams) * int(squad_min) * 1.15))


def role_counts(players: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = {r: 0 for r in ROLES}
    for p in players:
        counts[norm_role(p.get("role"))] += 1
    return counts


def xi_feasible(players: Sequence[Mapping[str, Any]], xi_overseas_max: int = 0) -> bool:
    """Can a legal XI (the engines' own rule) be picked from this squad?

    Rule: 11 players, Batsman >= 1, 2 <= AllRounder <= 4, 3 <= Bowler <= 4,
    Wicketkeeper >= 1 and (franchise leagues) at most N overseas players.
    """
    have: dict[str, list[int]] = {r: [0, 0] for r in ROLES}      # [home-born, overseas]
    for p in players:
        role = norm_role(p.get("role"))
        have[role][1 if is_overseas(p.get("country")) and xi_overseas_max else 0] += 1
    total = {r: have[r][0] + have[r][1] for r in ROLES}
    if len(players) < 11:
        return False
    for n_ar in range(2, min(4, total[ROLE_AR]) + 1):
        for n_bowl in range(3, min(4, total[ROLE_BOWL]) + 1):
            for n_wk in range(1, total[ROLE_WK] + 1):
                n_bat = 11 - n_ar - n_bowl - n_wk
                if n_bat < 1 or n_bat > total[ROLE_BAT]:
                    continue
                if not xi_overseas_max:
                    return True
                overseas_needed = (
                    max(0, n_ar - have[ROLE_AR][0]) + max(0, n_bowl - have[ROLE_BOWL][0])
                    + max(0, n_wk - have[ROLE_WK][0]) + max(0, n_bat - have[ROLE_BAT][0])
                )
                if overseas_needed <= xi_overseas_max:
                    return True
    return False


def _is_os(player: Mapping[str, Any], xi_overseas_max: int) -> bool:
    return bool(xi_overseas_max) and bool(is_overseas(player.get("country")))


def pick_best_xi(players: Sequence[Mapping[str, Any]], xi_overseas_max: int = 0) -> list[dict[str, Any]] | None:
    """The strongest legal XI of a squad (same rule as :func:`xi_feasible`), or None if there is none."""
    pool = [dict(p) for p in players]
    for p in pool:
        p["_v"] = player_value(p)
        p["_r"] = norm_role(p.get("role"))
        p["_os"] = _is_os(p, xi_overseas_max)
    by_role = {r: sorted((p for p in pool if p["_r"] == r), key=lambda p: -p["_v"]) for r in ROLES}
    best: list[dict[str, Any]] | None = None
    best_score = -1.0
    for n_ar in (2, 3, 4):
        for n_bowl in (3, 4):
            for n_wk in (1, 2, 3):
                n_bat = 11 - n_ar - n_bowl - n_wk
                counts = {ROLE_BAT: n_bat, ROLE_WK: n_wk, ROLE_AR: n_ar, ROLE_BOWL: n_bowl}
                if n_bat < 1 or any(len(by_role[r]) < counts[r] for r in ROLES):
                    continue
                pick = {r: list(by_role[r][:counts[r]]) for r in ROLES}
                if xi_overseas_max:
                    guard = 0
                    while sum(1 for r in ROLES for p in pick[r] if p["_os"]) > xi_overseas_max and guard < 40:
                        guard += 1
                        swap = None            # (loss, role, out, in)
                        for r in ROLES:
                            chosen_ids = {id(p) for p in pick[r]}
                            home = [p for p in by_role[r] if not p["_os"] and id(p) not in chosen_ids]
                            if not home:
                                continue
                            for out in (p for p in pick[r] if p["_os"]):
                                loss = out["_v"] - home[0]["_v"]
                                if swap is None or loss < swap[0]:
                                    swap = (loss, r, out, home[0])
                        if swap is None:
                            break
                        _loss, r, out, incoming = swap
                        pick[r][pick[r].index(out)] = incoming
                    if sum(1 for r in ROLES for p in pick[r] if p["_os"]) > xi_overseas_max:
                        continue
                xi = [p for r in ROLES for p in pick[r]]
                score = sum(p["_v"] for p in xi)
                if score > best_score:
                    best, best_score = xi, score
    if best is None:
        return None
    out = []
    for p in best:
        item = {k: v for k, v in p.items() if not k.startswith("_")}
        out.append(item)
    return out


def plan_autofill(squad: Sequence[Mapping[str, Any]], candidates: Iterable[Mapping[str, Any]], rules: Mapping[str, Any],
                  purse_left: int) -> list[tuple[dict[str, Any], int]]:
    """Players (and the price they cost) to add so the squad reaches its minimum size and can field a legal XI.

    ``candidates`` are players nobody bought; each has ``base_lakh``. ``rules``: squad_min, squad_max,
    overseas_squad_max, xi_overseas_max."""
    have = [dict(p) for p in squad]
    pool = sorted((dict(c) for c in candidates), key=lambda p: -player_value(p))
    squad_min, squad_max = int(rules.get("squad_min") or 0), int(rules.get("squad_max") or 99)
    os_cap, xi_cap = int(rules.get("overseas_squad_max") or 0), int(rules.get("xi_overseas_max") or 0)
    added: list[tuple[dict[str, Any], int]] = []
    purse = int(purse_left)

    def short() -> bool:
        return len(have) < squad_min or not xi_feasible(have, xi_cap)

    while short() and len(have) < squad_max and pool:
        overseas_now = sum(1 for p in have if is_overseas(p.get("country")))
        chosen = None
        for role in roles_needed(have, squad_min):
            for cand in pool:
                if norm_role(cand.get("role")) != role:
                    continue
                if os_cap and is_overseas(cand.get("country")) and overseas_now >= os_cap:
                    continue
                chosen = cand
                break
            if chosen is not None:
                break
        if chosen is None:
            break
        pool.remove(chosen)
        price = max(0, min(int(chosen.get("base_lakh") or 0), purse))
        purse -= price
        have.append(chosen)
        added.append((chosen, price))
    return added


_IDEAL_SHARE = {ROLE_BAT: 0.28, ROLE_WK: 0.10, ROLE_AR: 0.24, ROLE_BOWL: 0.38}


def roles_needed(players: Sequence[Mapping[str, Any]], target_size: int) -> list[str]:
    """Roles to add next, most useful first (used by the end-of-auction squad completion)."""
    counts = role_counts(players)
    hard = []
    if counts[ROLE_WK] < 1:
        hard.append(ROLE_WK)
    if counts[ROLE_BAT] < 1:
        hard.append(ROLE_BAT)
    if counts[ROLE_AR] < 2:
        hard.append(ROLE_AR)
    if counts[ROLE_BOWL] < 3:
        hard.append(ROLE_BOWL)
    size = max(int(target_size), len(players) + 1)
    soft = sorted(ROLES, key=lambda r: counts[r] - _IDEAL_SHARE[r] * size)
    return hard + [r for r in soft if r not in hard]


def max_bid_for_team(purse_left: int, squad_count: int, squad_min: int, min_base: int) -> int:
    """Highest price a team may pay *now* while still being able to afford the cheapest
    players it needs to reach its minimum squad size."""
    slots_after = max(0, int(squad_min) - int(squad_count) - 1)
    return int(purse_left) - slots_after * int(min_base)


def check_bid_allowed(team: Mapping[str, Any], player: Mapping[str, Any], price: int, rules: Mapping[str, Any]) -> tuple[bool, str]:
    """(ok, reason). Reasons: squad_full / overseas_full / purse."""
    if int(team.get("squad_count") or 0) >= int(rules.get("squad_max") or 99):
        return False, "squad_full"
    cap = int(rules.get("overseas_squad_max") or 0)
    if cap and player.get("overseas") and int(team.get("overseas_count") or 0) >= cap:
        return False, "overseas_full"
    limit = max_bid_for_team(int(team.get("purse_left") or 0), int(team.get("squad_count") or 0),
                             int(rules.get("squad_min") or 0), int(rules.get("min_base") or 0))
    if price > limit:
        return False, "purse"
    return True, ""


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def round_robin_rounds(team_ids: Sequence[int], rng: random.Random | None = None) -> list[list[tuple[int, int]]]:
    """Circle-method rounds; every team plays at most once per round (odd N -> a bye)."""
    rng = rng or random.Random()
    ids: list[int | None] = list(team_ids)
    rng.shuffle(ids)
    if len(ids) % 2:
        ids.append(None)
    n = len(ids)
    rounds: list[list[tuple[int, int]]] = []
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = ids[i], ids[n - 1 - i]
            if a is None or b is None:
                continue
            pairs.append((a, b) if (r + i) % 2 == 0 else (b, a))
        rounds.append(pairs)
        ids = [ids[0]] + [ids[-1]] + ids[1:-1]
    rng.shuffle(rounds)
    return rounds


def league_legs(n_teams: int) -> int:
    if n_teams <= 2:
        return 3
    if n_teams <= 4:
        return 2
    return 1


def build_league_fixtures(team_ids: Sequence[int], legs: int | None = None, rng: random.Random | None = None) -> list[dict[str, int]]:
    """[{day_no, seq, home, away}]; one round per match day."""
    rng = rng or random.Random()
    legs = int(legs or league_legs(len(team_ids)))
    fixtures: list[dict[str, int]] = []
    day = 0
    for leg in range(legs):
        rounds = round_robin_rounds(team_ids, rng)
        for pairs in rounds:
            day += 1
            for seq, (home, away) in enumerate(pairs, start=1):
                if leg % 2 == 1:
                    home, away = away, home
                fixtures.append({"day_no": day, "seq": seq, "home": int(home), "away": int(away)})
    return fixtures


def playoff_plan(n_teams: int) -> dict[str, Any]:
    if n_teams <= 2:
        return {"qualify": 0, "stages": []}
    if n_teams <= 4:
        return {"qualify": 2, "stages": ["F"]}
    return {"qualify": 4, "stages": ["Q1", "EL", "Q2", "F"]}


def next_playoff_fixtures(plan: Mapping[str, Any], seeds: Sequence[int], done: Mapping[str, Mapping[str, Any]], existing: Iterable[str]) -> list[dict[str, Any]]:
    """What playoff fixtures can be created right now?

    ``seeds``    team ids ordered by league position
    ``done``     {stage: {"winner": team_id, "loser": team_id}} for finished playoff matches
    ``existing`` stages that already have a fixture
    Returns [{stage, home, away, same_day_as_previous}].
    """
    existing = set(existing)
    stages = list(plan.get("stages") or [])
    out: list[dict[str, Any]] = []
    if not stages or len(seeds) < int(plan.get("qualify") or 0):
        return out
    if stages == ["F"]:
        if "F" not in existing:
            out.append({"stage": "F", "home": int(seeds[0]), "away": int(seeds[1]), "new_day": True})
        return out
    if "Q1" not in existing:
        out.append({"stage": "Q1", "home": int(seeds[0]), "away": int(seeds[1]), "new_day": True})
    if "EL" not in existing:
        out.append({"stage": "EL", "home": int(seeds[2]), "away": int(seeds[3]), "new_day": False})
    if "Q2" not in existing and "Q1" in done and "EL" in done:
        out.append({"stage": "Q2", "home": int(done["Q1"]["loser"]), "away": int(done["EL"]["winner"]), "new_day": True})
    if "F" not in existing and "Q1" in done and "Q2" in done:
        out.append({"stage": "F", "home": int(done["Q1"]["winner"]), "away": int(done["Q2"]["winner"]), "new_day": True})
    return out


# ---------------------------------------------------------------------------
# points table
# ---------------------------------------------------------------------------
def _overs_balls(balls: int, wickets: int, limit_balls: int) -> int:
    """All out -> the team is charged the full quota, otherwise the balls actually faced."""
    return limit_balls if int(wickets) >= 10 else int(balls)


def compute_table(teams: Sequence[Mapping[str, Any]], fixtures: Sequence[Mapping[str, Any]], overs_limit: int = 20) -> list[dict[str, Any]]:
    """League table from finished league fixtures. Sorted by points, wins, NRR, name."""
    limit_balls = int(overs_limit) * 6
    rows: dict[int, dict[str, Any]] = {}
    for t in teams:
        rows[int(t["team_id"])] = {
            "team_id": int(t["team_id"]), "name": t.get("name"), "played": 0, "won": 0, "lost": 0,
            "nr": 0, "abandoned": 0, "points": 0, "runs_for": 0, "balls_for": 0, "runs_against": 0,
            "balls_against": 0, "nrr": 0.0, "form": [],
        }
    ordered = sorted((f for f in fixtures if f.get("stage") == "league" and f.get("status") == "done"),
                     key=lambda f: (int(f.get("day_no") or 0), int(f.get("seq") or 0)))
    for f in ordered:
        home, away = rows.get(int(f["home_team_id"])), rows.get(int(f["away_team_id"]))
        if home is None or away is None:
            continue
        kind = f.get("result_type") or "win"
        winner = f.get("winner_team_id")
        for row in (home, away):
            row["played"] += 1
        if kind in {"win", "walkover"} and winner:
            win, lose = (home, away) if int(winner) == home["team_id"] else (away, home)
            win["won"] += 1
            lose["lost"] += 1
            win["points"] += POINTS_WALKOVER if kind == "walkover" else POINTS_WIN
            win["form"].append("W")
            lose["form"].append("L")
            if kind == "win" and int(f.get("home_balls") or 0) and int(f.get("away_balls") or 0):
                hb = _overs_balls(f.get("home_balls") or 0, f.get("home_wkts") or 0, limit_balls)
                ab = _overs_balls(f.get("away_balls") or 0, f.get("away_wkts") or 0, limit_balls)
                home["runs_for"] += int(f.get("home_runs") or 0); home["balls_for"] += hb
                home["runs_against"] += int(f.get("away_runs") or 0); home["balls_against"] += ab
                away["runs_for"] += int(f.get("away_runs") or 0); away["balls_for"] += ab
                away["runs_against"] += int(f.get("home_runs") or 0); away["balls_against"] += hb
        elif kind == "abandoned":
            for row in (home, away):
                row["abandoned"] += 1
                row["points"] += POINTS_ABANDONED
                row["form"].append("A")
        else:  # no result / tie
            for row in (home, away):
                row["nr"] += 1
                row["points"] += POINTS_NO_RESULT
                row["form"].append("N")
    for row in rows.values():
        for_rate = (row["runs_for"] * 6.0 / row["balls_for"]) if row["balls_for"] else 0.0
        against_rate = (row["runs_against"] * 6.0 / row["balls_against"]) if row["balls_against"] else 0.0
        row["nrr"] = round(for_rate - against_rate, 3)
        row["form"] = row["form"][-5:]
    table = sorted(rows.values(), key=lambda r: (-r["points"], -r["won"], -r["nrr"], str(r["name"] or "").casefold()))
    for position, row in enumerate(table, start=1):
        row["pos"] = position
    return table


def table_from_rows(team_rows: Sequence[Mapping[str, Any]], fixture_rows: Sequence[Mapping[str, Any]], overs_limit: int = 20) -> list[dict[str, Any]]:
    """Database rows (team_no / home_team_no ...) -> :func:`compute_table`. Rows keep ``team_no`` and carry the team columns."""
    teams = [{"team_id": int(t["team_no"]), "name": t.get("name")} for t in team_rows]
    fixtures = []
    for f in fixture_rows:
        fixtures.append({**f, "home_team_id": f.get("home_team_no"), "away_team_id": f.get("away_team_no"),
                         "winner_team_id": f.get("winner_team_no")})
    table = compute_table(teams, fixtures, overs_limit)
    by_no = {int(t["team_no"]): t for t in team_rows}
    for row in table:
        row["team_no"] = row["team_id"]
        team = by_no.get(row["team_id"]) or {}
        row["code"] = team.get("code")
        row["base_code"] = team.get("base_code")
        row["short"] = team.get("short") or team.get("base_code")
        row["owner_id"] = team.get("owner_id")
        row["owner_name"] = team.get("owner_name")
    return table


def fmt_nrr(value: float) -> str:
    return f"{value:+.3f}"


# ---------------------------------------------------------------------------
# no-show resolution
# ---------------------------------------------------------------------------
def resolve_no_show(present_home: bool, present_away: bool, stage: str = "league", home_seed: int = 1, away_seed: int = 2) -> dict[str, Any]:
    """What happens to a fixture that was not played by the day's deadline?

    * both present   -> still to be played ("play")
    * exactly one    -> walkover for the one who showed up
    * nobody         -> abandoned (league: 0 points each). A knockout cannot end
                        without a team going through, so the better seed advances.
    """
    if present_home and present_away:
        return {"action": "play"}
    if present_home or present_away:
        return {"action": "walkover", "winner": "home" if present_home else "away"}
    if stage == "league":
        return {"action": "abandoned", "winner": None}
    return {"action": "abandoned", "winner": "home" if home_seed <= away_seed else "away"}


# ---------------------------------------------------------------------------
# match statistics
# ---------------------------------------------------------------------------
def _num(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def mvp_points(line: Mapping[str, Any]) -> float:
    runs, balls = _num(line.get("runs")), _num(line.get("balls"))
    wickets, bowl_balls, bowl_runs = _num(line.get("wickets")), _num(line.get("bowl_balls")), _num(line.get("bowl_runs"))
    points = runs + _num(line.get("fours")) + 2 * _num(line.get("sixes"))
    if balls >= 10:
        points += max(-5.0, min(10.0, (runs * 100.0 / balls - 100.0) / 10.0))
    if line.get("batted") and line.get("dismissed") and runs == 0:
        points -= 5
    if runs >= 100:
        points += 25
    elif runs >= 50:
        points += 10
    points += 25 * wickets
    if wickets >= 5:
        points += 25
    elif wickets >= 3:
        points += 10
    if bowl_balls >= 6:
        economy = bowl_runs * 6.0 / bowl_balls
        points += max(-10.0, min(15.0, (8.0 - economy) * (bowl_balls / 6.0) * 1.5))
    if line.get("potm"):
        points += 15
    return round(points, 2)


def extract_match(payload: Mapping[str, Any], team_by_user: Mapping[int, int]) -> dict[str, Any]:
    """Boil a match-analysis payload down to what the tournament stores.

    ``team_by_user`` maps the two player user-ids to their tournament team ids.
    Returns {"innings": [...], "lines": [...], "totals": {team_id: {...}}}.
    """
    teams = {int(v) for v in team_by_user.values()}
    milestones = list(payload.get("milestones") or [])
    potm_name = str(((payload.get("potm") or {}).get("name")) or "").strip()
    lines: dict[tuple[int, str], dict[str, Any]] = {}
    innings_out: list[dict[str, Any]] = []
    totals = {t: {"runs": 0, "wkts": 0, "balls": 0, "innings": 0} for t in teams}

    def line_for(team_id: int, name: str) -> dict[str, Any]:
        key = (team_id, player_key(name))
        if key not in lines:
            lines[key] = {
                "team_id": team_id, "player_name": str(name), "player_key": player_key(name), "batted": False,
                "runs": 0, "balls": 0, "fours": 0, "sixes": 0, "dismissed": False,
                "fifty_balls": None, "hundred_balls": None, "wickets": 0, "bowl_balls": 0, "bowl_runs": 0, "potm": False,
            }
        return lines[key]

    for inn in payload.get("innings") or []:
        bat_team = team_by_user.get(_num(inn.get("batting_team_id")))
        if bat_team is None:
            continue
        bowl_team = next((t for t in teams if t != bat_team), None)
        number = _num(inn.get("innings_number")) or (len(innings_out) + 1)
        innings_out.append({
            "innings_no": number, "team_id": bat_team, "runs": _num(inn.get("runs")),
            "wkts": _num(inn.get("wickets")), "balls": _num(inn.get("legal_balls")),
        })
        totals[bat_team]["runs"] += _num(inn.get("runs"))
        totals[bat_team]["wkts"] += _num(inn.get("wickets"))
        totals[bat_team]["balls"] += _num(inn.get("legal_balls"))
        totals[bat_team]["innings"] += 1
        for b in inn.get("batters") or []:
            balls, dismissed = _num(b.get("balls")), bool(b.get("dismissed"))
            if balls == 0 and not dismissed:
                continue                                       # did not bat
            ln = line_for(bat_team, b.get("name") or "Player")
            ln["batted"] = True
            ln["runs"] += _num(b.get("runs")); ln["balls"] += balls
            ln["fours"] += _num(b.get("fours")); ln["sixes"] += _num(b.get("sixes"))
            ln["dismissed"] = ln["dismissed"] or dismissed
            for mark, field, kind in ((50, "fifty_balls", "BAT_50"), (100, "hundred_balls", "BAT_100")):
                if _num(b.get("runs")) < mark:
                    continue
                hit = next((m for m in milestones if str(m.get("type")) == kind and _num(m.get("innings")) == number
                            and (player_key(m.get("player_name")) == player_key(b.get("name"))
                                 or (m.get("player_id") and _num(m.get("player_id")) == _num(b.get("player_id"))))), None)
                at = _num(hit.get("balls")) if hit else balls
                ln[field] = at if ln[field] is None else min(ln[field], at)
        if bowl_team is not None:
            for w in inn.get("bowlers") or []:
                if _num(w.get("balls")) == 0 and _num(w.get("wickets")) == 0:
                    continue
                ln = line_for(bowl_team, w.get("name") or "Bowler")
                ln["wickets"] += _num(w.get("wickets")); ln["bowl_balls"] += _num(w.get("balls")); ln["bowl_runs"] += _num(w.get("runs"))

    if potm_name:
        for ln in lines.values():
            if ln["player_key"] == player_key(potm_name):
                ln["potm"] = True
                break
    result = list(lines.values())
    for ln in result:
        ln["mvp"] = mvp_points(ln)
    return {"innings": innings_out, "lines": result, "totals": totals}


def fixture_scores(extracted: Mapping[str, Any], home_team: int, away_team: int) -> dict[str, int]:
    t = extracted.get("totals") or {}
    h, a = t.get(home_team) or {}, t.get(away_team) or {}
    return {
        "home_runs": _num(h.get("runs")), "home_wkts": _num(h.get("wkts")), "home_balls": _num(h.get("balls")),
        "away_runs": _num(a.get("runs")), "away_wkts": _num(a.get("wkts")), "away_balls": _num(a.get("balls")),
    }


def fmt_overs(balls: int) -> str:
    return f"{int(balls) // 6}.{int(balls) % 6}"


def score_text(runs: int, wkts: int, balls: int) -> str:
    return f"{int(runs)}/{int(wkts)} ({fmt_overs(balls)})"


def match_margin(innings: Sequence[Mapping[str, Any]], winner_team: int | None, super_over: bool = False) -> str:
    """'by 14 runs' / 'by 6 wickets' / 'in the Super Over' (empty when it cannot be worked out)."""
    if super_over:
        return "in the Super Over"
    ordered = sorted(innings, key=lambda i: int(i.get("innings_no") or 0))
    if not winner_team or len(ordered) < 2:
        return ""
    first, second = ordered[0], ordered[1]
    if int(winner_team) == int(second.get("team_id") or 0):
        left = max(1, 10 - int(second.get("wkts") or 0))
        return f"by {left} wicket{'s' if left != 1 else ''}"
    diff = int(first.get("runs") or 0) - int(second.get("runs") or 0)
    return f"by {diff} run{'s' if diff != 1 else ''}" if diff > 0 else ""


# ---------------------------------------------------------------------------
# match-day clock
# ---------------------------------------------------------------------------
def tzinfo(tz_min: int = TZ_MIN_IST) -> timezone:
    return timezone(timedelta(minutes=int(tz_min)))


def window_bounds(day: date, start_min: int, end_min: int, tz_min: int = TZ_MIN_IST) -> tuple[datetime, datetime]:
    """Open/close of the match window of ``day`` as aware UTC datetimes."""
    midnight = datetime.combine(day, time(0, 0), tzinfo(tz_min))
    start = midnight + timedelta(minutes=int(start_min))
    end = midnight + timedelta(minutes=int(end_min))
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def first_play_date(now_utc: datetime, start_min: int, end_min: int, tz_min: int = TZ_MIN_IST, guard_min: int = LATE_START_GUARD_MIN) -> date:
    """Day 1: today if there is still a decent chunk of the window left, else tomorrow."""
    local = now_utc.astimezone(tzinfo(tz_min))
    today = local.date()
    _start, end = window_bounds(today, start_min, end_min, tz_min)
    return today if now_utc < end - timedelta(minutes=guard_min) else today + timedelta(days=1)


def fmt_clock(minutes: int) -> str:
    h, m = divmod(int(minutes) % 1440, 60)
    suffix = "AM" if h < 12 else "PM"
    return f"{(h % 12) or 12}:{m:02d} {suffix}" if m else f"{(h % 12) or 12} {suffix}"


def fmt_window(start_min: int, end_min: int) -> str:
    return f"{fmt_clock(start_min)} – {fmt_clock(end_min)} IST"


def fmt_day(day: date) -> str:
    return day.strftime("%a, %d %b").replace(" 0", " ")


def clean_name(text: Any, max_len: int = 36) -> str | None:
    value = re.sub(r"[\x00-\x1f<>&]", " ", str(text or ""))
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) < 3:
        return None
    return value[:max_len].strip()


# ---------------------------------------------------------------------------
# manual schedule (.txt) - parse / render
# ---------------------------------------------------------------------------
SCHEDULE_HELP = (
    "# Tournament schedule - one match per line\n"
    "# DAY lines start a match day. Date (YYYY-MM-DD) is optional; without it days follow one after another.\n"
    "# A team can be written as its short name (CSK), its number (#3) or its owner (@username).\n"
    "# Add [Q1] [EL] [Q2] [F] after a match for a playoff stage. Lines starting with # are ignored.\n"
    "\n"
    "DAY 1 | 2026-10-25\n"
    "CSK vs MI\n"
    "@rahul vs @aman\n"
    "\n"
    "DAY 2\n"
    "#1 vs #4\n"
)

_DAY_RE = re.compile(r"^\s*day\s*(\d+)?\s*(?:[|,:\-]\s*(\d{4}-\d{2}-\d{2}))?\s*$", re.I)
_VS_RE = re.compile(r"^\s*(.+?)\s+(?:vs\.?|v|x|versus)\s+(.+?)\s*(?:\[\s*(league|q1|el|q2|f|final)\s*\])?\s*$", re.I)


def _resolve_team(token: str, teams: Sequence[Mapping[str, Any]]) -> int | None:
    tok = token.strip().strip("<>").strip()
    if not tok:
        return None
    if tok.startswith("#") and tok[1:].isdigit():
        n = int(tok[1:])
        return n if any(int(t["team_no"]) == n for t in teams) else None
    if tok.startswith("@"):
        u = tok[1:].lower()
        for t in teams:
            if str(t.get("owner_username") or "").lower() == u:
                return int(t["team_no"])
        return None
    key = re.sub(r"\s+", " ", tok).casefold()
    for t in teams:
        names = {str(t.get("short") or "").casefold(), str(t.get("base_code") or "").casefold(), str(t.get("name") or "").casefold()}
        if key in names:
            return int(t["team_no"])
    return None


def parse_schedule_text(text: str, teams: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse a manual schedule. Returns (matches, errors); matches = [{day_no, seq, home, away, stage, date}].

    Rules: a team plays at most once per day, never against itself, a pair meets only where the file says so.
    Any error means nothing is applied (the caller shows the first few errors)."""
    errors: list[str] = []
    out: list[dict[str, Any]] = []
    day = 0
    day_date: str | None = None
    seen_days: set[int] = set()
    busy: dict[int, set[int]] = {}
    seq = 0
    for lineno, raw in enumerate(str(text or "").replace("﻿", "").splitlines(), start=1):
        line = "" if re.match(r"^\s*#(?!\d)", raw) else re.split(r"\s#(?!\d)", raw, maxsplit=1)[0].strip()
        if not line:
            continue
        m = _DAY_RE.match(line)
        if m:
            day = int(m.group(1)) if m.group(1) else day + 1
            day_date = m.group(2)
            if day in seen_days:
                errors.append(f"line {lineno}: DAY {day} appears twice")
            seen_days.add(day)
            if day_date:
                try:
                    date.fromisoformat(day_date)
                except ValueError:
                    errors.append(f"line {lineno}: bad date {day_date}")
                    day_date = None
            seq = 0
            continue
        m = _VS_RE.match(line)
        if not m:
            errors.append(f"line {lineno}: cannot read '{line[:40]}' (use: TEAM vs TEAM)")
            continue
        if day == 0:
            errors.append(f"line {lineno}: a match comes before the first DAY line")
            continue
        a, b = _resolve_team(m.group(1), teams), _resolve_team(m.group(2), teams)
        if a is None or b is None:
            bad = m.group(1) if a is None else m.group(2)
            errors.append(f"line {lineno}: unknown team '{bad.strip()[:20]}'")
            continue
        if a == b:
            errors.append(f"line {lineno}: a team cannot play itself")
            continue
        if a in busy.setdefault(day, set()) or b in busy[day]:
            errors.append(f"line {lineno}: a team already plays on day {day}")
            continue
        busy[day].update((a, b))
        stage = (m.group(3) or "league").lower()
        stage = {"final": "F", "q1": "Q1", "el": "EL", "q2": "Q2", "f": "F"}.get(stage, "league")
        seq += 1
        out.append({"day_no": day, "seq": seq, "home": a, "away": b, "stage": stage, "date": day_date})
    if not out and not errors:
        errors.append("the file has no matches")
    return out, errors


def render_schedule_text(fixtures: Sequence[Mapping[str, Any]], teams: Sequence[Mapping[str, Any]],
                         dates: Mapping[int, Any] | None = None) -> str:
    """Current schedule in the same format parse_schedule_text reads (so it can be edited and sent back)."""
    short = {int(t["team_no"]): str(t.get("short") or t.get("base_code") or f"#{t['team_no']}") for t in teams}
    lines = [SCHEDULE_HELP.split("\n\n")[0], ""]
    current = None
    for f in sorted(fixtures, key=lambda x: (int(x.get("day_no") or 0), int(x.get("seq") or 0))):
        d = int(f["day_no"])
        if d != current:
            current = d
            when = (dates or {}).get(d)
            lines += ["", f"DAY {d}" + (f" | {when}" if when else "")]
        stage = str(f.get("stage") or "league")
        tag = "" if stage == "league" else f" [{stage}]"
        lines.append(f"#{int(f['home_team_no'])} vs #{int(f['away_team_no'])}{tag}   # {short.get(int(f['home_team_no']))} vs {short.get(int(f['away_team_no']))}")
    return "\n".join(lines).strip() + "\n"
