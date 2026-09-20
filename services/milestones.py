"""Cross-engine cricket milestone notifications.

This module is intentionally independent from the existing scoring/simulation
logic.  The three live engines call ``schedule_milestone_notifications`` after
a ball has been applied; the helper only reads the already-updated runtime
state and sends an informational alert.  A notification failure must never
stop a match.
"""
from __future__ import annotations

import asyncio
import html
from collections import defaultdict
from typing import Any

from app import app
from utils.PremiumEmoji import (
    MILESTONE_FALLBACK_EMOJIS,
    MILESTONE_LINE_EMOJIS,
    MILESTONE_MEDIA,
    milestone_emoji_html,
)
from utils.mentions import mention_name_only_html

ENGINE_LABELS = {
    "PLAY": "PLAY",
    "PLAYIPL": "IPL",
    "PLAYINT": "INTERNATIONAL",
}

_EVENT_LABELS = {
    "BAT_50": ("Half-Century", "50 runs"),
    "BAT_100": ("Century", "100 runs"),
    "BOWL_3": ("3-Wicket Haul", "3 wickets"),
    "BOWL_5": ("Five-Wicket Haul", "5 wickets"),
    "HAT_TRICK": ("Hat-Trick", "3 wickets in consecutive legal deliveries"),
    "PARTNERSHIP": ("Partnership Milestone", ""),
}

# Per-match notification state. Keys are match IDs and values are the event
# keys already delivered. This prevents a duplicate alert if a callback/retry
# invokes the post-ball hook more than once.
_EMITTED: dict[int, set[str]] = defaultdict(set)
_HAT_TRICK_STATE: dict[int, tuple[int, int]] = {}


def clear_milestone_state(match_id: int) -> None:
    _EMITTED.pop(int(match_id), None)
    _HAT_TRICK_STATE.pop(int(match_id), None)
    if len(_EMITTED) > 4096:
        # Safety valve for long-running bots if a completion cleanup hook is
        # ever skipped by an abnormal process termination.
        for key in list(_EMITTED)[:1024]:
            _EMITTED.pop(key, None)
            _HAT_TRICK_STATE.pop(key, None)


def _emit_once(match_id: int, key: str) -> bool:
    seen = _EMITTED[int(match_id)]
    if key in seen:
        return False
    seen.add(key)
    return True


def _team_user(match: dict[str, Any], team_id: int) -> tuple[int, str | None, str | None]:
    challenger_id = int(match.get("challenger_id") or 0)
    if int(team_id) == challenger_id:
        return (
            challenger_id,
            match.get("challenger_username"),
            match.get("challenger_name"),
        )
    return (
        int(match.get("opponent_id") or 0),
        match.get("opponent_username"),
        match.get("opponent_name"),
    )


def _owner_mention(match: dict[str, Any], team_id: int) -> str:
    uid, username, name = _team_user(match, team_id)
    return mention_name_only_html(uid, name or username or "Player")


def _engine_key_for_session(session: Any) -> str:
    explicit = str((getattr(session, "match", {}) or {}).get("engine_key") or "").upper()
    if explicit:
        return explicit
    name = type(session).__name__.upper()
    if "PLAYINT" in name:
        return "PLAYINT"
    if "PLAYIPL" in name:
        return "PLAYIPL"
    return "PLAY"


def _engine_label(engine_key: str) -> str:
    return ENGINE_LABELS.get(str(engine_key or "").upper(), str(engine_key or "GAME"))


def _player_id(player: Any) -> int:
    try:
        return int(getattr(player, "player_id", None) or player.get("player_id") or 0)
    except Exception:
        return 0


def _player_name(player: Any) -> str:
    return str(getattr(player, "name", None) or player.get("name") or "Player")


def _collect_events(session: Any, outcome: Any, striker_before: Any, partnership_id: int | None = None, partnership_players: tuple[str, str] | None = None) -> list[dict[str, Any]]:
    match_id = int(session.match_id)
    innings = int(getattr(session.innings, "innings_number", 1) or 1)
    score = session.innings.score
    events: list[dict[str, Any]] = []

    # Batter milestones: 50 and 100 only, each once per innings/player.
    pid = _player_id(striker_before)
    pname = _player_name(striker_before)
    runs = int(getattr(striker_before, "runs", 0) or 0)
    balls = int(getattr(striker_before, "balls", 0) or 0)
    if pid:
        for mark, event_type in ((50, "BAT_50"), (100, "BAT_100")):
            if runs >= mark and _emit_once(match_id, f"i{innings}:bat:{pid}:{mark}"):
                events.append(
                    {
                        "type": event_type,
                        "player_id": pid,
                        "player_name": pname,
                        "runs": runs,
                        "balls": balls,
                        "team_id": int(session.batting_team_id),
                        "detail": f"{runs} runs from {balls} balls",
                    }
                )

    # Current partnership milestone. ``score.wickets`` identifies the current
    # partnership naturally, because a wicket starts the next partnership.
    partnership_runs = int(getattr(session, "partnership_runs", 0) or 0)
    partnership_id = int(partnership_id or (int(getattr(score, "wickets", 0) or 0) + 1))
    if partnership_runs >= 50:
        max_mark = (partnership_runs // 50) * 50
        for mark in range(50, max_mark + 1, 50):
            key = f"i{innings}:p{partnership_id}:{mark}"
            if _emit_once(match_id, key):
                striker = getattr(session.innings, "striker", None)
                non = getattr(session.innings, "non_striker", None)
                pair_name = (
                    f"{partnership_players[0]} & {partnership_players[1]}"
                    if partnership_players
                    else f"{_player_name(striker)} & {_player_name(non)}"
                )
                events.append(
                    {
                        "type": "PARTNERSHIP",
                        "player_id": 0,
                        "player_name": pair_name,
                        "runs": partnership_runs,
                        "balls": int(getattr(session, "partnership_balls", 0) or 0),
                        "team_id": int(session.batting_team_id),
                        "detail": f"{mark}-run partnership",
                    }
                )

    # Bowler milestones and hat-trick state.
    bowler = getattr(session, "current_bowler", None) or {}
    bowler_id = _player_id(bowler)
    bowler_name = str(bowler.get("name") or "Bowler")
    if bowler_id:
        bowler_stats = getattr(session, "bowler_stats", {}).get(bowler_id, {})
        wickets = int(bowler_stats.get("wickets") or 0)
        if wickets >= 3 and _emit_once(match_id, f"i{innings}:bowl:{bowler_id}:3"):
            events.append(
                {
                    "type": "BOWL_3",
                    "player_id": bowler_id,
                    "player_name": bowler_name,
                    "runs": wickets,
                    "balls": 0,
                    "team_id": int(session.bowling_team_id),
                    "detail": f"{wickets} wickets",
                }
            )
        if wickets >= 5 and _emit_once(match_id, f"i{innings}:bowl:{bowler_id}:5"):
            events.append(
                {
                    "type": "BOWL_5",
                    "player_id": bowler_id,
                    "player_name": bowler_name,
                    "runs": wickets,
                    "balls": 0,
                    "team_id": int(session.bowling_team_id),
                    "detail": f"{wickets} wickets",
                }
            )

        # Hat-trick uses consecutive *legal* balls by the same bowler. Illegal
        # balls do not reset the streak; a legal non-wicket does.
        previous_bowler, previous_streak = _HAT_TRICK_STATE.get(match_id, (bowler_id, 0))
        if previous_bowler != bowler_id:
            previous_streak = 0
        if bool(getattr(outcome, "legal", False)):
            if bool(getattr(outcome, "wicket", False)):
                previous_streak += 1
            else:
                previous_streak = 0
        _HAT_TRICK_STATE[match_id] = (bowler_id, previous_streak)
        if previous_streak >= 3:
            delivery_no = int(getattr(score, "legal_balls", 0) or 0)
            if _emit_once(match_id, f"i{innings}:hat:{bowler_id}:{delivery_no}"):
                events.append(
                    {
                        "type": "HAT_TRICK",
                        "player_id": bowler_id,
                        "player_name": bowler_name,
                        "runs": wickets,
                        "balls": 0,
                        "team_id": int(session.bowling_team_id),
                        "detail": "Three wickets in consecutive legal deliveries",
                    }
                )

    return events


def _build_event_text(session: Any, event: dict[str, Any]) -> str:
    event_type = event["type"]
    title, descriptor = _EVENT_LABELS.get(event_type, ("Milestone", ""))
    engine_key = _engine_key_for_session(session)
    engine = _engine_label(engine_key)
    team_id = int(event["team_id"])
    owner = _owner_mention(session.match, team_id)
    if event_type.startswith("BOWL") or event_type == "HAT_TRICK":
        team_display = str(getattr(session, "bowling_team_display", "") or "Team")
    else:
        team_display = str(getattr(session, "batting_team_display", "") or "Team")

    if event_type == "PARTNERSHIP":
        stat_line = f"🤝 <b>Partnership</b> : {int(event['runs']):,} runs • {int(event['balls']):,} balls"
    elif event_type.startswith("BOWL") or event_type == "HAT_TRICK":
        stat_line = f"🎯 <b>Bowling</b> : {event['detail']}"
    else:
        stat_line = f"📊 <b>Batting</b> : {event['detail']}"

    title_emoji = milestone_emoji_html("title", "🔔")
    player_emoji = milestone_emoji_html("player", "👤")
    game_emoji = milestone_emoji_html("game", "🎮")
    achievement_emoji = milestone_emoji_html("achievement", _EVENT_FALLBACK(event_type))
    owner_emoji = milestone_emoji_html("owner", "🧑‍💻")
    stat_emoji = milestone_emoji_html("stat", "📈")

    return "\n".join(
        [
            f"<b>╭━━〔 {title_emoji} MILESTONE ALERT 〕━━╮</b>",
            "",
            f"{achievement_emoji} <b>{html.escape(title)}</b>",
            f"{player_emoji} <b>Player</b> : {html.escape(str(event['player_name']))}",
            f"{game_emoji} <b>Game</b> : {html.escape(engine)}",
            f"{owner_emoji} <b>Played By</b> : {owner}",
            f"🏟️ <b>Team</b> : {html.escape(team_display)}",
            f"{stat_emoji} <b>Achievement</b> : {html.escape(descriptor or event['detail'])}",
            stat_line,
            "",
            f"<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>",
        ]
    )


def _EVENT_FALLBACK(event_type: str) -> str:
    return MILESTONE_FALLBACK_EMOJIS.get(event_type, "🏅")


def _media_for(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        entry = MILESTONE_MEDIA.get(event["type"]) or {}
        if isinstance(entry, dict) and entry.get("file_id"):
            return entry
    return None


async def _send_alert(chat_id: int, text: str, media: dict[str, Any] | None = None) -> None:
    try:
        if media:
            media_type = str(media.get("type") or "animation").lower()
            file_id = media.get("file_id")
            if media_type == "video":
                await app.send_video(chat_id, file_id, caption=text, parse_mode="HTML")
            else:
                await app.send_animation(chat_id, file_id, caption=text, parse_mode="HTML")
            return
        await app.send_message(chat_id, text, parse_mode="HTML")
    except Exception as exc:
        # Milestones are informational. A Telegram delivery issue must never
        # interrupt scoring or the active match.
        print(f"[milestones] alert delivery failed: {exc!r}")


def schedule_milestone_notifications(session: Any, outcome: Any, striker_before: Any, partnership_id: int | None = None, partnership_players: tuple[str, str] | None = None) -> None:
    """Collect post-ball milestones and schedule a single combined alert."""
    try:
        events = _collect_events(session, outcome, striker_before, partnership_id=partnership_id, partnership_players=partnership_players)
        if not events:
            return
        text = "\n\n".join(_build_event_text(session, event) for event in events)
        media = _media_for(events)
        asyncio.create_task(_send_alert(int(session.chat_id), text, media=media))
    except Exception as exc:
        print(f"[milestones] collection failed: {exc!r}")
