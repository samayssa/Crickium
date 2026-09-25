from __future__ import annotations

from collections import Counter
from typing import Any

from engines.innings_engine import BatterSlot
from engines.lineup_engine import bowling_candidates

MAX_BOWLER_OVERS = 4


def _key(team_id: int) -> str:
    return str(int(team_id))


def ensure_impact_state(session: Any, team_id: int) -> dict[str, Any]:
    state = session.impact_state.setdefault(
        _key(team_id),
        {
            "used": False,
            "stage": "idle",
            "out_id": None,
            "in_id": None,
            "context": None,
            "return_stage": None,
            "entry_role": None,
            "position": None,
        },
    )
    return state


def reset_impact_pending(session: Any, team_id: int, *, context: str, return_stage: str | None) -> dict[str, Any]:
    state = ensure_impact_state(session, team_id)
    state.update(
        {
            "stage": "out",
            "out_id": None,
            "in_id": None,
            "context": context,
            "return_stage": return_stage,
            "entry_role": None,
            "position": None,
        }
    )
    return state


def full_squad(session: Any, team_id: int) -> list[dict[str, Any]]:
    return [dict(p) for p in session.full_squads.get(int(team_id), [])]


def current_xi(session: Any, team_id: int) -> list[dict[str, Any]]:
    team_id = int(team_id)
    if team_id == int(session.batting_team_id):
        return [dict(p) for p in session.batting_squad]
    if team_id == int(session.bowling_team_id):
        return [dict(p) for p in session.bowling_squad]
    return []


def set_current_xi(session: Any, team_id: int, xi: list[dict[str, Any]]) -> None:
    team_id = int(team_id)
    xi = [dict(p) for p in xi]
    if team_id == int(session.batting_team_id):
        session.batting_squad = xi
        session.batting_xi = list(xi)
    elif team_id == int(session.bowling_team_id):
        session.bowling_squad = xi
        session.bowling_pool = bowling_candidates(xi)


def _batter_slot_ids(session: Any, team_id: int) -> set[int]:
    if int(team_id) != int(session.batting_team_id):
        return set()
    ids: set[int] = set()
    for slot in session.innings.batting_order:
        if slot.player_id is not None:
            ids.add(int(slot.player_id))
    return ids


def _dismissed_ids(session: Any, team_id: int) -> set[int]:
    if int(team_id) != int(session.batting_team_id):
        return set()
    return {
        int(slot.player_id)
        for slot in session.innings.batting_order
        if slot.player_id is not None and bool(slot.dismissed)
    }


def _active_batter_ids(session: Any, team_id: int) -> set[int]:
    if int(team_id) != int(session.batting_team_id):
        return set()
    return {
        int(slot.player_id)
        for slot in (session.innings.striker, session.innings.non_striker)
        if slot is not None and slot.player_id is not None
    }


def impact_out_candidates(session: Any, team_id: int) -> list[dict[str, Any]]:
    # The Impact Player UI intentionally exposes the complete current XI.
    return [dict(p) for p in current_xi(session, team_id)]


def impact_in_candidates(session: Any, team_id: int) -> list[dict[str, Any]]:
    active = {int(p.get("player_id") or 0) for p in current_xi(session, team_id)}
    return [
        dict(p)
        for p in full_squad(session, team_id)
        if int(p.get("player_id") or 0) not in active
    ]


def find_player(session: Any, team_id: int, player_id: int) -> dict[str, Any] | None:
    pid = int(player_id)
    for p in current_xi(session, team_id):
        if int(p.get("player_id") or 0) == pid:
            return dict(p)
    for p in full_squad(session, team_id):
        if int(p.get("player_id") or 0) == pid:
            return dict(p)
    return None


def _slot_from_player(player: dict[str, Any]) -> BatterSlot:
    return BatterSlot(
        player_id=int(player.get("player_id") or 0),
        name=str(player.get("name") or "Player"),
        role=player.get("role"),
        bat_level=player.get("bat_level"),
        bowl_level=player.get("bowl_level"),
    )


def apply_impact_replacement(session: Any, team_id: int, out_id: int, in_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    team_id = int(team_id)
    out_id = int(out_id)
    in_id = int(in_id)
    xi = current_xi(session, team_id)
    full = full_squad(session, team_id)
    out_player = next((dict(p) for p in xi if int(p.get("player_id") or 0) == out_id), None)
    in_player = next((dict(p) for p in full if int(p.get("player_id") or 0) == in_id), None)
    if out_player is None or in_player is None:
        raise ValueError("Impact Player replacement could not be resolved.")
    if any(int(p.get("player_id") or 0) == in_id for p in xi):
        raise ValueError("Selected Impact Player is already in the Playing XI.")
    if team_id == int(session.bowling_team_id) and session.current_bowler is not None:
        if int(session.current_bowler.get("player_id") or 0) == out_id:
            eligible_bowler_ids = {int(p.get("player_id") or 0) for p in bowling_candidates(xi)} | {in_id}
            if in_id not in eligible_bowler_ids:
                raise ValueError("The incoming Impact Player must be able to bowl when replacing the current bowler.")

    replaced = []
    for player in xi:
        if int(player.get("player_id") or 0) == out_id:
            replaced.append(dict(in_player))
        else:
            replaced.append(dict(player))
    set_current_xi(session, team_id, replaced)

    # If this team is currently batting, keep the batting-order slot so the
    # new Impact Player can be repositioned through the dedicated batting
    # position step. If the removed player is currently at the crease, the
    # active slot itself is replaced immediately, then the position selector
    # may move that player later.
    if team_id == int(session.batting_team_id):
        order = session.innings.batting_order
        for idx, slot in enumerate(order):
            if int(slot.player_id or 0) != out_id:
                continue
            replacement = _slot_from_player(in_player)
            order[idx] = replacement
            if session.innings.striker is slot:
                session.innings.striker = replacement
            if session.innings.non_striker is slot:
                session.innings.non_striker = replacement
            break

    # Keep the auto plans valid after an Impact replacement. A removed player
    # must never remain as a stale future bowler/batter reference. Replace it
    # with the incoming card where possible.
    if hasattr(session, "auto_bowler_queue"):
        session.auto_bowler_queue[:] = [
            in_id if int(pid) == out_id else int(pid)
            for pid in session.auto_bowler_queue
        ]
    if getattr(session, "pending_next_bowler_id", None) is not None and int(session.pending_next_bowler_id) == out_id:
        session.pending_next_bowler_id = in_id
    if hasattr(session, "auto_batsman_queue"):
        session.auto_batsman_queue[:] = [
            in_id if int(pid) == out_id else int(pid)
            for pid in session.auto_batsman_queue
        ]
    if hasattr(session, "pending_batsman_order"):
        session.pending_batsman_order[:] = [
            in_id if int(pid) == out_id else int(pid)
            for pid in session.pending_batsman_order
        ]

    # If the removed player is the current bowler, replace the current bowler
    # immediately and force a fresh tactic choice for the incoming player.
    if team_id == int(session.bowling_team_id) and session.current_bowler is not None:
        if int(session.current_bowler.get("player_id") or 0) == out_id:
            session.current_bowler = dict(in_player)
            session.selected_bowler_id = in_id
            session.current_tactic = None
            session.stage = "choose_tactic"

    # Replacing a queued/curr-bowler card with a card that cannot bowl would
    # otherwise create a dead auto-bowler plan. Keep the incoming card only if
    # it is a legitimate bowling candidate; otherwise clear that planned slot.
    if team_id == int(session.bowling_team_id):
        eligible_ids = {int(p.get("player_id") or 0) for p in bowling_candidates(session.bowling_squad)}
        if hasattr(session, "auto_bowler_queue"):
            session.auto_bowler_queue[:] = [pid for pid in session.auto_bowler_queue if pid in eligible_ids]
        if getattr(session, "pending_next_bowler_id", None) is not None and int(session.pending_next_bowler_id) not in eligible_ids:
            session.pending_next_bowler_id = None

    return out_player, in_player


def future_batting_candidates(session: Any, team_id: int | None = None) -> list[dict[str, Any]]:
    # When a team is not currently batting, its whole current XI is still a
    # future batting order. This is used by the runtime Impact Player flow so
    # the bowling-side user can place the incoming player for innings two.
    if team_id is not None and int(team_id) != int(session.batting_team_id):
        xi = current_xi(session, int(team_id))
        return [
            {
                **dict(player),
                "position": index + 1,
            }
            for index, player in enumerate(xi)
        ]

    order = session.innings.batting_order
    start = int(session.innings.next_batter_index or 0)
    active = _active_batter_ids(session, session.batting_team_id)
    dismissed = _dismissed_ids(session, session.batting_team_id)
    result: list[dict[str, Any]] = []
    for index in range(start, len(order)):
        slot = order[index]
        pid = int(slot.player_id or 0)
        if not pid or pid in active or pid in dismissed:
            continue
        result.append(
            {
                "player_id": pid,
                "name": slot.name,
                "position": index + 1,
                "bat_level": int(slot.bat_level or 0),
                "bowl_level": int(slot.bowl_level or 0),
                "role": slot.role,
            }
        )

    # During Impact Player batting-position selection, the incoming card may
    # have replaced a player whose original batting slot is already before the
    # normal next-batter cursor. Keep that card visible once so the user can
    # explicitly reposition it instead of getting an empty/partial selector.
    batting_key = _key(session.batting_team_id)
    for state_key, state in getattr(session, "impact_state", {}).items():
        if str(state_key) != batting_key:
            continue
        if state.get("stage") != "batpos" or state.get("in_id") is None:
            continue
        in_id = int(state.get("in_id"))
        if any(int(item.get("player_id") or 0) == in_id for item in result):
            continue
        for index, slot in enumerate(order):
            if int(slot.player_id or 0) != in_id:
                continue
            if in_id in dismissed:
                continue
            if index >= len(order):
                break
            result.append(
                {
                    "player_id": in_id,
                    "name": slot.name,
                    "position": index + 1,
                    "bat_level": int(slot.bat_level or 0),
                    "bowl_level": int(slot.bowl_level or 0),
                    "role": slot.role,
                }
            )
            break
    result.sort(key=lambda item: int(item.get("position") or 0))
    return result


def selected_batting_order(session: Any) -> list[int]:
    return list(session.pending_batsman_order)


def move_future_batting_player_to_position(session: Any, team_id: int, player_id: int, target_position: int) -> None:
    """Move an Impact Player inside a team's future XI batting order.

    This path is used only when the team is currently bowling, so the team
    has no live innings batting_order yet. The reordered XI becomes the order
    used when the second innings is created.
    """
    team_id = int(team_id)
    player_id = int(player_id)
    target_position = int(target_position)
    xi = current_xi(session, team_id)
    if not xi:
        raise ValueError("The team's current Playing XI is unavailable.")
    source_index = next(
        (idx for idx, player in enumerate(xi) if int(player.get("player_id") or 0) == player_id),
        None,
    )
    if source_index is None:
        raise ValueError("Impact Player is not available in the future batting XI.")
    target_index = min(max(0, target_position - 1), len(xi) - 1)
    player = xi.pop(source_index)
    xi.insert(target_index, player)
    set_current_xi(session, team_id, xi)


def confirm_batting_order(session: Any) -> list[int]:
    selected = list(session.pending_batsman_order)
    if not selected:
        raise ValueError("Select at least one next batter first.")

    order = session.innings.batting_order
    start = int(session.innings.next_batter_index or 0)
    slots = order[start:]
    by_id = {int(slot.player_id or 0): slot for slot in slots if slot.player_id is not None}
    if any(int(pid) not in by_id for pid in selected):
        raise ValueError("One or more selected batters are no longer available.")
    # Preserve the exact click/selection order. The order in `pending_batsman_order`
    # is the user's intended replacement queue, not the roster's original order.
    selected_slots = [by_id[int(pid)] for pid in selected]
    selected_set = {int(pid) for pid in selected}
    remaining_slots = [slot for slot in slots if int(slot.player_id or 0) not in selected_set]
    order[start:] = selected_slots + remaining_slots
    session.auto_batsman_queue = [int(pid) for pid in selected]
    session.pending_batsman_order.clear()
    session.auto_batsman_enabled = True
    return list(session.auto_batsman_queue)


def consume_planned_batsman_after_wicket(session: Any) -> None:
    if not session.auto_batsman_enabled:
        return
    if session.auto_batsman_queue:
        session.auto_batsman_queue.pop(0)
    if not session.auto_batsman_queue:
        session.auto_batsman_enabled = False


def _scheduled_bowler_counts(session: Any) -> Counter:
    return Counter(int(pid) for pid in session.auto_bowler_queue)


def scheduled_bowler_candidates(session: Any) -> list[dict[str, Any]]:
    candidates = bowling_candidates(session.bowling_squad)
    reserved = _scheduled_bowler_counts(session)
    last_scheduled = int(session.auto_bowler_queue[-1]) if session.auto_bowler_queue else None
    last_current = int(session.selected_bowler_id or 0) if session.selected_bowler_id is not None else None
    previous = last_scheduled or last_current
    result = []
    current_pid = int(session.current_bowler.get("player_id") or 0) if session.current_bowler else None
    for player in candidates:
        pid = int(player.get("player_id") or 0)
        stats = session.bowler_stats.get(pid, {})
        balls = int(stats.get("balls") or 0)
        completed_overs = balls // 6
        actual_left = MAX_BOWLER_OVERS - completed_overs

        # A newly selected/current bowler has already committed one over even
        # before the first legal ball is delivered. This makes the displayed
        # "Left" quota reflect the confirmed plan immediately. Once six balls
        # of that over are completed, the completed-over count takes over and
        # the extra commitment is no longer subtracted.
        current_commitment = 0
        if current_pid is not None and pid == current_pid:
            if balls % 6 != 0 or str(getattr(session, "stage", "")) == "choose_tactic":
                current_commitment = 1

        committed_future = int(reserved.get(pid, 0))
        displayed_left = actual_left - current_commitment - committed_future
        if displayed_left <= 0:
            continue
        if previous is not None and pid == previous:
            continue
        item = dict(player)
        item["_overs_left"] = max(0, displayed_left)
        result.append(item)
    return result


def scheduled_bowler_targets(session: Any) -> list[tuple[int, dict[str, Any]]]:
    """Return planned future overs while auto-bowler is being configured."""
    queue = list(session.auto_bowler_queue)
    if not queue:
        return []
    by_id = {int(p.get("player_id") or 0): dict(p) for p in bowling_candidates(session.bowling_squad)}
    current_over = current_over_number(session)
    start_over = current_over if session.current_bowler is None else current_over + 1
    return [
        (start_over + index, by_id[pid])
        for index, pid in enumerate(queue)
        if pid in by_id
    ]


def confirm_next_bowler(session: Any) -> int:
    """Confirm the selected planned bowler.

    If the current over has not received a bowler yet, the first planned
    selection is assigned to the current over immediately. Otherwise it is
    queued for the next available future over.
    """
    pid = int(session.pending_next_bowler_id or 0)
    if pid <= 0:
        raise ValueError("Select a bowler first.")
    valid = {int(p.get("player_id") or 0): p for p in scheduled_bowler_candidates(session)}
    if pid not in valid:
        raise ValueError("That bowler is no longer available for the scheduled over.")
    player = dict(valid[pid])
    session.pending_next_bowler_id = None
    if session.current_bowler is None:
        session.current_bowler = player
        session.selected_bowler_id = pid
        slot = session.bowler_stats.setdefault(
            pid, {"balls": 0, "runs": 0, "wickets": 0, "name": player.get("name", "Bowler")}
        )
        slot.setdefault("name", player.get("name", "Bowler"))
        session.current_tactic = None
        session.stage = "choose_tactic"
        return pid
    session.auto_bowler_queue.append(pid)
    return pid


def clear_bowler_schedule_selection(session: Any) -> None:
    session.pending_next_bowler_id = None


def scheduled_bowler_player(session: Any, player_id: int) -> dict[str, Any] | None:
    pid = int(player_id)
    for p in bowling_candidates(session.bowling_squad):
        if int(p.get("player_id") or 0) == pid:
            return dict(p)
    return None


def consume_next_scheduled_bowler(session: Any) -> dict[str, Any] | None:
    if not session.auto_bowler_queue:
        return None
    pid = int(session.auto_bowler_queue[0])
    player = scheduled_bowler_player(session, pid)
    session.auto_bowler_queue.pop(0)
    if player is None:
        return None
    return player


def current_over_number(session: Any) -> int:
    return int(session.innings.score.overs or 0) + 1


def ordinal(number: int) -> str:
    n = int(number)
    if 10 < n % 100 < 14:
        return f"{n}th"
    return f"{n}{ {1:'st',2:'nd',3:'rd'}.get(n % 10, 'th') }"


def move_batting_player_to_position(session: Any, player_id: int, target_position: int) -> None:
    player_id = int(player_id)
    target_position = int(target_position)
    order = session.innings.batting_order
    start = int(session.innings.next_batter_index or 0)
    if target_position <= start:
        target_position = start + 1
    source_index = next(
        (idx for idx in range(start, len(order)) if int(order[idx].player_id or 0) == player_id),
        None,
    )
    if source_index is None:
        raise ValueError("Impact Player is not available in the remaining batting order.")
    slot = order.pop(source_index)
    insert_index = min(max(start, target_position - 1), len(order))
    order.insert(insert_index, slot)
    session.impact_state[str(int(session.batting_team_id))]["position"] = target_position


def apply_entry_role_after_wicket(session: Any) -> None:
    """Apply a saved striker/non-striker preference when a planned batter enters."""
    for state in session.impact_state.values():
        in_id = state.get("in_id")
        role = state.get("entry_role")
        if not in_id or role not in {"striker", "non_striker"}:
            continue
        in_id = int(in_id)
        striker = session.innings.striker
        non = session.innings.non_striker
        if striker is not None and int(striker.player_id or 0) == in_id:
            if role == "non_striker" and non is not None:
                session.innings.striker, session.innings.non_striker = non, striker
            state["entry_role"] = None
            return
        if non is not None and int(non.player_id or 0) == in_id:
            if role == "striker" and striker is not None:
                session.innings.striker, session.innings.non_striker = non, striker
            state["entry_role"] = None
            return
