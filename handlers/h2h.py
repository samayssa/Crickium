from __future__ import annotations

import html

from app import app
from handlers.registry import register
from database.query import fetchrow
from database.user_stats_repo import get_h2h_stats
from utils.mentions import mention_html
from database.squads_repo import get_team_squad


def _target_from_message(message: dict):
    reply = message.get("reply_to_message") or {}
    user = reply.get("from") or {}
    if user.get("id") and not user.get("is_bot"):
        return int(user["id"]), user.get("username"), user.get("first_name")

    parts = str(message.get("text") or "").split()
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    if not arg:
        return None
    if arg.startswith("@"):
        return ("username", arg[1:])
    if arg.isdigit():
        return ("id", int(arg))
    return None


async def _resolve_target(message: dict):
    value = _target_from_message(message)
    if not value:
        return None, "<b>⚠️ Use /h2h by replying to a user, with @username, or with a user ID.</b>"
    if isinstance(value[0], int):
        uid, username, first_name = value
        row = await fetchrow("SELECT user_id, username, first_name FROM users WHERE user_id=$1;", uid)
    else:
        kind, raw = value
        if kind == "id":
            row = await fetchrow("SELECT user_id, username, first_name FROM users WHERE user_id=$1;", int(raw))
        else:
            row = await fetchrow("SELECT user_id, username, first_name FROM users WHERE LOWER(username)=LOWER($1) LIMIT 1;", str(raw))
    if not row:
        return None, "<b>⚠️ I couldn't find that user in Crickium yet.</b>\n\n<i>Ask them to use the bot once so their profile can be registered.</i>"
    return dict(row), None


def _h2h_text(one: dict, two: dict, stats: dict) -> str:
    total = int(stats["total"])
    w1, l1 = int(stats["wins1"]), int(stats["losses1"])
    w2, l2 = int(stats["wins2"]), int(stats["losses2"])
    r1, r2 = float(stats["win_rate1"]), float(stats["win_rate2"])
    m1 = mention_html(int(one["user_id"]), one.get("username"), one.get("first_name"))
    m2 = mention_html(int(two["user_id"]), two.get("username"), two.get("first_name"))
    if total == 0:
        leader = "<b>👑 RIVALRY STATUS : AWAITING FIRST BATTLE</b>\n\n<b>No head-to-head battles have been recorded yet.</b>"
        battle = f"<b>{m1}</b> and <b>{m2}</b> are waiting for their first battle. 🔥"
    elif w1 == w2:
        leader = f"<b>👑 RIVALRY STATUS : TIED</b>\n\n<b>Both players are locked at {w1} - {w2}.</b>"
        battle = f"<b>{m1}</b> and <b>{m2}</b> are level in this rivalry.\n\n<b>{total} battles played • {w1} victories each</b>"
    else:
        winner = one if w1 > w2 else two
        winner_mention = mention_html(int(winner["user_id"]), winner.get("username"), winner.get("first_name"))
        margin = abs(w1 - w2)
        rate = r1 if w1 > w2 else r2
        wins = w1 if w1 > w2 else w2
        leader = (f"<b>👑 LEADING THE RIVALRY</b>\n\n<b>{winner_mention}</b>\n\n"
                  f"<b>📈 Lead : +{margin} Wins</b>\n<b>🎯 Win Rate : {rate:.1f}%</b>\n<b>⚔️ Win Margin : {margin} Matches</b>")
        battle = f"<b>{winner_mention}</b> has the upper hand in this rivalry.\n\n<b>{total} battles played • {wins} victories secured</b>"

    # The H2H card is deliberately fully bold, per the requested bot message style.
    return (
        "<b>╭━━━〔 ⚔️ H2H • RIVALRY 〕━━━╮</b>\n\n"
        f"<b><blockquote>👤 {m1}\n            VS\n👤 {m2}</blockquote></b>\n\n"
        "<b>📊 HEAD-TO-HEAD RECORD</b>\n\n"
        f"<b><blockquote>🎮 Total Matches : {total}\n\n"
        f"🏆 {m1}  {w1} Wins  •  {l1} Losses\n"
        f"🏆 {m2}  {w2} Wins  •  {l2} Losses</blockquote></b>\n\n"
        f"{leader}\n\n"
        "<b>🔥 BATTLE STATUS</b>\n\n"
        f"<b><blockquote>{battle}</blockquote></b>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )

@register("h2h")
async def h2h_command(message):
    uid = int((message.get("from") or {}).get("id") or 0)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    target, error = await _resolve_target(message)
    if error:
        await app.send_message(chat_id, error, parse_mode="HTML")
        return
    opponent_id = int(target["user_id"])
    if opponent_id == uid:
        await app.send_message(chat_id, "<b>⚠️ H2H needs two different players.</b>", parse_mode="HTML")
        return
    me = await fetchrow("SELECT user_id, username, first_name FROM users WHERE user_id=$1;", uid)
    if not me:
        await app.send_message(chat_id, "<b>⚠️ Your Crickium profile is not ready yet. Use /start first.</b>", parse_mode="HTML")
        return
    stats = await get_h2h_stats(uid, opponent_id)
    await app.send_message(chat_id, _h2h_text(dict(me), target, stats), parse_mode="HTML")
