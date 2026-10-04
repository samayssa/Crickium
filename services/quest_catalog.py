"""Crickium Quest catalog loader.

The packaged CSV is the single source of truth for the 300 Quest definitions.
Each row carries the player-facing copy, verification metadata and rewards.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

_CATALOG_PATH = Path(__file__).with_name("quest_catalog.csv")

_DIFFICULTIES = ("Easy", "Medium", "Hard")
_EXPECTED_PERIODS = {"Daily": 100, "Weekly": 100, "Monthly": 100}


def _parse_qualifiers(value: str) -> dict[str, Any]:
    value = (value or "").strip()
    if not value or value.lower() == "none":
        return {}
    result: dict[str, Any] = {}
    for part in value.split(";"):
        if "=" not in part:
            continue
        key, raw = part.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        if not key:
            continue
        low = raw.lower()
        if low in {"true", "false"}:
            result[key] = low == "true"
            continue
        try:
            result[key] = float(raw) if "." in raw else int(raw)
        except ValueError:
            result[key] = raw
    return result


def _parse_int(value: str, field: str, task_id: str) -> int:
    try:
        return int(str(value).strip())
    except Exception as exc:
        raise ValueError(f"Invalid integer {field!r} for task {task_id}: {value!r}") from exc


REWARD_MAP = {
    "daily": {
        "Easy": {"sigils": 10, "coins": 100, "rubies": 0},
        "Medium": {"sigils": 20, "coins": 1500, "rubies": 0},
        "Hard": {"sigils": 30, "coins": 2000, "rubies": 0},
    },
    "weekly": {
        "Easy": {"sigils": 50, "coins": 2500, "rubies": 0},
        "Medium": {"sigils": 80, "coins": 3500, "rubies": 0},
        "Hard": {"sigils": 120, "coins": 5000, "rubies": 0},
    },
    "monthly": {
        "Easy": {"sigils": 100, "coins": 500, "rubies": 0},
        "Medium": {"sigils": 150, "coins": 0, "rubies": 100},
        "Hard": {"sigils": 200, "coins": 0, "rubies": 200},
    },
}


def _load_catalog() -> list[dict[str, Any]]:
    if not _CATALOG_PATH.exists():
        raise FileNotFoundError(f"Quest catalog not found: {_CATALOG_PATH}")

    rows: list[dict[str, Any]] = []
    with _CATALOG_PATH.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {
            "id", "period", "difficulty", "title", "description", "category",
            "event_type", "metric", "target", "qualifiers", "verification",
            "sigils", "coins", "rubies",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Quest catalog is missing columns: {sorted(missing)}")

        for raw in reader:
            task_id = str(raw.get("id") or "").strip()
            period = str(raw.get("period") or "").strip()
            difficulty = str(raw.get("difficulty") or "").strip()
            if not task_id or period not in _EXPECTED_PERIODS or difficulty not in _DIFFICULTIES:
                raise ValueError(f"Invalid Quest row: {raw!r}")
            task = {
                "id": task_id,
                "period": period,
                "difficulty": difficulty,
                "title": str(raw.get("title") or "").strip(),
                "description": str(raw.get("description") or "").strip(),
                "category": str(raw.get("category") or "").strip(),
                "event_type": str(raw.get("event_type") or "").strip(),
                "metric": str(raw.get("metric") or "").strip(),
                "target": _parse_int(raw.get("target", "0"), "target", task_id),
                "qualifiers": str(raw.get("qualifiers") or "none").strip(),
                "verification": str(raw.get("verification") or "").strip(),
                "sigils": _parse_int(raw.get("sigils", "0"), "sigils", task_id),
                "coins": _parse_int(raw.get("coins", "0"), "coins", task_id),
                "rubies": _parse_int(raw.get("rubies", "0"), "rubies", task_id),
            }
            task["qualifiers_obj"] = _parse_qualifiers(task["qualifiers"])
            rows.append(task)

    ids = [str(t["id"]) for t in rows]
    if len(rows) != 300 or len(set(ids)) != 300:
        raise ValueError(f"Quest catalog must contain 300 unique tasks, got {len(rows)} rows")
    for period, expected in _EXPECTED_PERIODS.items():
        actual = sum(1 for t in rows if t["period"] == period)
        if actual != expected:
            raise ValueError(f"Quest catalog {period} count must be {expected}, got {actual}")

    # Preserve the reward schedule required by the Quest economy. The CSV must
    # match this mapping exactly so the runtime and catalog cannot drift apart.
    for task in rows:
        period_key = str(task["period"]).lower()
        reward = REWARD_MAP[period_key][str(task["difficulty"])]
        if any(int(task[k]) != int(reward[k]) for k in ("sigils", "coins", "rubies")):
            raise ValueError(f"Reward mismatch in task {task['id']}: CSV and REWARD_MAP disagree")

    return rows


QUEST_TASKS = _load_catalog()
TASKS_BY_ID = {task["id"]: task for task in QUEST_TASKS}
TASKS_BY_PERIOD = {
    "daily": [t for t in QUEST_TASKS if t["period"] == "Daily"],
    "weekly": [t for t in QUEST_TASKS if t["period"] == "Weekly"],
    "monthly": [t for t in QUEST_TASKS if t["period"] == "Monthly"],
}

DIFFICULTY_COUNTS = {
    "daily": {"Easy": 2, "Medium": 2, "Hard": 1},
    "weekly": {"Easy": 2, "Medium": 2, "Hard": 2},
    "monthly": {"Easy": 3, "Medium": 4, "Hard": 3},
}

# REWARD_MAP is declared after the loader above, so validate again here with a
# compact deterministic pass once the mapping exists.
for _task in QUEST_TASKS:
    _period = str(_task["period"]).lower()
    _reward = REWARD_MAP[_period][str(_task["difficulty"])]
    assert int(_task["sigils"]) == int(_reward["sigils"]), (_task["id"], "sigils")
    assert int(_task["coins"]) == int(_reward["coins"]), (_task["id"], "coins")
    assert int(_task["rubies"]) == int(_reward["rubies"]), (_task["id"], "rubies")

del _task, _period, _reward
