
from __future__ import annotations

import json
import re
from collections import OrderedDict
from typing import Any, Iterable

from database.query import execute, fetch, fetchrow

PROBABILITY_KEYS = [
    "DOT",
    "SINGLE",
    "DOUBLE",
    "TRIPLE",
    "FOUR",
    "FIVE",
    "SIX",
    "WIDE",
    "NO_BALL",
    "LEG_BYE",
    "BYE",
    "OUT",
    "RUN_OUT",
]

PROBABILITY_KEY_ALIASES = {
    "WICKET": "OUT",
    "RUNOUT": "RUN_OUT",
    "RUN OUT": "RUN_OUT",
    "NO BALL": "NO_BALL",
    "LEG BYE": "LEG_BYE",
    "WIDE BALL": "WIDE",
}

# The selector grammar is intentionally strict enough to be safe, but flexible
# enough to survive human typing and Telegram formatting.
_SELECTOR_BLOCK_RE = re.compile(r"\[([^\[\]]+)\]")
_ROW_RE = re.compile(r"\[\s*([A-Za-z0-9 _\-]+?)\s*\]\s*=\s*\[\s*([^\[\]]+?)\s*\]")
_PERCENT_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*%?$")
_RANGE_RE = re.compile(r"^(?P<label>[A-Z_ ]+)\s*(?P<start>\d+)\s*[-–]\s*(?P<end>\d+)$", re.I)

# Common lofted / aerial shot families that should not be used with a
# grounded approach.
LOFT_KEYWORDS = (
    "LOFT",
    "LOFTED",
    "LOFTED DRIVE",
    "LOFTED COVER DRIVE",
    "LOFTED STRAIGHT DRIVE",
    "LOFTED SQUARE DRIVE",
    "INSIDE OUT",
    "INSIDE-OUT",
    "DOWN THE GROUND",
    "SLOG",
    "SLOG SWEEP",
    "SCOOP",
    "SCOOP SHOT",
    "RAMP",
    "RAMP SHOT",
    "UPPER CUT",
    "PULL (LOFTED)",
    "HOOK (LOFTED)",
    "SWITCH HIT",
    "PADDLE",
    "PADDLE SWEEP",
    "HELICOPTER",
    "CHARGE + LOFT",
    "CHARGE + HIT",
    "CHARGE + SWING",
)

# ---------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------

def _norm_token(value: str | None) -> str:
    text = str(value or "").strip().upper()
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def _norm_spaced(value: str | None) -> str:
    return " ".join(str(value or "").strip().upper().split())


def _as_float(value: str) -> float:
    match = _PERCENT_RE.match(value.strip())
    if not match:
        raise ValueError(f"invalid percentage value: {value!r}")
    return float(match.group(1))


def _parse_range_block(block: str, label: str) -> tuple[int, int]:
    cleaned = block.strip()
    m = _RANGE_RE.match(cleaned)
    if m and _norm_token(m.group("label")) == _norm_token(label):
        return int(m.group("start")), int(m.group("end"))
    if label in {"OVER"} and re.match(r"^\d+\s*[-–]\s*\d+$", cleaned):
        start, end = re.split(r"\s*[-–]\s*", cleaned)
        return int(start), int(end)
    raise ValueError(f"expected a {label} range like '{label} 60-70', got {block!r}")


def _is_loft_shot(shot: str) -> bool:
    text = _norm_spaced(shot)
    compact = _norm_token(shot)
    for keyword in LOFT_KEYWORDS:
        if _norm_spaced(keyword) in text or _norm_token(keyword) in compact:
            return True
    return False


# ---------------------------------------------------------------------
# Selector parsing
# ---------------------------------------------------------------------

def _parse_selector_command(command_text: str) -> dict[str, Any]:
    blocks = [b.strip() for b in _SELECTOR_BLOCK_RE.findall(command_text or "")]
    if len(blocks) < 8:
        raise ValueError(
            "expected 8 selector blocks in the command, for example: "
            "[BAT 60-70,BOWL 70-80][OUTSWING][WST,FL][FRONT,GROUND][STRAIGHT_DRIVE][DUSTY][1-6][BALLS_FACED 5-15]"
        )

    blocks = blocks[:8]

    level_block = blocks[0]
    delivery_block = blocks[1]
    line_length_block = blocks[2]
    movement_block = blocks[3]
    shot_block = blocks[4]
    pitch_block = blocks[5]
    over_block = blocks[6]
    balls_block = blocks[7]

    levels = [part.strip() for part in level_block.split(",") if part.strip()]
    bat_min = bat_max = bowl_min = bowl_max = None
    for part in levels:
        m = re.match(r"^(BAT|BOWL)\s*(\d+)\s*[-–]\s*(\d+)$", part, re.I)
        if not m:
            raise ValueError(f"invalid level selector {part!r}; expected 'BAT 60-70' or 'BOWL 70-80'")
        label = m.group(1).upper()
        start = int(m.group(2))
        end = int(m.group(3))
        if start > end:
            raise ValueError(f"invalid range {part!r}; start must be <= end")
        if label == "BAT":
            bat_min, bat_max = start, end
        else:
            bowl_min, bowl_max = start, end

    if bat_min is None or bat_max is None or bowl_min is None or bowl_max is None:
        raise ValueError("both BAT and BOWL ranges are required in the first selector block")

    line_length = [p.strip() for p in line_length_block.split(",") if p.strip()]
    if len(line_length) != 2:
        raise ValueError(f"invalid line/length block {line_length_block!r}; expected exactly two values like [WST,FL]")

    movement_approach = [p.strip() for p in movement_block.split(",") if p.strip()]
    if len(movement_approach) != 2:
        raise ValueError(f"invalid movement/approach block {movement_block!r}; expected exactly two values like [FRONT,GROUND]")

    delivery = _norm_token(delivery_block)
    line = _norm_token(line_length[0])
    length = _norm_token(line_length[1])
    movement = _norm_token(movement_approach[0])
    approach = _norm_token(movement_approach[1])
    shot = _norm_token(shot_block)
    pitch = _norm_token(pitch_block)

    over_min, over_max = _parse_range_block(over_block, "OVER")

    balls_match = re.match(r"^BALLS_FACED\s*(\d+)\s*[-–]\s*(\d+)$", _norm_spaced(balls_block), re.I)
    if not balls_match:
        balls_match = re.match(r"^BALLS_FACED\s*(\d+)\s*[-–]\s*(\d+)$", balls_block.strip(), re.I)
    if not balls_match:
        raise ValueError(f"invalid balls-faced selector {balls_block!r}; expected 'BALLS_FACED 5-15'")
    balls_min = int(balls_match.group(1))
    balls_max = int(balls_match.group(2))
    if balls_min > balls_max:
        raise ValueError("balls-faced range start must be <= end")

    # Guardrails requested by the user:
    # - Grounded approach rejects lofted shots
    # - Lofted approach rejects grounded shots
    # - Advance is intentionally permissive so any shot family can be used.
    if movement != "ADVANCE":
        if approach == "GROUND" and _is_loft_shot(shot):
            raise ValueError(f"shot {shot!r} is a lofted shot, so it cannot be used with approach GROUND")
        if approach in {"LOFT", "LOFTED"} and not _is_loft_shot(shot):
            raise ValueError(f"shot {shot!r} is a grounded shot, so it cannot be used with approach LOFT")

    return {
        "bat_min": bat_min,
        "bat_max": bat_max,
        "bowl_min": bowl_min,
        "bowl_max": bowl_max,
        "delivery": delivery,
        "line": line,
        "length": length,
        "movement": movement,
        "approach": approach,
        "shot": shot,
        "pitch": pitch,
        "over_min": over_min,
        "over_max": over_max,
        "balls_faced_min": balls_min,
        "balls_faced_max": balls_max,
    }


def _profile_key_from_selectors(selectors: dict[str, Any]) -> str:
    return "||".join(
        [
            f"BAT {selectors['bat_min']}-{selectors['bat_max']}",
            f"BOWL {selectors['bowl_min']}-{selectors['bowl_max']}",
            selectors["delivery"],
            selectors["line"],
            selectors["length"],
            selectors["movement"],
            selectors["approach"],
            selectors["shot"],
            selectors["pitch"],
            f"{selectors['over_min']}-{selectors['over_max']}",
            f"BALLS_FACED {selectors['balls_faced_min']}-{selectors['balls_faced_max']}",
        ]
    )


def _parse_probability_rows(rows_text: str) -> tuple[dict[str, list[float]], list[str]]:
    probabilities: dict[str, list[float]] = OrderedDict()
    errors: list[str] = []
    matched_rows = 0

    for match in _ROW_RE.finditer(rows_text or ""):
        matched_rows += 1
        raw_key = _norm_token(match.group(1))
        key = PROBABILITY_KEY_ALIASES.get(raw_key, raw_key)
        if key not in PROBABILITY_KEYS:
            errors.append(f"unknown probability key {raw_key!r} in row {matched_rows}")
            continue

        raw_values = match.group(2)
        values: list[float] = []
        for raw_value in raw_values.split(","):
            item = raw_value.strip()
            if not item:
                continue
            try:
                values.append(_as_float(item))
            except ValueError as exc:
                errors.append(f"{key}: {exc}")
                values = []
                break

        if not values:
            errors.append(f"{key}: no valid percentage values found")
            continue

        probabilities[key] = values

    if matched_rows == 0:
        errors.append("No valid probability rows were found.")

    return probabilities, errors


async def parse_probability_upload(command_text: str, rows_text: str) -> tuple[dict[str, Any] | None, dict[str, list[float]] | None, list[str]]:
    try:
        selectors = _parse_selector_command(command_text)
    except ValueError as exc:
        return None, None, [str(exc)]

    probabilities, row_errors = _parse_probability_rows(rows_text)
    if not probabilities:
        return None, None, row_errors or ["No valid probability rows were found."]

    return selectors, probabilities, row_errors


# ---------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------

async def load_probability_profiles() -> list[dict[str, Any]]:
    rows = await fetch("SELECT * FROM probability_profiles ORDER BY profile_id ASC;")
    return [dict(row) for row in rows]


async def clear_probability_profiles() -> None:
    await execute("TRUNCATE TABLE probability_profiles RESTART IDENTITY CASCADE;")


def _merge_probability_lists(existing: list[float], incoming: list[float]) -> tuple[list[float], int]:
    merged = list(existing)
    seen = {float(v) for v in merged}
    duplicates = 0
    for value in incoming:
        value = float(value)
        if value in seen:
            duplicates += 1
            continue
        merged.append(value)
        seen.add(value)
    return merged, duplicates


async def upsert_probability_profile(
    selectors: dict[str, Any],
    probabilities: dict[str, list[float]],
    *,
    created_by: int | None = None,
    updated_by: int | None = None,
) -> dict[str, Any]:
    profile_key = _profile_key_from_selectors(selectors)
    existing = await fetchrow("SELECT * FROM probability_profiles WHERE profile_key = $1 LIMIT 1;", profile_key)

    added_values = 0
    duplicate_values = 0
    duplicate_counts = {key: 0 for key in PROBABILITY_KEYS}
    outcomes_payload = {key: list(values) for key, values in probabilities.items()}

    if existing is None:
        duplicate_counts_payload = {key: 0 for key in outcomes_payload.keys()}
        await execute(
            """
            INSERT INTO probability_profiles (
                profile_key, selectors, probabilities, outcomes, duplicate_counts,
                created_by, updated_by, created_at, updated_at
            ) VALUES ($1, $2::jsonb, $3::jsonb, $4::jsonb, $5::jsonb, $6, $7, NOW(), NOW());
            """,
            profile_key,
            json.dumps(selectors, default=str),
            json.dumps(outcomes_payload, default=str),
            json.dumps(outcomes_payload, default=str),
            json.dumps(duplicate_counts_payload, default=str),
            created_by,
            updated_by,
        )
        added_values = sum(len(v) for v in outcomes_payload.values())
        return {
            "profile_key": profile_key,
            "created": True,
            "updated": False,
            "added_values": added_values,
            "duplicate_values": 0,
            "duplicate_counts": duplicate_counts_payload,
            "selectors": selectors,
            "probabilities": outcomes_payload,
            "outcomes": outcomes_payload,
        }

    existing_probabilities = existing["probabilities"]
    if isinstance(existing_probabilities, str):
        existing_probabilities = json.loads(existing_probabilities)
    existing_outcomes = existing.get("outcomes") if hasattr(existing, "get") else None
    if isinstance(existing_outcomes, str):
        existing_outcomes = json.loads(existing_outcomes)
    existing_duplicate_counts = existing["duplicate_counts"]
    if isinstance(existing_duplicate_counts, str):
        existing_duplicate_counts = json.loads(existing_duplicate_counts)
    existing_selectors = existing["selectors"]
    if isinstance(existing_selectors, str):
        existing_selectors = json.loads(existing_selectors)

    merged_probabilities: dict[str, list[float]] = {k: list(v) for k, v in dict(existing_probabilities or {}).items()}
    merged_duplicate_counts: dict[str, int] = {k: int(v) for k, v in dict(existing_duplicate_counts or {}).items()}

    for key, incoming in probabilities.items():
        current = [float(v) for v in merged_probabilities.get(key, [])]
        merged, dups = _merge_probability_lists(current, incoming)
        merged_probabilities[key] = merged
        merged_duplicate_counts[key] = merged_duplicate_counts.get(key, 0) + dups
        duplicate_values += dups
        added_values += len(merged) - len(current)
        duplicate_counts[key] = dups

    await execute(
        """
        UPDATE probability_profiles
        SET selectors = $1::jsonb,
            probabilities = $2::jsonb,
            outcomes = $3::jsonb,
            duplicate_counts = $4::jsonb,
            updated_by = $5,
            updated_at = NOW()
        WHERE profile_key = $6;
        """,
        json.dumps(selectors, default=str),
        json.dumps(merged_probabilities, default=str),
        json.dumps(merged_probabilities, default=str),
        json.dumps(merged_duplicate_counts, default=str),
        updated_by,
        profile_key,
    )

    return {
        "profile_key": profile_key,
        "created": False,
        "updated": True,
        "added_values": added_values,
        "duplicate_values": duplicate_values,
        "duplicate_counts": merged_duplicate_counts,
        "selectors": selectors,
        "probabilities": merged_probabilities,
        "outcomes": merged_probabilities,
        "previous_selectors": existing_selectors,
        "previous_outcomes": existing_outcomes,
    }


def format_upload_report(summary: dict[str, Any], errors: list[str] | None = None) -> str:
    """Builds the Markdown report shown to the admin after an /upload_prob upload.
    `summary` is whatever upsert_probability_profile(...) returned."""
    selectors = summary.get("selectors") or {}
    profile_key = summary.get("profile_key", "")
    created = bool(summary.get("created"))
    added_values = int(summary.get("added_values", 0) or 0)
    duplicate_values = int(summary.get("duplicate_values", 0) or 0)
    duplicate_counts = summary.get("duplicate_counts") or {}
    probabilities = summary.get("probabilities") or summary.get("outcomes") or {}

    status_line = "🆕 *New profile created*" if created else "🔁 *Existing profile updated*"

    selector_lines = [
        f"*Levels:* BAT {selectors.get('bat_min')}-{selectors.get('bat_max')}, "
        f"BOWL {selectors.get('bowl_min')}-{selectors.get('bowl_max')}",
        f"*Delivery:* {selectors.get('delivery')}",
        f"*Line/Length:* {selectors.get('line')}, {selectors.get('length')}",
        f"*Movement/Approach:* {selectors.get('movement')}, {selectors.get('approach')}",
        f"*Shot:* {selectors.get('shot')}",
        f"*Pitch:* {selectors.get('pitch')}",
        f"*Over:* {selectors.get('over_min')}-{selectors.get('over_max')}",
        f"*Balls Faced:* {selectors.get('balls_faced_min')}-{selectors.get('balls_faced_max')}",
    ]

    filled_keys = sorted(k for k, v in probabilities.items() if v)
    keys_summary = ", ".join(filled_keys) if filled_keys else "none"

    lines = [
        "📋 *Probability Upload Report*",
        "",
        status_line,
        f"*Profile Key:* `{profile_key}`",
        "",
        *selector_lines,
        "",
        f"*✅ Values Added:* {added_values}",
        f"*♻️ Duplicate Values Skipped:* {duplicate_values}",
        f"*📊 Keys With Data:* {keys_summary}",
    ]

    dup_nonzero = {k: v for k, v in duplicate_counts.items() if v} if duplicate_counts else {}
    if dup_nonzero:
        dup_text = ", ".join(f"{k}={v}" for k, v in dup_nonzero.items())
        lines.append(f"*Duplicates by Key:* {dup_text}")

    if errors:
        lines.append("")
        lines.append("*⚠️ Problems:*")
        lines.extend(f"• {err}" for err in errors[:20])

    return "\n".join(lines)


async def get_probability_system_summary() -> dict[str, Any]:
    profiles = await load_probability_profiles()
    total_profiles = len(profiles)
    total_values = 0
    duplicate_values = 0
    complete_profiles = 0
    max_possible_per_profile = len(PROBABILITY_KEYS) * 10

    for profile in profiles:
        probabilities = profile.get("probabilities") or profile.get("outcomes") or {}
        duplicate_counts = profile.get("duplicate_counts") or {}
        if isinstance(probabilities, str):
            probabilities = json.loads(probabilities)
        if isinstance(duplicate_counts, str):
            duplicate_counts = json.loads(duplicate_counts)

        profile_values = 0
        for key in PROBABILITY_KEYS:
            values = probabilities.get(key, []) if isinstance(probabilities, dict) else []
            if isinstance(values, str):
                try:
                    values = json.loads(values)
                except Exception:
                    values = []
            profile_values += len(values or [])
        total_values += profile_values
        duplicate_values += sum(int(v or 0) for v in duplicate_counts.values()) if isinstance(duplicate_counts, dict) else 0
        if profile_values >= max_possible_per_profile:
            complete_profiles += 1

    average_fill_percent = round((total_values / max(total_profiles * max_possible_per_profile, 1)) * 100.0, 2) if total_profiles else 0.0
    duplicate_ratio = round((duplicate_values / max(total_values + duplicate_values, 1)) * 100.0, 2) if (total_values + duplicate_values) else 0.0
    efficiency = round(max(0.0, min(100.0, (average_fill_percent * 0.82) + ((100.0 - duplicate_ratio) * 0.18))), 2)

    return {
        "total_profiles": total_profiles,
        "complete_profiles": complete_profiles,
        "total_values": total_values,
        "duplicate_values": duplicate_values,
        "average_fill_percent": average_fill_percent,
        "duplicate_ratio_percent": duplicate_ratio,
        "efficiency_percent": efficiency,
        "max_possible_per_profile": max_possible_per_profile,
    }


# ---------------------------------------------------------------------
# Convenience helpers for the probability engine
# ---------------------------------------------------------------------

def normalize_profile_value(value: Any) -> str:
    return _norm_token(str(value or ""))


_DELIVERY_ALIASES = {
    "OUTSWING": {"OUTSWING", "OUTSWING_BALL"},
    "INSWING": {"INSWING", "INSWING_BALL"},
    "FAST": {"FAST", "FAST_BALL"},
    "FAST_BALL": {"FAST", "FAST_BALL"},
    "SLOWER": {"SLOWER", "SLOWER_BALL", "SLOW_BALL"},
    "SLOWER_BALL": {"SLOWER", "SLOWER_BALL", "SLOW_BALL"},
    "YORKER": {"YORKER", "YORKER_BALL"},
    "YORKER_BALL": {"YORKER", "YORKER_BALL"},
    "KNUCKLE": {"KNUCKLE", "KNUCKLE_BALL"},
    "KNUCKLE_BALL": {"KNUCKLE", "KNUCKLE_BALL"},
    "REVERSE_SWING": {"REVERSE_SWING", "REVERSE_SWING_BALL"},
    "BOUNCER": {"BOUNCER", "BOUNCER_BALL"},
    "BOUNCER_BALL": {"BOUNCER", "BOUNCER_BALL"},
}

_LINE_ALIASES = {
    "WST": {"WST", "WIDE_OF_OFF_STUMP", "WIDE_OF_OFF_STUMPS", "WIDE_OF_STUMP", "WIDE_OF_OFF"},
    "MST": {"MST", "MIDDLE_STUMP", "MIDDLE_OF_STUMP"},
    "LST": {"LST", "LEG_STUMP", "LEG_OF_STUMP"},
}

_LENGTH_ALIASES = {
    "FL": {"FL", "FULL_LENGTH"},
    "GL": {"GL", "GOOD_LENGTH"},
    "SL": {"SL", "SHORT_LENGTH", "SHORT_OF_LENGTH"},
    "YL": {"YL", "YORKER_LENGTH", "YORKER"},
}

_MOVEMENT_ALIASES = {
    "FRONT": {"FRONT", "FRONT_FOOT"},
    "BACK": {"BACK", "BACK_FOOT"},
    "ADVANCE": {"ADVANCE", "STEP_OUT", "STEPOUT"},
}

_APPROACH_ALIASES = {
    "GROUND": {"GROUND", "GROUNDED", "GROUND_SHOT"},
    "LOFT": {"LOFT", "LOFTED", "LOFTED_SHOT"},
    "ADVANCE": {"ADVANCE", "STEP_OUT", "STEPOUT"},
}


def _selector_group_match(selector: Any, actual: Any, family: str) -> bool:
    sel = normalize_profile_value(selector)
    act = normalize_profile_value(actual)
    if not sel or not act:
        return True
    if family == "delivery":
        sel_group = _DELIVERY_ALIASES.get(sel, {sel, sel.replace("_BALL", "")})
        act_group = _DELIVERY_ALIASES.get(act, {act, act.replace("_BALL", "")})
        return bool(sel_group & act_group) or sel in act or act in sel
    if family == "line":
        sel_group = _LINE_ALIASES.get(sel, {sel})
        act_group = _LINE_ALIASES.get(act, {act})
        return bool(sel_group & act_group) or sel in act or act in sel
    if family == "length":
        sel_group = _LENGTH_ALIASES.get(sel, {sel})
        act_group = _LENGTH_ALIASES.get(act, {act})
        return bool(sel_group & act_group) or sel in act or act in sel
    if family == "movement":
        sel_group = _MOVEMENT_ALIASES.get(sel, {sel})
        act_group = _MOVEMENT_ALIASES.get(act, {act})
        return bool(sel_group & act_group) or sel in act or act in sel
    if family == "approach":
        sel_group = _APPROACH_ALIASES.get(sel, {sel})
        act_group = _APPROACH_ALIASES.get(act, {act})
        return bool(sel_group & act_group) or sel in act or act in sel
    return sel == act or sel in act or act in sel


def profile_matches_selectors(profile_selectors: dict[str, Any], context: dict[str, Any]) -> bool:
    def _rng_contains(low_key: str, high_key: str, value: int) -> bool:
        low = int(profile_selectors.get(low_key, 0) or 0)
        high = int(profile_selectors.get(high_key, 0) or 0)
        return low <= value <= high

    if not _rng_contains("bat_min", "bat_max", int(context.get("batsman_level", 0) or 0)):
        return False
    if not _rng_contains("bowl_min", "bowl_max", int(context.get("bowler_level", 0) or 0)):
        return False
    if not _rng_contains("over_min", "over_max", int(context.get("over_number", 0) or 0)):
        return False
    if not _rng_contains("balls_faced_min", "balls_faced_max", int(context.get("balls_faced", 0) or 0)):
        return False

    for key in ("delivery", "line", "length", "movement", "approach", "shot", "pitch"):
        expected = normalize_profile_value(profile_selectors.get(key))
        actual = normalize_profile_value(context.get(key))
        if expected != actual:
            return False

    return True
