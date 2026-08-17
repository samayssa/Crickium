"""Innings engine for the cricket match flow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engines.score_engine import (
    ScoreCard,
    ball_summary,
    create_score_state,
    innings_finished,
    should_rotate_strike,
    update_score_from_ball,
)

@dataclass(slots=True)
class BatterSlot:
    player_id: int | None = None
    name: str = "Player"
    role: str | None = None
    bat_level: int | None = None
    bowl_level: int | None = None
    runs: int = 0
    balls: int = 0
    dismissed: bool = False
    dismissal_text: str | None = None
    confidence: float = 0.0
    boundary_streak: int = 0

@dataclass(slots=True)
class InningsState:
    innings_number: int = 1
    batting_team_id: int | None = None
    bowling_team_id: int | None = None
    target: int | None = None
    score: ScoreCard = field(default_factory=create_score_state)
    striker: BatterSlot | None = None
    non_striker: BatterSlot | None = None
    next_batter_index: int = 0
    batting_order: list[BatterSlot] = field(default_factory=list)
    completed: bool = False
    winner_team_id: int | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "innings_number": self.innings_number,
            "batting_team_id": self.batting_team_id,
            "bowling_team_id": self.bowling_team_id,
            "target": self.target,
            "score": {
                "runs": self.score.runs,
                "wickets": self.score.wickets,
                "legal_balls": self.score.legal_balls,
                "overs": self.score.overs,
                "balls": self.score.balls,
            },
            "striker": {
                "player_id": self.striker.player_id if self.striker else None,
                "name": self.striker.name if self.striker else None,
                "runs": self.striker.runs if self.striker else None,
                "balls": self.striker.balls if self.striker else None,
            } if self.striker else None,
            "non_striker": {
                "player_id": self.non_striker.player_id if self.non_striker else None,
                "name": self.non_striker.name if self.non_striker else None,
                "runs": self.non_striker.runs if self.non_striker else None,
                "balls": self.non_striker.balls if self.non_striker else None,
            } if self.non_striker else None,
            "next_batter_index": self.next_batter_index,
            "completed": self.completed,
            "winner_team_id": self.winner_team_id,
        }

def create_innings(
    batting_team_id: int | None = None,
    bowling_team_id: int | None = None,
    batting_order: list[dict[str, Any]] | list[BatterSlot] | None = None,
    target: int | None = None,
    innings_number: int = 1,
) -> InningsState:
    order: list[BatterSlot] = []
    for item in batting_order or []:
        if isinstance(item, BatterSlot):
            order.append(item)
        else:
            order.append(
                BatterSlot(
                    player_id=item.get("player_id"),
                    name=item.get("name", "Player"),
                    role=item.get("role"),
                    bat_level=item.get("bat_level"),
                    bowl_level=item.get("bowl_level"),
                )
            )

    state = InningsState(
        innings_number=innings_number,
        batting_team_id=batting_team_id,
        bowling_team_id=bowling_team_id,
        target=target,
        batting_order=order,
    )
    if len(order) >= 2:
        state.striker = order[0]
        state.non_striker = order[1]
        state.next_batter_index = 2
    return state

def current_score_text(state: InningsState) -> str:
    return ball_summary(state.score)

def _get_next_batter(state: InningsState) -> BatterSlot | None:
    if state.next_batter_index >= len(state.batting_order):
        return None
    batter = state.batting_order[state.next_batter_index]
    state.next_batter_index += 1
    return batter

def on_single_or_three(state: InningsState, runs: int) -> None:
    if runs % 2 == 1:
        state.striker, state.non_striker = state.non_striker, state.striker

def on_over_end(state: InningsState) -> None:
    state.striker, state.non_striker = state.non_striker, state.striker

def register_ball(
    state: InningsState,
    outcome: str,
    runs: int = 0,
    wicket: bool = False,
    batter_name: str | None = None,
    wicket_type: str = "caught",
    fielder_name: str | None = None,
    extra_type: str | None = None,
    legal_delivery: bool = True,
    max_legal_balls: int = 120,
    max_wickets: int = 10,
) -> dict[str, Any]:
    before_balls = state.score.legal_balls
    before_over = state.score.over_text
    striker_before = state.striker

    update_score_from_ball(
        state.score,
        outcome=outcome,
        runs=runs,
        wicket=wicket,
        batter_name=batter_name or (state.striker.name if state.striker else None),
        wicket_type=wicket_type,
        fielder_name=fielder_name,
        extra_type=extra_type,
        legal_delivery=legal_delivery,
    )

    normalized = str(outcome or "").strip().lower()
    if striker_before is not None:
        if legal_delivery and normalized not in {"wide", "no_ball"}:
            striker_before.balls += 1
        if normalized in {"single", "double", "triple", "four", "six"}:
            striker_before.runs += int(runs or 0)
        elif normalized == "bye" or normalized == "leg_bye":
            striker_before.runs += 0
        if wicket:
            striker_before.dismissed = True
            striker_before.dismissal_text = wicket_type

    if normalized not in {"wide", "no_ball"} and should_rotate_strike(runs):
        state.striker, state.non_striker = state.non_striker, state.striker

    if state.score.legal_balls > before_balls and state.score.legal_balls % 6 == 0:
        on_over_end(state)

    if wicket and state.striker is not None:
        replacement = _get_next_batter(state)
        state.striker = replacement

    finished = innings_finished(
        state.score,
        max_wickets=max_wickets,
        max_legal_balls=max_legal_balls,
        target=state.target,
    )

    if finished:
        state.completed = True
        if state.target is not None and state.score.runs >= state.target:
            state.winner_team_id = state.batting_team_id

    return {
        "before_over": before_over,
        "after_over": state.score.over_text,
        "summary": current_score_text(state),
        "completed": state.completed,
        "winner_team_id": state.winner_team_id,
        "striker": state.striker.name if state.striker else None,
        "non_striker": state.non_striker.name if state.non_striker else None,
    }

def set_target(state: InningsState, target: int) -> None:
    state.target = target

def next_innings(state: InningsState, new_batting_team_id: int | None = None, new_bowling_team_id: int | None = None) -> InningsState:
    state.innings_number += 1
    state.batting_team_id = new_batting_team_id if new_batting_team_id is not None else state.batting_team_id
    state.bowling_team_id = new_bowling_team_id if new_bowling_team_id is not None else state.bowling_team_id
    state.score = create_score_state()
    state.target = None
    state.completed = False
    state.winner_team_id = None
    state.next_batter_index = 0
    if len(state.batting_order) >= 2:
        state.striker = state.batting_order[0]
        state.non_striker = state.batting_order[1]
        state.next_batter_index = 2
    else:
        state.striker = None
        state.non_striker = None
    return state
