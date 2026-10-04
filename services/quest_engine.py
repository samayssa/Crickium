"""Crickium Quest runtime: periods, per-user assignment, verification and rewards.

The supplied 300-task PDF is the task-definition source of truth. This module
keeps the runtime state in PostgreSQL so restart/redeploy never rerolls a
user's active tasks and never depends on an in-process timer for boundaries.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from database.query import execute, fetch, fetchrow, transaction
from services.quest_catalog import DIFFICULTY_COUNTS, TASKS_BY_PERIOD, TASKS_BY_ID
from utils.PremiumEmoji import (
    COINS_EMOJI_ID,
    DAILY_QUEST_EMOJI_ID,
    DONE_QUEST_EMOJI_ID,
    MONTHLY_QUEST_EMOJI_ID,
    RUBIES_EMOJI_ID,
    SIGIL_EMOJI_ID,
    WEEKLY_QUEST_EMOJI_ID,
    custom_emoji_html,
)

PERIODS = ("daily", "weekly", "monthly")
_EVENT_TYPES_FOR_QUEST_RUNNER = {
    "MATCH_COMPLETED", "PLAYER_PURCHASE", "COIN_SPENT", "RUBY_SPENT",
    "CLAIM_SUCCESS", "DAILY_REWARD_CLAIM", "CARD_VIEW", "STAT_VIEW",
    "PLAYING_XI_CONFIRMED", "TEAM_UPDATE", "LOADOUT_EQUIPPED", "LOADOUT_UNEQUIPPED",
    "PACK_PURCHASED", "PACK_OPENED", "AUCTION_JOINED", "AUCTION_BID_WON",
    "TOURNAMENT_MATCH_COMPLETED", "QUEST_EVENT",
}


def _server_now() -> datetime:
    """Current server-local time, including the server's UTC offset."""
    return datetime.now().astimezone()


def _local_midnight(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _next_sunday_midnight(start: datetime) -> datetime:
    """Return the first Sunday 00:00 strictly after start."""
    base = _local_midnight(start)
    days_until = (6 - base.weekday()) % 7
    if days_until == 0:
        days_until = 7
    return base + timedelta(days=days_until)


def _next_month_midnight(start: datetime) -> datetime:
    year = start.year + (1 if start.month == 12 else 0)
    month = 1 if start.month == 12 else start.month + 1
    return start.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _period_key(period_type: str, start: datetime) -> str:
    if period_type == "daily":
        return start.strftime("%Y-%m-%d")
    if period_type == "weekly":
        return start.strftime("%Y-%m-%d")
    return start.strftime("%Y-%m")


def _period_boundary_from_start(period_type: str, start: datetime) -> datetime:
    if period_type == "daily":
        return _local_midnight(start + timedelta(days=1))
    if period_type == "weekly":
        return start + timedelta(days=7)
    return _next_month_midnight(start)


async def _latest_period(period_type: str) -> dict[str, Any] | None:
    row = await fetchrow(
        "SELECT * FROM quest_periods WHERE period_type=$1 ORDER BY end_at DESC LIMIT 1;",
        period_type,
    )
    return dict(row) if row else None


async def ensure_current_period(period_type: str, now: datetime | None = None) -> dict[str, Any]:
    if period_type not in PERIODS:
        raise ValueError("invalid quest period")
    now = now or _server_now()
    latest = await _latest_period(period_type)

    if latest and now < latest["end_at"]:
        return latest

    if latest:
        # Advance from the persisted boundary, never from process uptime.
        start = latest["end_at"].astimezone(now.tzinfo) if getattr(latest["end_at"], "tzinfo", None) else latest["end_at"].replace(tzinfo=now.tzinfo)
        while start <= now:
            end = _period_boundary_from_start(period_type, start)
            key = _period_key(period_type, start)
            await execute(
                """
                INSERT INTO quest_periods(period_type, period_key, start_at, end_at)
                VALUES($1,$2,$3,$4)
                ON CONFLICT(period_type, period_key) DO NOTHING;
                """,
                period_type, key, start, end,
            )
            latest = await fetchrow(
                "SELECT * FROM quest_periods WHERE period_type=$1 AND period_key=$2;",
                period_type, key,
            )
            if latest and now < latest["end_at"]:
                return dict(latest)
            start = end
        return dict(latest)

    # First launch of this period type. Daily is aligned to today's midnight;
    # weekly/monthly begin at the actual launch time and then lock to the
    # persisted boundary forever after. This honors a Saturday launch -> next
    # Sunday 00:00 example while remaining restart-safe.
    if period_type == "daily":
        start = _local_midnight(now)
    elif period_type == "weekly":
        start = now
    else:
        start = now
    end = _next_sunday_midnight(now) if period_type == "weekly" else _period_boundary_from_start(period_type, start)
    key = _period_key(period_type, start)
    await execute(
        """
        INSERT INTO quest_periods(period_type, period_key, start_at, end_at)
        VALUES($1,$2,$3,$4)
        ON CONFLICT(period_type, period_key) DO NOTHING;
        """,
        period_type, key, start, end,
    )
    row = await fetchrow("SELECT * FROM quest_periods WHERE period_type=$1 AND period_key=$2;", period_type, key)
    if not row:
        raise RuntimeError("Could not create quest period")
    return dict(row)


async def ensure_current_periods() -> None:
    for period in PERIODS:
        try:
            await ensure_current_period(period)
        except Exception as exc:
            print(f"[quest] Failed to ensure {period} period: {exc!r}")


_QUEST_MAINTENANCE_TASK: Any = None


async def start_quest_maintenance() -> None:
    global _QUEST_MAINTENANCE_TASK
    if _QUEST_MAINTENANCE_TASK is not None and not _QUEST_MAINTENANCE_TASK.done():
        return

    import asyncio

    async def _runner() -> None:
        while True:
            try:
                await ensure_current_periods()
            except Exception as exc:
                print(f"[quest] refresh worker failed: {exc!r}")
            await asyncio.sleep(30)

    _QUEST_MAINTENANCE_TASK = asyncio.create_task(_runner(), name="crickium-quest-maintenance")


def _assignment_fingerprint(task_ids: list[str]) -> str:
    return hashlib.sha256("|".join(task_ids).encode("utf-8")).hexdigest()


async def get_or_create_assignment(user_id: int, period_type: str) -> tuple[dict[str, Any], dict[str, Any]]:
    period = await ensure_current_period(period_type)
    pkey = str(period["period_key"])
    existing = await fetchrow(
        "SELECT * FROM quest_user_assignments WHERE period_type=$1 AND period_key=$2 AND user_id=$3;",
        period_type, pkey, int(user_id),
    )
    if existing:
        return period, dict(existing)

    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in TASKS_BY_PERIOD[period_type]:
        pools[task["difficulty"]].append(task)

    counts = DIFFICULTY_COUNTS[period_type]
    sampler = secrets.SystemRandom()
    for attempt in range(40):
        task_ids: list[str] = []
        for difficulty, count in counts.items():
            task_ids.extend(t["id"] for t in sampler.sample(pools[difficulty], count))
        # Keep the visual order stable and intentional: Easy -> Medium -> Hard.
        order = {"Easy": 0, "Medium": 1, "Hard": 2}
        task_ids.sort(key=lambda tid: (order[TASKS_BY_ID[tid]["difficulty"]], tid))
        fingerprint = _assignment_fingerprint(task_ids)
        try:
            await execute(
                """
                INSERT INTO quest_user_assignments(
                    period_type, period_key, user_id, task_ids, fingerprint,
                    assigned_at, updated_at
                ) VALUES($1,$2,$3,$4::jsonb,$5,NOW(),NOW())
                ON CONFLICT(period_type, period_key, user_id) DO NOTHING;
                """,
                period_type, pkey, int(user_id), json.dumps(task_ids), fingerprint,
            )
        except Exception as exc:
            # The unique fingerprint constraint is deliberately allowed to reject
            # a duplicate set. Resample in that rare case.
            if "fingerprint" not in str(exc).lower() and "unique" not in str(exc).lower():
                raise
            continue
        assigned = await fetchrow(
            "SELECT * FROM quest_user_assignments WHERE period_type=$1 AND period_key=$2 AND user_id=$3;",
            period_type, pkey, int(user_id),
        )
        if assigned:
            return period, dict(assigned)
    raise RuntimeError("Could not allocate a unique Quest task set")


async def _assignment_tasks(user_id: int, period_type: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    period, assignment = await get_or_create_assignment(user_id, period_type)
    raw_ids = assignment.get("task_ids") or []
    if isinstance(raw_ids, str):
        raw_ids = json.loads(raw_ids)
    tasks = [TASKS_BY_ID[str(tid)] for tid in raw_ids if str(tid) in TASKS_BY_ID]
    return period, assignment, tasks


async def get_quest_view(user_id: int, period_type: str) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    period, _assignment, tasks = await _assignment_tasks(user_id, period_type)
    rows = await fetch(
        """
        SELECT task_id FROM quest_completions
        WHERE user_id=$1 AND period_type=$2 AND period_key=$3;
        """,
        int(user_id), period_type, str(period["period_key"]),
    )
    completed = {str(r["task_id"]) for r in rows}
    return period, tasks, completed


async def record_quest_event(user_id: int, event_type: str, *, value: int = 1, metadata: dict[str, Any] | None = None, occurred_at: datetime | None = None) -> None:
    """Persist a verified gameplay/economy event, then evaluate current quests."""
    user_id = int(user_id)
    event_type = str(event_type).upper().strip()
    metadata = dict(metadata or {})
    now = occurred_at or _server_now()
    metadata.setdefault("event_date", now.astimezone().strftime("%Y-%m-%d"))
    try:
        await execute(
            """
            INSERT INTO quest_events(event_id,user_id,event_type,value_int,metadata,occurred_at)
            VALUES($1,$2,$3,$4,$5::jsonb,$6);
            """,
            uuid.uuid4(), user_id, event_type, int(value or 0), json.dumps(metadata, default=str), now,
        )
        if event_type != "QUEST_EVENT" and event_type in _EVENT_TYPES_FOR_QUEST_RUNNER:
            await execute(
                """
                INSERT INTO quest_events(event_id,user_id,event_type,value_int,metadata,occurred_at)
                VALUES($1,$2,'QUEST_EVENT',1,$3::jsonb,$4);
                """,
                uuid.uuid4(), user_id, json.dumps({"source": event_type}), now,
            )
        await evaluate_and_complete_user(user_id)
    except Exception as exc:
        # Quest verification/rewarding is intentionally non-fatal to the core
        # bot command that generated the event.
        print(f"[quest] record/evaluate failed for user_id={user_id} event={event_type}: {exc!r}")


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return default
    return value


def _first_int(text: str) -> int | None:
    m = re.search(r"(?<!\d)([0-9][0-9,]*)(?!\d)", text or "")
    return int(m.group(1).replace(",", "")) if m else None


def _events_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    counts = defaultdict(int)
    sums = defaultdict(int)
    unique: dict[str, set[str]] = defaultdict(set)
    purchase_level_counts = defaultdict(int)
    loadout_keys: set[str] = set()
    dates_by_type: dict[str, set[str]] = defaultdict(set)
    for ev in events:
        et = str(ev.get("event_type") or "")
        val = int(ev.get("value_int") or 0)
        counts[et] += 1
        sums[et] += val
        meta = _json(ev.get("metadata"), {})
        if not isinstance(meta, dict):
            meta = {}
        for key in ("player_key", "match_key", "loadout_key", "week_key"):
            if meta.get(key) is not None:
                unique[f"{et}:{key}"].add(str(meta[key]))
        if meta.get("loadout_key"):
            loadout_keys.add(str(meta["loadout_key"]))
        if meta.get("event_date"):
            dates_by_type[et].add(str(meta["event_date"]))
        if et == "PLAYER_PURCHASE":
            ovr = int(meta.get("ovr") or 0)
            for level in (70, 75, 80, 85):
                if ovr >= level:
                    purchase_level_counts[level] += 1
    return {
        "counts": counts, "sums": sums, "unique": unique,
        "purchase_level_counts": purchase_level_counts,
        "loadout_keys": loadout_keys, "dates_by_type": dates_by_type,
    }



def record_live_ball(session: Any, outcome: Any) -> None:
    """Update the in-memory over ledger used by Quest phase/dot/boundary tasks."""
    try:
        score_balls = int(session.innings.score.legal_balls or 0)
        over_number = ((score_balls - 1) // 6) + 1 if score_balls > 0 else 1
        current = dict(getattr(session, "quest_current_over", {}) or {})
        if not current:
            current = {
                "innings_number": int(session.innings.innings_number or 1),
                "over_number": over_number,
                "phase": _phase_for_over(over_number),
                "batting_team_id": int(session.batting_team_id or 0),
                "bowling_team_id": int(session.bowling_team_id or 0),
                "runs": 0, "wickets": 0, "dots": 0, "fours": 0, "sixes": 0, "balls": 0,
                "completed": False,
            }
        current["runs"] = int(current.get("runs") or 0) + int(getattr(outcome, "runs", 0) or 0)
        current["wickets"] = int(current.get("wickets") or 0) + (1 if bool(getattr(outcome, "wicket", False)) else 0)
        name = str(getattr(outcome, "outcome", "") or "").lower()
        if name == "dot": current["dots"] = int(current.get("dots") or 0) + 1
        if name == "four": current["fours"] = int(current.get("fours") or 0) + 1
        if name == "six": current["sixes"] = int(current.get("sixes") or 0) + 1
        if bool(getattr(outcome, "legal", False)):
            current["balls"] = int(current.get("balls") or 0) + 1
        current["legal_balls_after"] = score_balls
        if bool(getattr(outcome, "legal", False)) and score_balls > 0 and score_balls % 6 == 0:
            current["completed"] = True
            history = list(getattr(session, "quest_over_history", []) or [])
            history.append(dict(current))
            session.quest_over_history = history
            session.quest_current_over = {}
        else:
            session.quest_current_over = current
    except Exception as exc:
        print(f"[quest] live-ball ledger update failed: {exc!r}")

def _phase_for_over(over_number: int) -> str:
    if over_number <= 6:
        return "powerplay"
    if over_number <= 15:
        return "middle"
    return "death"


def _match_metrics(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    m = {
        "runs_total": 0, "runs_chase": 0, "runs_defense": 0,
        "fours_total": 0, "sixes_total": 0, "chase_sixes": 0, "defense_sixes": 0,
        "wickets_total": 0, "wickets_chase": 0, "wickets_defense": 0,
        "dots_total": 0, "matches_total": len(summaries),
        "wins_total": 0, "chase_wins": 0, "defense_wins": 0,
        "chase_fifty_wins": 0, "chase_century_wins": 0,
        "late_chase_wins": 0, "comeback_chase_wins": 0, "comeback_defense_wins": 0,
        "phase_runs": defaultdict(int), "phase_wickets": defaultdict(int),
        "phase_dots": defaultdict(int), "phase_boundaries": defaultdict(int),
        "phase_over_runs": defaultdict(list),
        "player_runs": defaultdict(int), "chase_player_runs": defaultdict(int),
        "defense_player_runs": defaultdict(int),
        "single_match_player_runs_max": 0, "single_match_player_sixes_max": 0,
        "single_match_player_wickets_max": 0,
        "centuries": 0, "fifties": 0, "defense_fifties": 0, "defense_centuries": 0,
        "mode_matches": defaultdict(int), "mode_wins": defaultdict(int),
        "summary_rows": summaries,
    }
    for s in summaries:
        batting = _json(s.get("batting"), []) or []
        bowling = _json(s.get("bowling"), []) or []
        overs = _json(s.get("over_history"), []) or []
        runs = int(s.get("batting_runs") or 0)
        wickets = int(s.get("bowling_wickets") or 0)
        m["runs_total"] += runs
        m["wickets_total"] += wickets
        chasing = bool(s.get("chasing"))
        defending = bool(s.get("defending"))
        if chasing:
            m["runs_chase"] += runs
        if defending:
            m["runs_defense"] += runs
        if chasing:
            m["wickets_chase"] += wickets
        if defending:
            m["wickets_defense"] += wickets
        if bool(s.get("won")):
            m["wins_total"] += 1
            if chasing: m["chase_wins"] += 1
            if defending: m["defense_wins"] += 1
            if bool(s.get("late_chase")): m["late_chase_wins"] += 1
            if bool(s.get("comeback_chase")): m["comeback_chase_wins"] += 1
            if bool(s.get("comeback_defense")): m["comeback_defense_wins"] += 1
        if bool(s.get("chasing")) and bool(s.get("won")):
            if any(int(b.get("runs") or 0) >= 100 for b in batting):
                m["chase_century_wins"] += 1
            if any(int(b.get("runs") or 0) >= 50 for b in batting):
                m["chase_fifty_wins"] += 1
        mode = str(s.get("match_type") or s.get("engine") or "play").lower()
        m["mode_matches"][mode] += 1
        if bool(s.get("won")): m["mode_wins"][mode] += 1
        for b in batting:
            pid = str(b.get("player_id") or "")
            if not pid: continue
            br = int(b.get("runs") or 0)
            m["player_runs"][pid] += br
            if chasing: m["chase_player_runs"][pid] += br
            if defending: m["defense_player_runs"][pid] += br
            m["single_match_player_runs_max"] = max(m["single_match_player_runs_max"], br)
            m["single_match_player_sixes_max"] = max(m["single_match_player_sixes_max"], int(b.get("sixes") or 0))
            m["fours_total"] += int(b.get("fours") or 0)
            m["sixes_total"] += int(b.get("sixes") or 0)
            if chasing:
                m["chase_sixes"] += int(b.get("sixes") or 0)
            if defending:
                m["defense_sixes"] += int(b.get("sixes") or 0)
            if br >= 100: m["centuries"] += 1
            if 50 <= br < 100: m["fifties"] += 1
            if defending and 50 <= br < 100: m["defense_fifties"] += 1
            if defending and br >= 100: m["defense_centuries"] += 1
        for b in bowling:
            m["single_match_player_wickets_max"] = max(m["single_match_player_wickets_max"], int(b.get("wickets") or 0))
        for ov in overs:
            ovno = int(ov.get("over_number") or 0)
            phase = str(ov.get("phase") or _phase_for_over(ovno))
            relevant_bat = int(ov.get("batting_team_id") or 0) == int(s.get("user_id") or 0)
            relevant_bowl = int(ov.get("bowling_team_id") or 0) == int(s.get("user_id") or 0)
            if relevant_bat:
                m["phase_runs"][phase] += int(ov.get("runs") or 0)
                m["phase_dots"][phase] += int(ov.get("dots") or 0)
                m["phase_boundaries"][phase] += int(ov.get("fours") or 0) + int(ov.get("sixes") or 0)
            if relevant_bowl:
                m["phase_wickets"][phase] += int(ov.get("wickets") or 0)
                m["phase_dots"][f"bowl_{phase}"] += int(ov.get("dots") or 0)
                m["phase_over_runs"][phase].append(int(ov.get("runs") or 0))
            # Dots are bowling actions for the dot-ball quests. Boundaries are
            # already calculated from the batting snapshots above.
            if relevant_bowl:
                m["dots_total"] += int(ov.get("dots") or 0)
    return m


def _consecutive_days(dates: set[str], minimum: int) -> bool:
    if not dates:
        return False
    parsed = sorted({datetime.fromisoformat(d).date() for d in dates if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)})
    if not parsed:
        return False
    run = 1
    for prev, cur in zip(parsed, parsed[1:]):
        if (cur - prev).days == 1:
            run += 1
            if run >= minimum: return True
        else:
            run = 1
    return run >= minimum


def _daily_quest_day_set(completions: list[dict[str, Any]], *, daily_only: bool = True) -> dict[str, set[str]]:
    by_date: dict[str, set[str]] = defaultdict(set)
    for row in completions:
        if daily_only and str(row.get("period_type")) != "daily":
            continue
        when = row.get("completed_at")
        date = when.astimezone().strftime("%Y-%m-%d") if getattr(when, "tzinfo", None) else when.strftime("%Y-%m-%d")
        by_date[date].add(str(row.get("task_id")))
    return by_date


async def _load_current_metrics(user_id: int, period_type: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    period = await ensure_current_period(period_type)
    start = period["start_at"]
    pkey = str(period["period_key"])
    events = [dict(r) for r in await fetch(
        "SELECT * FROM quest_events WHERE user_id=$1 AND occurred_at >= $2 ORDER BY occurred_at;",
        int(user_id), start,
    )]
    summaries = [dict(r) for r in await fetch(
        "SELECT * FROM quest_match_summaries WHERE user_id=$1 AND played_at >= $2 ORDER BY played_at;",
        int(user_id), start,
    )]
    completions = [dict(r) for r in await fetch(
        "SELECT * FROM quest_completions WHERE user_id=$1 ORDER BY completed_at DESC LIMIT 5000;",
        int(user_id),
    )]
    event_metrics = _events_metrics(events)
    match_metrics = _match_metrics(summaries)
    match_metrics["user_id"] = int(user_id)
    return period, events, summaries, completions, {"events": event_metrics, "matches": match_metrics, "period_key": pkey}


def _task_satisfied(task: dict[str, Any], period_type: str, metrics: dict[str, Any], completions: list[dict[str, Any]]) -> bool:
    tid = str(task["id"])
    n = int(tid[1:])
    em = metrics["events"]
    mm = metrics["matches"]
    c = em["counts"]
    s = em["sums"]
    ul = em["unique"]
    levels = em["purchase_level_counts"]

    # Finish/completion tasks are evaluated from completion records.
    period_rows = [r for r in completions if r.get("period_type") == period_type and str(r.get("period_key")) == str(metrics["period_key"])]
    completed_ids = {str(r.get("task_id")) for r in period_rows}

    if period_type == "daily":
        if n <= 10:
            threshold = _first_int(task["description"]) or 0
            if n == 8: return mm["runs_total"] >= threshold and mm["matches_total"] >= 3
            return mm["runs_total"] >= threshold
        if 11 <= n <= 20:
            threshold = _first_int(task["description"]) or 0
            if n in (16,17,18): return max(mm["chase_player_runs"].values() or [0]) >= threshold
            if n == 19: return max([int(x) for x in mm["defense_player_runs"].values()] or [0]) >= threshold
            if n == 20: return max([int(x) for x in mm["defense_player_runs"].values()] or [0]) >= threshold
            if n in (11,12,13,14,15): return mm["single_match_player_runs_max"] >= threshold
        if 21 <= n <= 30:
            threshold = _first_int(task["description"]) or 0
            if n in (27,28): return max(mm["chase_player_runs"].values() or [0]) >= threshold
            if n in (29,30): return max(mm["defense_player_runs"].values() or [0]) >= threshold
            return max(mm["player_runs"].values() or [0]) >= threshold
        if 31 <= n <= 40:
            threshold = _first_int(task["description"]) or 0
            if 31 <= n <= 33: return mm["fours_total"] >= threshold
            if n in (37,38): return mm["single_match_player_sixes_max"] >= threshold
            if n == 39: return mm["chase_sixes"] >= threshold
            if n == 40: return mm["defense_sixes"] >= threshold
            return mm["sixes_total"] >= threshold
        if 41 <= n <= 50:
            threshold = _first_int(task["description"]) or 0
            if n in (46,47): return mm["single_match_player_wickets_max"] >= threshold
            if n == 48: return mm["phase_wickets"]["powerplay"] >= threshold
            if n == 49: return mm["phase_wickets"]["middle"] >= threshold
            if n == 50: return mm["phase_wickets"]["death"] >= threshold
            return mm["wickets_total"] >= threshold
        if 51 <= n <= 60:
            threshold = _first_int(task["description"]) or 0
            if n <= 53: return mm["dots_total"] >= threshold
            if n in (54,55,56):
                phase = {54:"powerplay",55:"middle",56:"death"}[n]
                limit = {54:6,55:6,56:8}[n]
                return any(v <= limit for v in mm["phase_over_runs"][phase])
            phase = {57:"powerplay",58:"middle",59:"death",60:"death"}[n]
            return mm["phase_runs"][phase] >= threshold
        if 61 <= n <= 74:
            if n in (61,62,63): return mm["chase_wins"] >= (1 if n==61 else 2 if n==62 else 3)
            if n == 64: return mm["late_chase_wins"] >= 1
            if n == 65: return mm["runs_chase"] >= 100
            if n == 66: return mm["chase_fifty_wins"] >= 1
            if n == 67: return mm["chase_century_wins"] >= 1
            if n in (68,69,70): return mm["defense_wins"] >= (1 if n==68 else 2 if n==69 else 3)
            if n == 71: return mm["wickets_defense"] >= 3 and mm["defense_wins"] >= 1
            if n == 72: return mm["wickets_defense"] >= 5
            if n == 73: return mm["comeback_chase_wins"] >= 1
            if n == 74: return mm["comeback_defense_wins"] >= 1
        if 75 <= n <= 80:
            if n == 75: return mm["mode_matches"]["play"] >= 2
            if n == 76: return mm["mode_matches"]["playipl"] >= 1
            if n == 77: return mm["mode_wins"]["playipl"] >= 1
            if n == 78: return mm["mode_matches"]["playint"] >= 1
            if n == 79: return mm["mode_wins"]["playint"] >= 1
            if n == 80: return mm["matches_total"] >= 5
        if 81 <= n <= 90:
            if n == 81: return levels[70] >= 1
            if n == 82: return levels[75] >= 1
            if n == 83: return levels[80] >= 1
            if n == 84: return levels[85] >= 1
            if n == 85: return s["COIN_SPENT"] >= 25000
            if n == 86: return s["COIN_SPENT"] >= 50000
            if n == 87: return s["RUBY_SPENT"] >= 5
            if n == 88: return c["PLAYER_PURCHASE"] >= 2
            if n == 89: return c["UPGRADE_APPLIED"] >= 2
            if n == 90: return c["LOADOUT_CHANGED"] >= 2
        if 91 <= n <= 100:
            if n == 91: return c["CLAIM_SUCCESS"] >= 5
            if n == 92: return _consecutive_days(em["dates_by_type"]["CLAIM_SUCCESS"], 5)
            if n == 93: return _consecutive_days(em["dates_by_type"]["DAILY_REWARD_CLAIM"], 3)
            if n == 94: return _daily_quest_streak(completions, 3)
            if n == 95: return len(ul["CARD_VIEW:player_key"]) >= 5
            if n == 96: return c["STAT_VIEW"] >= 5
            if n == 97: return c["AUCTION_JOINED"] >= 2
            if n == 98: return c["TOURNAMENT_MATCH_COMPLETED"] >= 2
            if n == 99: return c["PLAYING_XI_CONFIRMED"] >= 2
            if n == 100: return len(completed_ids) >= 5

    elif period_type == "weekly":
        if n <= 20:
            if n <= 10:
                threshold = _first_int(task["description"]) or 0
                if n == 9: return mm["runs_total"] >= threshold and mm["matches_total"] >= 5
                return mm["runs_total"] >= threshold
            threshold = _first_int(task["description"]) or 0
            if n in (16,17): return max(mm["chase_player_runs"].values() or [0]) >= threshold
            if n == 18: return mm["centuries"] >= 1
            if n == 19: return max(mm["defense_player_runs"].values() or [0]) >= threshold
            if n == 20: return max(mm["defense_player_runs"].values() or [0]) >= threshold
            return max(mm["player_runs"].values() or [0]) >= threshold
        if 21 <= n <= 30:
            threshold = _first_int(task["description"]) or 0
            if n in (21,22,23): return mm["fours_total"] >= threshold
            if n in (28,29): return mm["single_match_player_sixes_max"] >= threshold
            if n == 30: return mm["chase_sixes"] >= threshold
            return mm["sixes_total"] >= threshold
        if 31 <= n <= 40:
            threshold = _first_int(task["description"]) or 0
            if n in (36,37): return mm["single_match_player_wickets_max"] >= threshold
            if n in (38,39,40): return mm["phase_wickets"][{38:"powerplay",39:"middle",40:"death"}[n]] >= threshold
            return mm["wickets_total"] >= threshold
        if 41 <= n <= 50:
            threshold = _first_int(task["description"]) or 0
            if n <= 43: return mm["dots_total"] >= threshold
            if n in (44,45,46):
                phase={44:"powerplay",45:"middle",46:"death"}[n]
                limit={44:7,45:7,46:9}[n]
                runs=mm["phase_over_runs"][phase]
                # Require three separate qualifying overs.
                return sum(1 for v in runs if v <= limit) >= 3
            phase={47:"powerplay",48:"middle",49:"death",50:"death"}[n]
            return mm["phase_runs"][phase] >= threshold
        if 51 <= n <= 64:
            if n in (51,52,53): return mm["chase_wins"] >= {51:3,52:5,53:7}[n]
            if n == 54: return mm["late_chase_wins"] >= 2
            if n == 55: return mm["runs_chase"] >= 500
            if n == 56: return mm["chase_century_wins"] >= 1
            if n == 57: return mm["chase_fifty_wins"] >= 2
            if n in (58,59,60): return mm["defense_wins"] >= {58:3,59:5,60:7}[n]
            if n == 61: return mm["wickets_defense"] >= 10
            if n == 62: return mm["single_match_player_wickets_max"] >= 5 and any(x.get("defending") for x in mm["summary_rows"])
            if n == 63: return mm["comeback_chase_wins"] >= 2
            if n == 64: return mm["comeback_defense_wins"] >= 2
        if 65 <= n <= 70:
            return {
                65: mm["mode_matches"]["play"] >= 7,
                66: mm["mode_matches"]["playipl"] >= 3,
                67: mm["mode_wins"]["playipl"] >= 3,
                68: mm["mode_matches"]["playint"] >= 3,
                69: mm["mode_wins"]["playint"] >= 3,
                70: mm["matches_total"] >= 12,
            }[n]
        if 71 <= n <= 80:
            if n == 71: return levels[70] >= 5
            if n == 72: return levels[75] >= 3
            if n == 73: return levels[80] >= 2
            if n == 74: return levels[85] >= 1
            if n == 75: return c["PLAYER_PURCHASE"] >= 10
            if n == 76: return s["COIN_SPENT"] >= 100000
            if n == 77: return s["COIN_SPENT"] >= 250000
            if n == 78: return s["RUBY_SPENT"] >= 10
            if n == 79: return c["UPGRADE_APPLIED"] >= 8
            if n == 80: return c["LOADOUT_CHANGED"] >= 6
        if 81 <= n <= 84:
            if n == 81: return c["CLAIM_SUCCESS"] >= 15
            if n == 82: return _consecutive_days(em["dates_by_type"]["DAILY_REWARD_CLAIM"], 7)
            if n == 83: return _consecutive_days(em["dates_by_type"]["DAILY_REWARD_CLAIM"], 7)
            return _daily_quest_week_streak(completions, 7)
        if 85 <= n <= 100:
            if n == 85: return _daily_full_week_clear(completions)
            if n == 86: return c["TOURNAMENT_MATCH_COMPLETED"] >= 5
            if n == 87: return c["AUCTION_JOINED"] >= 5
            if n == 88: return c["AUCTION_BID_WON"] >= 3
            if n == 89: return len(ul["CARD_VIEW:player_key"]) >= 15
            if n == 90: return c["STAT_VIEW"] >= 15
            if n == 91: return c["PLAYING_XI_CONFIRMED"] >= 5
            if n == 92: return c["OVERSEAS_XI_CONFIRMED"] >= 3
            if n == 93: return c["TEAM_UPDATE"] >= 6
            if n == 94: return len(em["loadout_keys"]) >= 3
            if n == 95: return c["PACK_OPENED"] >= 5
            if n == 96: return c["PACK_PURCHASED"] >= 5
            if n == 97: return c["QUEST_EVENT"] >= 15
            if n == 98: return mm["wins_total"] >= 10
            if n == 99: return len(completed_ids) >= 5
            if n == 100: return len(completed_ids) >= 6

    else:  # monthly
        if n <= 20:
            if n <= 10:
                threshold = _first_int(task["description"]) or 0
                if n == 10: return mm["runs_total"] >= threshold and mm["matches_total"] >= 15
                return mm["runs_total"] >= threshold
            threshold = _first_int(task["description"]) or 0
            if n in (16,17): return max(mm["chase_player_runs"].values() or [0]) >= threshold
            if n == 18: return mm["centuries"] >= 3
            if n == 19: return mm["defense_fifties"] >= 3
            if n == 20: return mm["defense_centuries"] >= 2
            return max(mm["player_runs"].values() or [0]) >= threshold
        if 21 <= n <= 30:
            threshold = _first_int(task["description"]) or 0
            if n in (21,22,23): return mm["fours_total"] >= threshold
            if n in (29,30): return mm["single_match_player_sixes_max"] >= threshold
            return mm["sixes_total"] >= threshold
        if 31 <= n <= 40:
            threshold = _first_int(task["description"]) or 0
            if n in (36,37): return mm["single_match_player_wickets_max"] >= threshold
            if n in (38,39,40): return mm["phase_wickets"][{38:"powerplay",39:"middle",40:"death"}[n]] >= threshold
            return mm["wickets_total"] >= threshold
        if 41 <= n <= 50:
            threshold = _first_int(task["description"]) or 0
            if n <= 43: return mm["dots_total"] >= threshold
            if n in (44,45,46):
                phase={44:"powerplay",45:"middle",46:"death"}[n]
                limit={44:7,45:7,46:9}[n]
                return sum(1 for v in mm["phase_over_runs"][phase] if v <= limit) >= 8
            phase={47:"powerplay",48:"middle",49:"death",50:"death"}[n]
            return mm["phase_runs"][phase] >= threshold
        if 51 <= n <= 64:
            if n in (51,52,53): return mm["chase_wins"] >= {51:5,52:10,53:15}[n]
            if n == 54: return mm["late_chase_wins"] >= 5
            if n == 55: return mm["runs_chase"] >= 1000
            if n == 56: return mm["chase_century_wins"] >= 3
            if n == 57: return mm["chase_fifty_wins"] >= 5
            if n in (58,59,60): return mm["defense_wins"] >= {58:5,59:10,60:15}[n]
            if n == 61: return mm["wickets_defense"] >= 25
            if n == 62: return mm["single_match_player_wickets_max"] >= 5 and any(x.get("defending") for x in mm["summary_rows"])
            if n == 63: return mm["comeback_chase_wins"] >= 5
            if n == 64: return mm["comeback_defense_wins"] >= 5
        if 65 <= n <= 70:
            return {
                65: mm["mode_matches"]["play"] >= 15,
                66: mm["mode_matches"]["playipl"] >= 10,
                67: mm["mode_wins"]["playipl"] >= 7,
                68: mm["mode_matches"]["playint"] >= 10,
                69: mm["mode_wins"]["playint"] >= 7,
                70: mm["matches_total"] >= 30,
            }[n]
        if 71 <= n <= 80:
            if n == 71: return levels[70] >= 10
            if n == 72: return levels[75] >= 5
            if n == 73: return levels[80] >= 3
            if n == 74: return levels[85] >= 2
            if n == 75: return c["PLAYER_PURCHASE"] >= 20
            if n == 76: return s["COIN_SPENT"] >= 250000
            if n == 77: return s["COIN_SPENT"] >= 500000
            if n == 78: return s["RUBY_SPENT"] >= 25
            if n == 79: return c["UPGRADE_APPLIED"] >= 20
            if n == 80: return c["LOADOUT_CHANGED"] >= 15
        if 81 <= n <= 86:
            if n == 81: return c["CLAIM_SUCCESS"] >= 40
            if n == 82: return c["CLAIM_SUCCESS"] >= 60
            if n == 83: return _consecutive_days(em["dates_by_type"]["DAILY_REWARD_CLAIM"], 14)
            if n == 84: return _consecutive_days(em["dates_by_type"]["DAILY_REWARD_CLAIM"], 30)
            if n == 85: return _daily_quest_streak(completions, 30)
            if n == 86: return _weekly_quest_four_weeks(completions)
        if 87 <= n <= 100:
            if n == 87: return c["AUCTION_JOINED"] >= 12
            if n == 88: return c["AUCTION_BID_WON"] >= 8
            if n == 89: return len(ul["CARD_VIEW:player_key"]) >= 30
            if n == 90: return c["STAT_VIEW"] >= 30
            if n == 91: return c["PLAYING_XI_CONFIRMED"] >= 12
            if n == 92: return c["OVERSEAS_XI_CONFIRMED"] >= 6
            if n == 93: return c["TEAM_UPDATE"] >= 15
            if n == 94: return len(em["loadout_keys"]) >= 5
            if n == 95: return c["PACK_OPENED"] >= 15
            if n == 96: return c["PACK_PURCHASED"] >= 10
            if n == 97: return c["QUEST_EVENT"] >= 30
            if n == 98: return mm["wins_total"] >= 20
            if n == 99: return len(completed_ids) >= 10
            if n == 100: return len(completed_ids) >= 10
    return False


def _daily_quest_streak(completions: list[dict[str, Any]], days: int) -> bool:
    by_date = _daily_quest_day_set(completions)
    dates = set(by_date.keys())
    good = {d for d, ids in by_date.items() if ids}
    return _consecutive_days(good, days)


def _daily_quest_week_streak(completions: list[dict[str, Any]], days: int) -> bool:
    by_date = _daily_quest_day_set(completions)
    # The latest run of completed daily dates must span at least 7 days.
    return _consecutive_days(set(by_date.keys()), days)


def _daily_full_week_clear(completions: list[dict[str, Any]]) -> bool:
    by_date = _daily_quest_day_set(completions)
    return sum(1 for ids in by_date.values() if len(ids) >= 5) >= 7


def _weekly_quest_four_weeks(completions: list[dict[str, Any]]) -> bool:
    weeks = {str(r.get("period_key")) for r in completions if str(r.get("period_type")) == "weekly"}
    return len(weeks) >= 4


async def evaluate_and_complete_user(user_id: int) -> None:
    for period_type in PERIODS:
        try:
            period, _events, _summaries, completions, metrics = await _load_current_metrics(user_id, period_type)
            _, tasks, _done = await get_quest_view(user_id, period_type)
            for task in tasks:
                if await is_task_completed(user_id, period_type, period, task["id"]):
                    continue
                if _task_satisfied(task, period_type, metrics, completions):
                    await _complete_task(user_id, period_type, period, task)
        except Exception as exc:
            print(f"[quest] evaluation failed for user_id={user_id} period={period_type}: {exc!r}")


async def is_task_completed(user_id: int, period_type: str, period: dict[str, Any], task_id: str) -> bool:
    row = await fetchrow(
        "SELECT 1 FROM quest_completions WHERE user_id=$1 AND period_type=$2 AND period_key=$3 AND task_id=$4 LIMIT 1;",
        int(user_id), period_type, str(period["period_key"]), str(task_id),
    )
    return row is not None


async def _complete_task(user_id: int, period_type: str, period: dict[str, Any], task: dict[str, Any]) -> bool:
    from services.quest_catalog import REWARD_MAP
    reward = REWARD_MAP[period_type][task["difficulty"]]
    completion_id = uuid.uuid4()

    async def _tx(conn):
        inserted = await conn.fetchval(
            """
            INSERT INTO quest_completions(completion_id,user_id,period_type,period_key,task_id,sigils,coins,rubies,completed_at)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,NOW())
            ON CONFLICT(user_id,period_type,period_key,task_id) DO NOTHING
            RETURNING completion_id;
            """,
            completion_id, int(user_id), period_type, str(period["period_key"]), task["id"],
            int(reward["sigils"]), int(reward["coins"]), int(reward["rubies"]),
        )
        if inserted is None:
            return False
        await conn.execute(
            """
            UPDATE users SET
                sigils = COALESCE(sigils,0) + $1,
                total_sigils_earned = COALESCE(total_sigils_earned,0) + $1,
                balance = COALESCE(balance,0) + $2,
                rubies = COALESCE(rubies,0) + $3,
                total_rubies_earned = COALESCE(total_rubies_earned,0) + $3,
                last_seen_at = NOW()
            WHERE user_id=$4;
            """,
            int(reward["sigils"]), int(reward["coins"]), int(reward["rubies"]), int(user_id),
        )
        return True

    awarded = await transaction(_tx)
    if not awarded:
        return False

    # Completion itself is also useful history for streak tasks on future days.
    try:
        await _notify_completion(user_id, task, reward)
    except Exception as exc:
        print(f"[quest] Completion notification failed user_id={user_id} task={task['id']}: {exc!r}")
    return True


async def _notify_completion(user_id: int, task: dict[str, Any], reward: dict[str, int]) -> None:
    from app import app
    import html

    done = custom_emoji_html(DONE_QUEST_EMOJI_ID, "✅")
    sg = custom_emoji_html(SIGIL_EMOJI_ID, "✨")
    coins = custom_emoji_html(COINS_EMOJI_ID, "🪙")
    rubies = custom_emoji_html(RUBIES_EMOJI_ID, "💎")
    title = html.escape(task["title"])
    desc = html.escape(task["description"])
    lines = [
        f"{done} <b>QUEST COMPLETED</b>", "",
        f"{done} <b>{title}</b>",
        f"↳ <blockquote><i>{desc}</i></blockquote>",
        "",
        f"<b>{sg} +{int(reward['sigils'])} Sigils</b>",
    ]
    if reward["coins"]:
        lines.append(f"<b>{coins} +{int(reward['coins']):,} Coins</b>")
    if reward["rubies"]:
        lines.append(f"<b>{rubies} +{int(reward['rubies']):,} Rubies</b>")
    await app.send_message(int(user_id), "\n".join(lines), parse_mode="HTML")


async def record_match_summary_for_session(session: Any, innings_1: dict[str, Any], innings_2: dict[str, Any], *, engine: str, match_type: str) -> None:
    """Persist one verified Quest summary per participant after a decided match."""
    match = getattr(session, "match", {}) or {}
    challenger_id = int(match.get("challenger_id") or 0)
    opponent_id = int(match.get("opponent_id") or 0)
    if not challenger_id or not opponent_id:
        return
    try:
        winner_id, _margin = (
            (None, "") if int(innings_2.get("runs") or 0) == int(innings_1.get("runs") or 0)
            else ((int(innings_2.get("batting_team_id")), "") if int(innings_2.get("runs") or 0) >= int(innings_1.get("runs") or 0) + 1 else (int(innings_1.get("batting_team_id")), ""))
        )
    except Exception:
        winner_id = None

    full_overs = list(getattr(session, "quest_over_history", []) or [])
    current = dict(getattr(session, "quest_current_over", {}) or {})
    if current.get("balls"):
        current.setdefault("innings_number", int(innings_2.get("innings_number") or 2))
        current.setdefault("over_number", ((int(current.get("legal_balls_after") or 0) - 1) // 6) + 1 if current.get("legal_balls_after") else 1)
        current.setdefault("phase", _phase_for_over(int(current.get("over_number") or 1)))
        current.setdefault("completed", False)
        full_overs.append(current)

    # Retain only compact JSON-safe dictionaries.
    safe_overs = []
    for ov in full_overs:
        safe_overs.append({
            k: int(v) if isinstance(v, bool) is False and isinstance(v, (int, float)) else bool(v) if isinstance(v, bool) else v
            for k, v in ov.items()
            if k in {"innings_number","over_number","phase","batting_team_id","bowling_team_id","runs","wickets","dots","fours","sixes","balls","completed"}
        })

    async def _one(user_id: int):
        bat = innings_1 if int(innings_1.get("batting_team_id") or 0) == user_id else innings_2
        bowl = innings_1 if int(innings_1.get("bowling_team_id") or 0) == user_id else innings_2
        chasing = int(bat.get("innings_number") or 1) == 2
        defending = int(bat.get("innings_number") or 1) == 1
        target = int(innings_2.get("target") or (int(innings_1.get("runs") or 0) + 1)) if chasing else None
        late_chase = chasing and bool(winner_id == user_id) and int(innings_2.get("legal_balls") or 0) >= 90
        comeback_chase = False
        if chasing and target:
            chase_overs = [x for x in safe_overs if int(x.get("innings_number") or 0) == 2 and int(x.get("batting_team_id") or 0) == user_id]
            balls=0; score=0
            for ov in chase_overs:
                score += int(ov.get("runs") or 0); balls += int(ov.get("balls") or 0)
                if balls and score < (target * balls / 120.0):
                    comeback_chase = True
        comeback_defense = defending and bool(winner_id == user_id) and any(int(x.get("bowling_team_id") or 0)==user_id and (int(x.get("fours") or 0)+int(x.get("sixes") or 0))>0 for x in safe_overs if int(x.get("innings_number") or 0)==1)
        batting_fours = sum(int(b.get("fours") or 0) for b in bat.get("batters", []) or [])
        batting_sixes = sum(int(b.get("sixes") or 0) for b in bat.get("batters", []) or [])
        bowling_wickets = int(sum(int(b.get("wickets") or 0) for b in bowl.get("bowlers", []) or []))
        conceded_boundary = defending and any(int(x.get("bowling_team_id") or 0)==user_id and (int(x.get("fours") or 0)+int(x.get("sixes") or 0))>0 for x in safe_overs if int(x.get("innings_number") or 0)==1)
        data = {
            "user_id": user_id, "engine": engine, "match_id": int(getattr(session,"match_id",0) or 0),
            "match_type": match_type, "pitch": str(getattr(session,"pitch",None) or ""),
            "won": bool(winner_id == user_id), "chasing": chasing, "defending": defending,
            "late_chase": late_chase, "comeback_chase": comeback_chase and bool(winner_id == user_id),
            "comeback_defense": comeback_defense, "target": target,
            "batting_runs": int(bat.get("runs") or 0), "bowling_wickets": bowling_wickets,
            "conceded_boundary": conceded_boundary,
            "batting": bat.get("batters", []), "bowling": bowl.get("bowlers", []),
            "over_history": safe_overs,
        }
        await execute(
            """
            INSERT INTO quest_match_summaries(
                user_id,engine,match_id,match_type,pitch,won,chasing,defending,late_chase,
                comeback_chase,comeback_defense,target,batting_runs,bowling_wickets,conceded_boundary,
                batting,bowling,over_history,played_at
            ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb,$17::jsonb,$18::jsonb,NOW())
            ON CONFLICT(user_id,engine,match_id) DO UPDATE SET
                won=EXCLUDED.won,chasing=EXCLUDED.chasing,defending=EXCLUDED.defending,
                late_chase=EXCLUDED.late_chase,comeback_chase=EXCLUDED.comeback_chase,
                comeback_defense=EXCLUDED.comeback_defense,target=EXCLUDED.target,
                batting_runs=EXCLUDED.batting_runs,bowling_wickets=EXCLUDED.bowling_wickets,
                conceded_boundary=EXCLUDED.conceded_boundary,batting=EXCLUDED.batting,
                bowling=EXCLUDED.bowling,over_history=EXCLUDED.over_history,played_at=NOW();
            """,
            user_id, engine, data["match_id"], match_type, data["pitch"], data["won"], data["chasing"], data["defending"], data["late_chase"],
            data["comeback_chase"], data["comeback_defense"], data["target"], data["batting_runs"], data["bowling_wickets"], data["conceded_boundary"],
            json.dumps(data["batting"], default=str), json.dumps(data["bowling"], default=str), json.dumps(data["over_history"], default=str),
        )
        await record_quest_event(user_id, "MATCH_COMPLETED", metadata={"engine": engine, "match_id": data["match_id"], "mode": match_type})

    for uid in (challenger_id, opponent_id):
        await _one(uid)
