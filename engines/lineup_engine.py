"""Lineup helpers for opening batters, bowler selection, and team reports."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from database.squads_repo import get_team_squad
from engines.match_engine import get_match_session
from utils.constants import ROLE_ALLROUNDER, ROLE_BATSMAN, ROLE_BOWLER, ROLE_WICKETKEEPER
from utils.mentions import mention


@dataclass(slots=True)
class LineupStatus:
    batting_team_id: int | None = None
    bowling_team_id: int | None = None
    striker: dict[str, Any] | None = None
    non_striker: dict[str, Any] | None = None
    bowler: dict[str, Any] | None = None
    batting_order: list[dict[str, Any]] | None = None
    bowling_order: list[dict[str, Any]] | None = None


def split_squad(squad: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    batsmen = [p for p in squad if p.get("role") in {ROLE_BATSMAN, ROLE_WICKETKEEPER}]
    bowlers = [p for p in squad if p.get("role") == ROLE_BOWLER]
    allrounders = [p for p in squad if p.get("role") == ROLE_ALLROUNDER]
    return {"batsmen": batsmen, "bowlers": bowlers, "allrounders": allrounders, "all": list(squad)}


async def load_squad(user_id: int) -> list[dict[str, Any]] | None:
    return await get_team_squad(user_id)


def default_lineup_ids(squad: list[dict[str, Any]]) -> list[int]:
    return [int(p.get("player_id") or 0) for p in squad[:11]]


async def load_current_xi(user_id: int) -> list[dict[str, Any]] | None:
    from database.lineups_repo import get_lineup_ids, save_lineup_ids

    squad = await get_team_squad(user_id)
    if not squad:
        return None

    lineup_ids = await get_lineup_ids(user_id)
    if not lineup_ids:
        lineup_ids = default_lineup_ids(squad)
        await save_lineup_ids(user_id, lineup_ids)

    xi = []
    for pid in lineup_ids:
        player = find_player_by_id(squad, pid)
        if player is not None:
            xi.append(player)

    if len(xi) < 11:
        existing_ids = {int(p.get("player_id") or 0) for p in xi}
        for player in squad:
            if len(xi) >= 11:
                break
            pid = int(player.get("player_id") or 0)
            if pid not in existing_ids:
                xi.append(player)
                existing_ids.add(pid)

    return xi


async def load_match_session(chat_id: int):
    return get_match_session(chat_id)


def find_player_by_id(squad: list[dict[str, Any]], player_id: int) -> dict[str, Any] | None:
    for player in squad:
        if int(player.get("player_id") or 0) == int(player_id):
            return player
    return None


def reorder_openers(squad: list[dict[str, Any]], striker_id: int, non_striker_id: int) -> list[dict[str, Any]]:
    striker = find_player_by_id(squad, striker_id)
    non_striker = find_player_by_id(squad, non_striker_id)
    if striker is None or non_striker is None:
        raise ValueError("Opening batters must come from the current squad.")
    if striker_id == non_striker_id:
        raise ValueError("Striker and non-striker must be different players.")

    ordered = [striker, non_striker]
    ordered.extend(player for player in squad if int(player.get("player_id") or 0) not in {striker_id, non_striker_id})
    return ordered


def bowling_candidates(squad: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [p for p in squad if p.get("role") in {ROLE_BOWLER, ROLE_ALLROUNDER}]
    return candidates or list(squad)


def format_player_block(player: dict[str, Any], marker: str = "") -> str:
    from utils.country_flags import flag_for

    name = str(player.get("name", "Player"))
    role = str(player.get("role", ""))
    bat = int(player.get("bat_level") or 0)
    bowl = int(player.get("bowl_level") or 0)
    flag = flag_for(player.get("country"))

    if role == ROLE_BATSMAN:
        stats = f"Bat {bat}"
    elif role == ROLE_BOWLER:
        stats = f"Bowl {bowl}"
    else:
        stats = f"Bat {bat} / Bowl {bowl}"

    lead = f"{marker} " if marker else ""
    return f"{lead}{name} {flag} — {stats}"


def format_team_lines(players: Iterable[dict[str, Any]], selected_ids: set[int] | None = None, index_map: dict[int, int] | None = None) -> list[str]:
    selected_ids = selected_ids or set()
    index_map = index_map or {}
    lines = []
    for player in players:
        player_id = int(player.get("player_id") or 0)
        marker = "👑" if player_id in selected_ids else "•"
        number = index_map.get(player_id)
        prefix = f"{number}. " if number is not None else ""
        lines.append(f"{prefix}{format_player_block(player, marker=marker)}")
    return lines


def render_opening_selection_message(
    batting_display: str,
    bowling_display: str,
    squad: list[dict[str, Any]],
    selected_striker_id: int | None = None,
    selected_non_striker_id: int | None = None,
) -> str:
    status = [
        "*🏏 OPENING BATSMEN SELECTION*",
        "",
        f"*👤 Batting Side:* {batting_display}",
        f"*🎯 Bowling Side:* {bowling_display}",
        "",
        "*Choose your opening batsman first.*",
        "*Then choose your non-striker.*",
    ]
    if selected_striker_id is not None:
        striker = find_player_by_id(squad, selected_striker_id)
        if striker:
            status.append(f"*Striker:* {striker['name']}")
    if selected_non_striker_id is not None:
        non = find_player_by_id(squad, selected_non_striker_id)
        if non:
            status.append(f"*Non-striker:* {non['name']}")
    status.append("")
    status.append("Pick from the full squad below.")
    return "\n".join(status)


def render_bowler_selection_message(
    bowling_display: str,
    batting_display: str,
    selected_striker: dict[str, Any] | None,
    selected_non_striker: dict[str, Any] | None,
) -> str:
    lines = [
        "*🎯 BOWLER SELECTION*",
        "",
        f"*Bowling Side:* {bowling_display}",
        f"*Batting Side:* {batting_display}",
        "",
        "*Choose your bowler for ball one.*",
    ]
    if selected_striker:
        lines.append(f"*Striker:* {selected_striker.get('name')}")
    if selected_non_striker:
        lines.append(f"*Non-striker:* {selected_non_striker.get('name')}")
    return "\n".join(lines)


def render_ready_message(
    batting_display: str,
    bowling_display: str,
    striker: dict[str, Any] | None,
    non_striker: dict[str, Any] | None,
    bowler: dict[str, Any] | None,
) -> str:
    return "\n".join([
        "*🏟 LINEUP LOCKED*",
        "",
        f"*Batting Side:* {batting_display}",
        f"*Bowling Side:* {bowling_display}",
        "",
        f"*Striker:* {striker.get('name') if striker else 'Player'}",
        f"*Non-striker:* {non_striker.get('name') if non_striker else 'Player'}",
        f"*Bowler:* {bowler.get('name') if bowler else 'Bowler'}",
        "",
        "*The next ball can begin now.*",
    ])


def render_team_report(
    squad: list[dict[str, Any]],
    title: str,
    session: Any | None = None,
) -> str:
    grouped = split_squad(squad)
    index_map = {int(p.get("player_id") or 0): i for i, p in enumerate(squad, start=1)}
    selected_ids = set()
    if session is not None:
        for p in (getattr(session, 'current_batsman', None), getattr(session, 'current_bowler', None)):
            if isinstance(p, dict) and p.get('player_id') is not None:
                selected_ids.add(int(p['player_id']))
        innings = getattr(session, 'innings', None)
        if innings and getattr(innings, 'striker', None):
            for player in grouped['all']:
                if player.get('name') == innings.striker.name:
                    selected_ids.add(int(player.get('player_id') or 0))
        if innings and getattr(innings, 'non_striker', None):
            for player in grouped['all']:
                if player.get('name') == innings.non_striker.name:
                    selected_ids.add(int(player.get('player_id') or 0))

    lines = [f"*{title}*", ""]
    lines.append("*Batsmen*")
    lines.extend(format_team_lines(grouped['batsmen'], selected_ids=selected_ids, index_map=index_map) or ["• None"])
    lines.append("")
    lines.append("*Bowlers*")
    lines.extend(format_team_lines(grouped['bowlers'], selected_ids=selected_ids, index_map=index_map) or ["• None"])
    lines.append("")
    lines.append("*All-Rounders*")
    lines.extend(format_team_lines(grouped['allrounders'], selected_ids=selected_ids, index_map=index_map) or ["• None"])
    lines.append("")
    lines.append("_Numbers above = squad position for /changeXL._")
    return "\n".join(lines)


def render_pxl_report(xi: list[dict[str, Any]], session: Any | None = None) -> str:
    title = "*📋 CURRENT PLAYING XI*"
    selected_ids = set()
    innings = getattr(session, "innings", None) if session is not None else None
    if innings is not None:
        if innings.striker:
            for player in xi:
                if player.get("name") == innings.striker.name:
                    selected_ids.add(int(player.get("player_id") or 0))
        if innings.non_striker:
            for player in xi:
                if player.get("name") == innings.non_striker.name:
                    selected_ids.add(int(player.get("player_id") or 0))
    current_bowler = getattr(session, "current_bowler", None) if session is not None else None
    if isinstance(current_bowler, dict) and current_bowler.get("player_id") is not None:
        selected_ids.add(int(current_bowler["player_id"]))

    lines = [title, ""]
    for index, player in enumerate(xi, start=1):
        pid = int(player.get("player_id") or 0)
        marker = "👑" if pid in selected_ids else "•"
        lines.append(f"{index}. {format_player_block(player, marker=marker)}")

    if innings is not None:
        lines.extend([
            "",
            f"*Stage:* {getattr(session, 'stage', 'idle')}",
            f"*Score:* {innings.score.runs}/{innings.score.wickets} in {innings.score.over_text}",
            f"*Striker:* {innings.striker.name if innings.striker else 'Player'}",
            f"*Non-striker:* {innings.non_striker.name if innings.non_striker else 'Player'}",
            f"*Bowler:* {current_bowler.get('name') if isinstance(current_bowler, dict) else 'Bowler'}",
        ])

    lines.append("")
    lines.append("_Use /changeXL <slot> <squad no.> to update your XI._")
    return "\n".join(lines)
