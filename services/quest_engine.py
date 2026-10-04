"""Crickium Quest runtime: periods, per-user assignment, verification and rewards.

The packaged quest_catalog.csv is the task-definition source of truth. This module
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
    """Build lightweight event aggregates used by event-backed Quest metrics."""
    counts: defaultdict[str, int] = defaultdict(int)
    sums: defaultdict[str, int] = defaultdict(int)
    dates_by_type: defaultdict[str, set[str]] = defaultdict(set)
    return_meta: list[dict[str, Any]] = []
    for ev in events:
        et = str(ev.get("event_type") or "").upper()
        val = int(ev.get("value_int") or 0)
        counts[et] += 1
        sums[et] += val
        meta = _json(ev.get("metadata"), {})
        if not isinstance(meta, dict):
            meta = {}
        if meta.get("event_date"):
            dates_by_type[et].add(str(meta["event_date"]))
        return_meta.append({"type": et, "value": val, "metadata": meta})
    return {
        "counts": counts,
        "sums": sums,
        "dates_by_type": dates_by_type,
        "rows": return_meta,
    }



def _phase_for_over(over_number: int) -> str:
    if over_number <= 6:
        return "powerplay"
    if over_number <= 15:
        return "middle"
    return "death"


def _match_metrics(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Build reusable match aggregates without encoding Quest IDs."""
    m: dict[str, Any] = {
        "summary_rows": summaries,
        "runs_total": 0,
        "wins_total": 0,
        "matches_total": len(summaries),
        "mode_matches": defaultdict(int),
        "mode_wins": defaultdict(int),
    }
    for summary in summaries:
        m["runs_total"] += int(summary.get("batting_runs") or 0)
        if bool(summary.get("won")):
            m["wins_total"] += 1
        mode = str(summary.get("match_type") or summary.get("engine") or "").upper()
        if mode:
            m["mode_matches"][mode] += 1
            if bool(summary.get("won")):
                m["mode_wins"][mode] += 1
    return m


def _task_qualifiers(task: dict[str, Any]) -> dict[str, Any]:
    obj = task.get("qualifiers_obj")
    if isinstance(obj, dict):
        return obj
    raw = str(task.get("qualifiers") or "none").strip()
    if not raw or raw.lower() == "none":
        return {}
    out: dict[str, Any] = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key:
            continue
        try:
            out[key] = float(value) if "." in value else int(value)
        except ValueError:
            out[key] = value
    return out


def _filter_summaries(summaries: list[dict[str, Any]], qualifiers: dict[str, Any]) -> list[dict[str, Any]]:
    mode = str(qualifiers.get("mode") or "").upper().strip()
    pitch = str(qualifiers.get("pitch") or "").lower().strip()
    side = str(qualifiers.get("side") or "").lower().strip()
    result = str(qualifiers.get("result") or "").lower().strip()
    out: list[dict[str, Any]] = []
    for summary in summaries:
        if mode and str(summary.get("match_type") or summary.get("engine") or "").upper() != mode:
            continue
        if pitch and str(summary.get("pitch") or "").lower() != pitch:
            continue
        if side == "chasing" and not bool(summary.get("chasing")):
            continue
        if side == "defending" and not bool(summary.get("defending")):
            continue
        if result == "won" and not bool(summary.get("won")):
            continue
        out.append(summary)
    return out


def _batters(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _json(summary.get("batting"), []) or []
    return [dict(x) for x in rows if isinstance(x, dict)]


def _bowlers(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _json(summary.get("bowling"), []) or []
    return [dict(x) for x in rows if isinstance(x, dict)]


def _overs(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _json(summary.get("over_history"), []) or []
    return [dict(x) for x in rows if isinstance(x, dict)]


def _event_rows(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    wanted = str(event_type).upper()
    rows: list[dict[str, Any]] = []
    for ev in events:
        et = str(ev.get("event_type") or "").upper()
        if et != wanted:
            continue
        meta = _json(ev.get("metadata"), {})
        if not isinstance(meta, dict):
            meta = {}
        rows.append({"value": int(ev.get("value_int") or 0), "metadata": meta, "occurred_at": ev.get("occurred_at")})
    return rows


def _completion_rows_for_period(completions: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in completions:
        when = row.get("completed_at")
        if when is None:
            continue
        if getattr(when, "tzinfo", None) is None:
            when = when.replace(tzinfo=start.tzinfo)
        if start <= when < end:
            out.append(row)
    return out


def _task_days_from_completions(completions: list[dict[str, Any]], start: datetime, end: datetime, period_type: str | None = None) -> dict[str, set[str]]:
    by_date: dict[str, set[str]] = defaultdict(set)
    for row in _completion_rows_for_period(completions, start, end):
        if period_type and str(row.get("period_type")) != period_type:
            continue
        when = row.get("completed_at")
        local = when.astimezone() if getattr(when, "tzinfo", None) else when
        by_date[local.strftime("%Y-%m-%d")].add(str(row.get("task_id")))
    return by_date


def _longest_streak(dates: set[str]) -> int:
    if not dates:
        return 0
    parsed = sorted({datetime.fromisoformat(d).date() for d in dates if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)})
    if not parsed:
        return 0
    best = run = 1
    for prev, cur in zip(parsed, parsed[1:]):
        if (cur - prev).days == 1:
            run += 1
            best = max(best, run)
        else:
            run = 1
    return best


def _event_sum(events: list[dict[str, Any]], event_type: str, source: str | None = None) -> int:
    total = 0
    for row in _event_rows(events, event_type):
        if source is not None and str(row["metadata"].get("source") or "") != source:
            continue
        total += int(row["value"])
    return total


def _count_event(events: list[dict[str, Any]], event_type: str, predicate=None) -> int:
    count = 0
    for row in _event_rows(events, event_type):
        if predicate is None or predicate(row):
            count += 1
    return count


def _distinct_event_metadata(events: list[dict[str, Any]], event_type: str, key: str, predicate=None) -> set[str]:
    values: set[str] = set()
    for row in _event_rows(events, event_type):
        if predicate is not None and not predicate(row):
            continue
        value = row["metadata"].get(key)
        if value is not None and str(value) != "":
            values.add(str(value))
    return values


def _task_satisfied(task: dict[str, Any], period_type: str, metrics: dict[str, Any], completions: list[dict[str, Any]]) -> bool:
    """Evaluate a task from catalog metadata rather than numeric task IDs."""
    metric = str(task.get("metric") or "").strip()
    target = int(task.get("target") or 0)
    q = _task_qualifiers(task)
    events = metrics["raw_events"]
    summaries_all = metrics["raw_summaries"]
    summaries = _filter_summaries(summaries_all, q)
    user_id = int(metrics["user_id"])
    period_start = metrics["period_start"]
    period_end = metrics["period_end"]

    # Match/team volume metrics.
    if metric == "team_runs":
        if q.get("min_matches") is not None and len(summaries) < int(q["min_matches"]): return False
        return sum(int(s.get("batting_runs") or 0) for s in summaries) >= target
    if metric == "team_runs_max":
        return max([int(s.get("batting_runs") or 0) for s in summaries] or [0]) >= target
    if metric == "team_score_count":
        score_min = int(q.get("score_min") or 0)
        return sum(1 for s in summaries if int(s.get("batting_runs") or 0) >= score_min) >= target
    if metric == "avg_team_runs":
        min_matches = int(q.get("min_matches") or 0)
        if len(summaries) < min_matches or not summaries: return False
        return (sum(int(s.get("batting_runs") or 0) for s in summaries) / len(summaries)) >= float(target)
    if metric == "matches_played":
        return len(summaries) >= target
    if metric == "wins":
        return sum(1 for s in summaries if bool(s.get("won"))) >= target
    if metric == "win_streak":
        return _longest_win_streak(summaries) >= target
    if metric == "win_rate":
        min_matches = int(q.get("min_matches") or 0)
        if len(summaries) < min_matches or not summaries: return False
        wins = sum(1 for s in summaries if bool(s.get("won")))
        return (wins * 100.0 / len(summaries)) >= float(target)
    if metric == "balanced_wins":
        chase = int(q.get("chase") or 0)
        defend = int(q.get("defend") or 0)
        return (
            sum(1 for s in summaries_all if bool(s.get("won")) and bool(s.get("chasing"))) >= chase
            and sum(1 for s in summaries_all if bool(s.get("won")) and bool(s.get("defending"))) >= defend
        )
    if metric == "mode_wins_min":
        per_mode = int(q.get("per_mode") or target or 1)
        wanted = ("PLAY", "PLAYIPL", "PLAYINT")
        return all(sum(1 for s in summaries_all if str(s.get("match_type") or "").upper() == mode and bool(s.get("won"))) >= per_mode for mode in wanted)
    if metric == "distinct_modes_played":
        return len({str(s.get("match_type") or "").upper() for s in summaries if str(s.get("match_type") or "")}) >= target
    if metric == "distinct_modes_won":
        return len({str(s.get("match_type") or "").upper() for s in summaries if bool(s.get("won"))}) >= target
    if metric == "distinct_pitch_played":
        return len({str(s.get("pitch") or "").lower() for s in summaries if str(s.get("pitch") or "")}) >= target
    if metric == "distinct_pitch_wins":
        return len({str(s.get("pitch") or "").lower() for s in summaries if bool(s.get("won")) and str(s.get("pitch") or "")}) >= target

    # Batting metrics.
    all_batting = [(s, b) for s in summaries for b in _batters(s)]
    if metric == "player_runs_match":
        return max([int(b.get("runs") or 0) for _, b in all_batting] or [0]) >= target
    if metric == "player_runs_total":
        totals: defaultdict[str, int] = defaultdict(int)
        for _, b in all_batting:
            pid = str(b.get("player_id") or "")
            if pid: totals[pid] += int(b.get("runs") or 0)
        return max(list(totals.values()) or [0]) >= target
    if metric == "player_sixes_match":
        return max([int(b.get("sixes") or 0) for _, b in all_batting] or [0]) >= target
    if metric == "player_sixes_total":
        totals: defaultdict[str, int] = defaultdict(int)
        for _, b in all_batting:
            pid = str(b.get("player_id") or "")
            if pid: totals[pid] += int(b.get("sixes") or 0)
        return max(list(totals.values()) or [0]) >= target
    if metric == "player_boundaries_match":
        return max([int(b.get("fours") or 0) + int(b.get("sixes") or 0) for _, b in all_batting] or [0]) >= target
    if metric == "team_fours":
        return sum(int(b.get("fours") or 0) for _, b in all_batting) >= target
    if metric == "team_sixes":
        return sum(int(b.get("sixes") or 0) for _, b in all_batting) >= target
    if metric == "team_boundaries":
        return sum(int(b.get("fours") or 0) + int(b.get("sixes") or 0) for _, b in all_batting) >= target
    if metric == "fifty_plus_count":
        return sum(1 for _, b in all_batting if int(b.get("runs") or 0) >= 50) >= target
    if metric == "hundred_count":
        return sum(1 for _, b in all_batting if int(b.get("runs") or 0) >= 100) >= target
    if metric == "notout_innings_count":
        min_runs = int(q.get("min_runs") or 0)
        return sum(1 for _, b in all_batting if int(b.get("runs") or 0) >= min_runs and not bool(b.get("dismissed"))) >= target
    if metric == "fast_innings_count":
        min_runs = int(q.get("min_runs") or 0)
        max_balls = int(q.get("balls") or 0)
        return sum(1 for _, b in all_batting if int(b.get("runs") or 0) >= min_runs and 0 < int(b.get("balls") or 0) <= max_balls) >= target
    if metric == "sr_innings_count":
        min_runs = int(q.get("min_runs") or 0)
        sr = float(q.get("sr") or 0)
        return sum(1 for _, b in all_batting if int(b.get("runs") or 0) >= min_runs and int(b.get("balls") or 0) > 0 and (int(b.get("runs") or 0) * 100.0 / int(b.get("balls"))) >= sr) >= target
    if metric == "multi_fifty_match":
        n = int(q.get("n") or 2)
        return sum(1 for s in summaries if sum(1 for b in _batters(s) if int(b.get("runs") or 0) >= 50) >= n) >= target
    if metric == "depth_match":
        min_runs = int(q.get("min_runs") or 0)
        batters = int(q.get("batters") or 0)
        return sum(1 for s in summaries if sum(1 for b in _batters(s) if int(b.get("runs") or 0) >= min_runs) >= batters) >= target
    if metric == "distinct_fifty_players":
        ids = {str(b.get("player_id")) for _, b in all_batting if b.get("player_id") is not None and int(b.get("runs") or 0) >= 50}
        return len(ids) >= target

    # Bowling metrics.
    all_bowling = [(s, b) for s in summaries for b in _bowlers(s)]
    if metric == "team_wickets":
        return sum(int(s.get("bowling_wickets") or 0) for s in summaries) >= target
    if metric == "bowler_wickets_match":
        return max([int(b.get("wickets") or 0) for _, b in all_bowling] or [0]) >= target
    if metric == "bowler_wickets_total":
        totals: defaultdict[str, int] = defaultdict(int)
        for _, b in all_bowling:
            pid = str(b.get("player_id") or "")
            if pid: totals[pid] += int(b.get("wickets") or 0)
        return max(list(totals.values()) or [0]) >= target
    if metric == "haul_count":
        min_w = int(q.get("w") or 0)
        return sum(1 for _, b in all_bowling if int(b.get("wickets") or 0) >= min_w) >= target
    if metric == "econ_spell_count":
        min_balls = int(q.get("min_balls") or 0)
        econ_limit = float(q.get("econ") or 99)
        min_wickets = int(q.get("w") or 0)
        count = 0
        for _, b in all_bowling:
            balls = int(b.get("balls") or 0)
            runs = int(b.get("runs") or 0)
            wickets = int(b.get("wickets") or 0)
            if balls >= min_balls and balls > 0 and runs * 6.0 / balls <= econ_limit and wickets >= min_wickets:
                count += 1
        return count >= target
    if metric == "bowled_out_count":
        return sum(1 for s in summaries if int(s.get("bowling_wickets") or 0) >= 10) >= target

    # Over/phase metrics use the user-owned side in each summary.
    relevant_overs: list[dict[str, Any]] = []
    for summary in summaries:
        for ov in _overs(summary):
            if int(ov.get("batting_team_id") or 0) == user_id or int(ov.get("bowling_team_id") or 0) == user_id:
                relevant_overs.append(ov)
    if metric == "dots_bowled":
        phase = str(q.get("phase") or "").lower()
        return sum(int(ov.get("dots") or 0) for ov in relevant_overs if int(ov.get("bowling_team_id") or 0) == user_id and (not phase or str(ov.get("phase") or _phase_for_over(int(ov.get("over_number") or 0))).lower() == phase)) >= target
    if metric == "maiden_overs":
        return sum(1 for ov in relevant_overs if int(ov.get("bowling_team_id") or 0) == user_id and int(ov.get("balls") or 0) == 6 and int(ov.get("runs") or 0) == 0) >= target
    if metric == "wicket_overs":
        w = int(q.get("w") or 0)
        return sum(1 for ov in relevant_overs if int(ov.get("bowling_team_id") or 0) == user_id and int(ov.get("wickets") or 0) >= w) >= target
    if metric == "big_overs":
        minimum = int(q.get("min_over_runs") or 0)
        return sum(1 for ov in relevant_overs if int(ov.get("batting_team_id") or 0) == user_id and int(ov.get("balls") or 0) == 6 and int(ov.get("runs") or 0) >= minimum) >= target
    if metric == "phase_runs":
        phase = str(q.get("phase") or "").lower()
        return sum(int(ov.get("runs") or 0) for ov in relevant_overs if int(ov.get("batting_team_id") or 0) == user_id and str(ov.get("phase") or _phase_for_over(int(ov.get("over_number") or 0))).lower() == phase) >= target
    if metric == "phase_wickets":
        phase = str(q.get("phase") or "").lower()
        return sum(int(ov.get("wickets") or 0) for ov in relevant_overs if int(ov.get("bowling_team_id") or 0) == user_id and str(ov.get("phase") or _phase_for_over(int(ov.get("over_number") or 0))).lower() == phase) >= target
    if metric == "phase_boundaries":
        phase = str(q.get("phase") or "").lower()
        return sum(int(ov.get("fours") or 0) + int(ov.get("sixes") or 0) for ov in relevant_overs if int(ov.get("batting_team_id") or 0) == user_id and str(ov.get("phase") or _phase_for_over(int(ov.get("over_number") or 0))).lower() == phase) >= target
    if metric == "phase_tight_overs":
        phase = str(q.get("phase") or "").lower()
        limit = int(q.get("max_over_runs") or 0)
        return sum(1 for ov in relevant_overs if int(ov.get("bowling_team_id") or 0) == user_id and int(ov.get("balls") or 0) == 6 and int(ov.get("runs") or 0) <= limit and str(ov.get("phase") or _phase_for_over(int(ov.get("over_number") or 0))).lower() == phase) >= target
    if metric == "phase_innings_count":
        phase = str(q.get("phase") or "").lower()
        phase_min = int(q.get("phase_min") or 0)
        count = 0
        for summary in summaries:
            total = sum(int(ov.get("runs") or 0) for ov in _overs(summary) if int(ov.get("batting_team_id") or 0) == user_id and str(ov.get("phase") or _phase_for_over(int(ov.get("over_number") or 0))).lower() == phase)
            if total >= phase_min: count += 1
        return count >= target

    # Chasing/defending and result-context metrics.
    if metric == "late_chase_wins":
        return sum(1 for s in summaries if bool(s.get("chasing")) and bool(s.get("won")) and bool(s.get("late_chase"))) >= target
    if metric == "chase_target_wins":
        target_min = int(q.get("target_min") or 0)
        return sum(1 for s in summaries if bool(s.get("chasing")) and bool(s.get("won")) and int(s.get("target") or 0) >= target_min) >= target
    if metric == "chase_wins_low_wkts":
        max_wkts = int(q.get("max_wkts_lost") or 0)
        return sum(1 for s in summaries if bool(s.get("chasing")) and bool(s.get("won")) and sum(1 for b in _batters(s) if bool(b.get("dismissed"))) <= max_wkts) >= target
    if metric == "defend_total_wins":
        score_min = int(q.get("score_min") or 0)
        return sum(1 for s in summaries if bool(s.get("defending")) and bool(s.get("won")) and int(s.get("batting_runs") or 0) >= score_min) >= target
    if metric == "wins_low_conceded":
        max_conceded = int(q.get("max_conceded") or 0)
        return sum(1 for s in summaries if bool(s.get("won")) and sum(int(b.get("runs") or 0) for b in _bowlers(s)) <= max_conceded) >= target
    if metric == "complete_win":
        return sum(1 for s in summaries if bool(s.get("won")) and any(int(b.get("runs") or 0) >= 50 for b in _batters(s)) and any(int(b.get("wickets") or 0) >= 3 for b in _bowlers(s))) >= target
    if metric == "allround_match":
        min_runs = int(q.get("runs") or 0)
        min_w = int(q.get("w") or 0)
        qualifying = 0
        for s in summaries:
            bats = {str(b.get("player_id")): int(b.get("runs") or 0) for b in _batters(s) if b.get("player_id") is not None}
            bowls = {str(b.get("player_id")): int(b.get("wickets") or 0) for b in _bowlers(s) if b.get("player_id") is not None}
            if any(bats.get(pid, 0) >= min_runs and bowls.get(pid, 0) >= min_w for pid in set(bats) & set(bowls)):
                qualifying += 1
        return qualifying >= target
    if metric == "wins" and q.get("side"):
        # Kept for clarity; side filtering already reduced the summary set.
        return sum(1 for s in summaries if bool(s.get("won"))) >= target

    # Event-backed progression/economy metrics.
    if metric == "purchase_ovr_count":
        minimum = int(q.get("ovr_min") or 0)
        return _count_event(events, "PLAYER_PURCHASE", lambda r: int(r["metadata"].get("ovr") or 0) >= minimum) >= target
    if metric == "purchase_special_count":
        return _count_event(events, "PLAYER_PURCHASE", lambda r: bool(r["metadata"].get("is_special"))) >= target
    if metric == "shop_coins_spent":
        return _event_sum(events, "COIN_SPENT", "player_shop") >= target
    if metric == "upgrade_rubies_spent":
        return sum(r["value"] for r in _event_rows(events, "RUBY_SPENT") if str(r["metadata"].get("source") or "") in {"upgrade_purchase", "upgrade_levelup"}) >= target
    if metric == "upgrade_levelups":
        return _count_event(events, "RUBY_SPENT", lambda r: str(r["metadata"].get("source") or "") == "upgrade_levelup") >= target
    if metric == "upgrades_equipped":
        return _count_event(events, "UPGRADE_APPLIED") >= target
    if metric == "distinct_players_upgraded":
        return len(_distinct_event_metadata(events, "UPGRADE_APPLIED", "player_id")) >= target
    if metric == "distinct_upgrades_equipped":
        return len(_distinct_event_metadata(events, "UPGRADE_APPLIED", "upgrade_key")) >= target
    if metric == "claim_count":
        return _count_event(events, "CLAIM_SUCCESS") >= target
    if metric == "claim_days":
        return len({str(r["metadata"].get("event_date")) for r in _event_rows(events, "CLAIM_SUCCESS") if r["metadata"].get("event_date")}) >= target
    if metric == "daily_reward_streak":
        dates = {str(r["metadata"].get("event_date")) for r in _event_rows(events, "DAILY_REWARD_CLAIM") if r["metadata"].get("event_date")}
        return _longest_streak(dates) >= target
    if metric == "packs_opened":
        pack_key = q.get("pack_key")
        return _count_event(events, "PACK_OPENED", lambda r: pack_key is None or str(r["metadata"].get("pack_key") or "") == str(pack_key)) >= target
    if metric == "packs_purchased":
        pack_key = q.get("pack_key")
        return _count_event(events, "PACK_PURCHASED", lambda r: pack_key is None or str(r["metadata"].get("pack_key") or "") == str(pack_key)) >= target
    if metric == "distinct_packs_opened":
        return len(_distinct_event_metadata(events, "PACK_OPENED", "pack_key")) >= target
    if metric == "xi_confirmed":
        return _count_event(events, "PLAYING_XI_CONFIRMED") >= target
    if metric == "overseas_xi":
        required = int(q.get("overseas_count") or 0)
        return _count_event(events, "OVERSEAS_XI_CONFIRMED", lambda r: int(r["metadata"].get("overseas_count") or 0) == required) >= target

    # Quest-on-quest metrics are constrained to this task's current period window.
    current_completions = _completion_rows_for_period(completions, period_start, period_end)
    if metric == "daily_quests_done":
        return sum(1 for r in current_completions if str(r.get("period_type")) == "daily") >= target
    if metric == "weekly_quests_done":
        return sum(1 for r in current_completions if str(r.get("period_type")) == "weekly") >= target
    if metric == "daily_quest_streak":
        by_date = _task_days_from_completions(current_completions, period_start, period_end, "daily")
        return _longest_streak(set(by_date.keys())) >= target
    if metric == "daily_full_clear_days":
        by_date = _task_days_from_completions(current_completions, period_start, period_end, "daily")
        return sum(1 for ids in by_date.values() if len(ids) >= 5) >= target

    # The catalog is validated against known metrics before deployment. Returning
    # False is safer than accidentally granting a task whose evaluator is unknown.
    return False


def _longest_win_streak(summaries: list[dict[str, Any]]) -> int:
    best = run = 0
    ordered = sorted(
        summaries,
        key=lambda row: (
            row.get("played_at").timestamp()
            if getattr(row.get("played_at"), "timestamp", None)
            else 0.0
        ),
    )
    for summary in ordered:
        if bool(summary.get("won")):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _consecutive_days(dates: set[str], minimum: int) -> bool:
    return _longest_streak(dates) >= minimum


def _daily_quest_day_set(completions: list[dict[str, Any]], *, daily_only: bool = True) -> dict[str, set[str]]:
    by_date: dict[str, set[str]] = defaultdict(set)
    for row in completions:
        if daily_only and str(row.get("period_type")) != "daily":
            continue
        when = row.get("completed_at")
        if when is None:
            continue
        local = when.astimezone() if getattr(when, "tzinfo", None) else when
        by_date[local.strftime("%Y-%m-%d")].add(str(row.get("task_id")))
    return by_date


async def _load_current_metrics(user_id: int, period_type: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    period = await ensure_current_period(period_type)
    start = period["start_at"]
    end = period["end_at"]
    pkey = str(period["period_key"])
    events = [dict(r) for r in await fetch(
        "SELECT * FROM quest_events WHERE user_id=$1 AND occurred_at >= $2 AND occurred_at < $3 ORDER BY occurred_at;",
        int(user_id), start, end,
    )]
    summaries = [dict(r) for r in await fetch(
        "SELECT * FROM quest_match_summaries WHERE user_id=$1 AND played_at >= $2 AND played_at < $3 ORDER BY played_at;",
        int(user_id), start, end,
    )]
    completions = [dict(r) for r in await fetch(
        "SELECT * FROM quest_completions WHERE user_id=$1 ORDER BY completed_at DESC LIMIT 10000;",
        int(user_id),
    )]
    event_metrics = _events_metrics(events)
    match_metrics = _match_metrics(summaries)
    match_metrics["user_id"] = int(user_id)
    return period, events, summaries, completions, {
        "events": event_metrics,
        "matches": match_metrics,
        "period_key": pkey,
        "user_id": int(user_id),
        "period_start": start,
        "period_end": end,
        "raw_events": events,
        "raw_summaries": summaries,
    }

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
