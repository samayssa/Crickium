import time

from database import pitch_stats_repo

PITCHES = ("green", "dry", "dusty", "flat", "hard", "even", "bouncy", "slow")
TRACKED_ENGINES = {"PLAY", "PLAYINT", "PLAYIPL", "PLAYWPL"}
CACHE_SECONDS = 60

_cache: dict[str, tuple[float, tuple[dict, dict]]] = {}


def _phase(over: int) -> str:
    if over <= 6:
        return "pp"
    if over <= 16:
        return "mid"
    return "death"


def _is_bowler_wicket(ball: dict) -> bool:
    kind = str(ball.get("wicket_kind") or ball.get("dismissal") or "").lower()
    return "run out" not in kind and "run_out" not in kind and "runout" not in kind


def _innings_row(snapshot: dict) -> dict:
    row = {
        "n": 1,
        "runs": int(snapshot.get("runs") or 0),
        "wickets": int(snapshot.get("wickets") or 0),
    }
    balls = snapshot.get("analysis_balls") or []
    if not balls:
        return row
    row["detail_n"] = 1
    for ball in balls:
        phase = _phase(int(ball.get("over") or 1))
        row[f"{phase}_runs"] = row.get(f"{phase}_runs", 0) + int(ball.get("runs") or 0)
        if ball.get("wicket"):
            row[f"{phase}_wkts"] = row.get(f"{phase}_wkts", 0) + 1
            if _is_bowler_wicket(ball):
                key = "spin_wkts" if ball.get("bowler_family") == "spin" else "pace_wkts"
                row[key] = row.get(key, 0) + 1
    return row


def extract_match(payload: dict):
    engine = str(payload.get("engine") or "").upper()
    pitch = str(payload.get("pitch") or "").strip().lower()
    if engine not in TRACKED_ENGINES or pitch not in PITCHES:
        return None
    if str(payload.get("termination") or "") != "completed":
        return None
    snapshots = sorted(payload.get("innings") or [], key=lambda s: int(s.get("innings_number") or 0))
    if len(snapshots) < 2:
        return None
    first, second = snapshots[0], snapshots[1]
    winner = payload.get("winner_id")
    side = None
    if winner is not None:
        if int(winner) == int(first.get("batting_team_id") or -1):
            side = "defend"
        elif int(winner) == int(second.get("batting_team_id") or -1):
            side = "chase"
    return engine, int(payload.get("match_id") or 0), pitch, {1: _innings_row(first), 2: _innings_row(second)}, side


def _invalidate(pitch: str) -> None:
    _cache.pop(pitch, None)


async def record_completed_match(payload: dict) -> bool:
    try:
        parsed = extract_match(payload)
        if not parsed:
            return False
        engine, match_id, pitch, innings, side = parsed
        if not match_id:
            return False
        saved = await pitch_stats_repo.record_match(engine, match_id, pitch, innings, side)
        if saved:
            _invalidate(pitch)
        return saved
    except Exception as exc:
        print(f"[pitch_stats] record failed: {exc!r}")
        return False


async def record_ai_result(row: dict) -> bool:
    try:
        pitch = str(row.get("pitch") or "").strip().lower()
        if pitch not in PITCHES or int(row.get("overs") or 0) != 20 or row.get("forfeit") or row.get("batted_first") is None:
            return False
        user = {"n": 1, "runs": int(row["user_runs"]), "wickets": int(row["user_wkts"])}
        ai = {"n": 1, "runs": int(row["ai_runs"]), "wickets": int(row["ai_wkts"])}
        innings = {1: user, 2: ai} if row["batted_first"] else {1: ai, 2: user}
        outcome = row.get("outcome")
        side = None
        if outcome in ("win", "loss"):
            user_won = outcome == "win"
            side = "defend" if user_won == bool(row["batted_first"]) else "chase"
        saved = await pitch_stats_repo.record_match("AI", int(row["game_id"]), pitch, innings, side)
        if saved:
            _invalidate(pitch)
        return saved
    except Exception as exc:
        print(f"[pitch_stats] AI record failed: {exc!r}")
        return False


async def load_pitch(pitch: str) -> tuple[dict, dict]:
    hit = _cache.get(pitch)
    now = time.monotonic()
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    data = await pitch_stats_repo.load_pitch(pitch)
    _cache[pitch] = (now, data)
    return data
