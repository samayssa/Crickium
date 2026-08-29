from __future__ import annotations

import html
from database.query import fetchval
from config import NOTIFICATION_GROUP_ID


async def next_match_number() -> int:
    """Return the 1-based number of the current completed match across engines."""
    total = await fetchval(
        """
        SELECT
            (SELECT COUNT(*) FROM matches WHERE status='completed')
          + (SELECT COUNT(*) FROM play_matches WHERE status='completed')
          + (SELECT COUNT(*) FROM playint_matches WHERE status='completed')
          + (SELECT COUNT(*) FROM playipl_matches WHERE status='completed')
          + (SELECT COUNT(*) FROM match_challenges WHERE status='completed');
        """
    )
    return int(total or 0)


def _person(username, name):
    if username:
        return f"@{html.escape(str(username))}"
    return html.escape(str(name or 'Unknown'))


def _pitch(value) -> str:
    return html.escape(str(value or 'Not selected').replace('_', ' ').title())


def _score(snapshot: dict | None) -> str:
    if not snapshot:
        return 'Not available'
    team = html.escape(str(snapshot.get('batting_team_display') or snapshot.get('batting_team_name') or 'Team'))
    return f"{team} • {int(snapshot.get('runs') or 0)}/{int(snapshot.get('wickets') or 0)} ({snapshot.get('over_text') or '0.0'} Ov)"


async def send_match_completion_notification(app, *, engine: str, pitch: str | None, user1: tuple[str | None, str | None], user2: tuple[str | None, str | None], innings_1: dict, innings_2: dict, result: str | None = None) -> None:
    match_no = await next_match_number()
    result_text = html.escape(str(result or 'Match completed'))
    text = (
        f"<b>🏏 CRICKIUM MATCH COMPLETED #{match_no}</b>\n\n"
        f"<b>🎮 Engine:</b> {html.escape(engine)}\n"
        f"<b>🏟️ Pitch:</b> {_pitch(pitch)}\n\n"
        f"<b>👤 User 1:</b> {_person(*user1)}\n"
        f"<b>👤 User 2:</b> {_person(*user2)}\n\n"
        f"<b>1️⃣ First Innings:</b> {_score(innings_1)}\n"
        f"<b>2️⃣ Second Innings:</b> {_score(innings_2)}\n\n"
        f"<b>🏆 Result:</b> {result_text}"
    )
    try:
        await app.send_message(int(NOTIFICATION_GROUP_ID), text, parse_mode='HTML')
    except Exception as exc:
        print(f"[match_notification] failed to send notification: {exc!r}")
