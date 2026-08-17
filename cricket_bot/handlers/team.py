print("team.py loaded")

from handlers.registry import register
from app import app
from engines.match_engine import get_match_session
from engines.lineup_engine import load_squad, render_team_report
from utils.mentions import mention


@register("team")
async def team_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")
    username = from_user.get("username")
    first_name = from_user.get("first_name")

    squad = await load_squad(user_id)
    if not squad:
        await app.send_message(chat_id, "⚠️ No squad found yet. Use /debut first to create your team.")
        return

    session = get_match_session(chat_id)
    report = render_team_report(squad, f"TEAM REPORT for {mention(user_id, username, first_name)}", session=session)

    if session is not None:
        report += "\n\n*Match Status*\n"
        report += f"Stage: {session.stage}\n"
        if session.innings is not None:
            report += f"Score: {session.innings.score.runs}/{session.innings.score.wickets} in {session.innings.score.over_text}\n"
            report += f"Striker: {session.innings.striker.name if session.innings.striker else 'Player'}\n"
            report += f"Non-striker: {session.innings.non_striker.name if session.innings.non_striker else 'Player'}\n"
        if session.current_bowler is not None:
            report += f"Bowler: {session.current_bowler.get('name')}\n"

    await app.send_message(chat_id, report, parse_mode="Markdown")
