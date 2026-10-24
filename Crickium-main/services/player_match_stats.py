from __future__ import annotations

from typing import Any

from database.player_user_stats_repo import record_match_player_stats


def _aggregate_rows_for_user(user_id: int, squad: list[dict[str, Any]], snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build one stats row per player in the user's XI from all innings snapshots.

    player_id is the squad-facing identity. Positive IDs are global players;
    negative IDs are special-edition players. We intentionally preserve the
    exact stored ID so /plstats data cannot collapse two editions into one.
    """
    bat_map: dict[int, dict[str, Any]] = {}
    bowl_map: dict[int, dict[str, Any]] = {}

    for snap in snapshots:
        if int(snap.get("batting_team_id") or 0) == int(user_id):
            for batter in snap.get("batters", []):
                try:
                    pid = int(batter.get("player_id") or 0)
                except (TypeError, ValueError):
                    pid = 0
                if pid:
                    bat_map[pid] = batter
        if int(snap.get("bowling_team_id") or 0) == int(user_id):
            for bowler in snap.get("bowlers", []):
                try:
                    pid = int(bowler.get("player_id") or 0)
                except (TypeError, ValueError):
                    pid = 0
                if pid:
                    bowl_map[pid] = bowler

    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for player in squad or []:
        try:
            pid = int(player.get("player_id") or 0)
        except (TypeError, ValueError):
            pid = 0
        if not pid or pid in seen:
            continue
        seen.add(pid)

        bat = bat_map.get(pid, {})
        bowl = bowl_map.get(pid, {})
        runs = int(bat.get("runs") or 0)
        bat_balls = int(bat.get("balls") or 0)
        dismissed = bool(bat.get("dismissed"))
        wickets = int(bowl.get("wickets") or 0)
        bowl_balls = int(bowl.get("balls") or 0)
        bowl_runs = int(bowl.get("runs") or 0)

        rows.append({
            "player_id": pid,
            "bat_matches": 1,
            "bat_innings": 1 if bat_balls > 0 else 0,
            "runs": runs,
            "fifties": 1 if 50 <= runs < 100 else 0,
            "centuries": 1 if runs >= 100 else 0,
            "bat_balls": bat_balls,
            "dismissals": 1 if dismissed else 0,
            "bowl_matches": 1,
            "bowl_innings": 1 if bowl_balls > 0 else 0,
            "wickets": wickets,
            "three_wickets": 1 if 3 <= wickets < 5 else 0,
            "five_wickets": 1 if wickets >= 5 else 0,
            "bowl_balls": bowl_balls,
            "bowl_runs": bowl_runs,
        })
    return rows


async def record_session_player_stats(session: Any) -> None:
    """Persist player performance for a live /play or /playint session.

    Safe to call at normal match completion and at an early /exitgame. The
    per-match UNIQUE key makes repeated calls idempotent.
    """
    match = getattr(session, "match", {}) or {}
    challenger_id = int(match.get("challenger_id") or 0)
    opponent_id = int(match.get("opponent_id") or 0)
    if not challenger_id or not opponent_id:
        return

    current = None
    try:
        # The current innings may be incomplete. Both engines expose the same
        # snapshot helper and keep prior completed innings in innings_history.
        from engines.play_runtime import snapshot_innings as play_snapshot
        current = play_snapshot(session)
    except Exception:
        try:
            from engines.playint_runtime import snapshot_innings as playint_snapshot
            current = playint_snapshot(session)
        except Exception:
            current = None

    history = list(getattr(session, "innings_history", []) or [])
    snapshots = history[:]
    if current:
        if not snapshots or snapshots[-1].get("innings_number") != current.get("innings_number"):
            snapshots.append(current)
        else:
            snapshots[-1] = current
    if not snapshots:
        return

    # At any point in a session these two fields represent the two participants'
    # current XIs, regardless of which side is currently batting.
    batting_team_id = int(getattr(session, "batting_team_id", 0) or 0)
    bowling_team_id = int(getattr(session, "bowling_team_id", 0) or 0)
    batting_squad = list(getattr(session, "batting_squad", []) or [])
    bowling_squad = list(getattr(session, "bowling_squad", []) or [])
    user_squads: dict[int, list[dict[str, Any]]] = {
        batting_team_id: batting_squad,
        bowling_team_id: bowling_squad,
    }

    for user_id in (challenger_id, opponent_id):
        squad = user_squads.get(user_id, [])
        rows = _aggregate_rows_for_user(user_id, squad, snapshots)
        if rows:
            await record_match_player_stats(int(session.match_id), user_id, rows)
