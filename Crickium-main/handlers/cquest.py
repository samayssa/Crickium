from __future__ import annotations

import html
from datetime import datetime

from handlers.registry import register, register_callback
from app import app
from services.quest_engine import get_quest_view
from utils.PremiumEmoji import (
    coins_emoji_html,
    done_quest_emoji_html,
    quest_period_emoji_html,
    rubies_emoji_html,
    sigil_emoji_html,
)
from services.quest_catalog import REWARD_MAP

PERIOD_LABELS = {
    "daily": "DAILY TASK",
    "weekly": "WEEKLY TASK",
    "monthly": "MONTHLY TASK",
}


def _refresh_text(dt: datetime) -> str:
    # Telegram Bot API does not render Discord-style <t:...> timestamp tags.
    # Show the exact persisted server-local boundary instead.
    local = dt.astimezone() if getattr(dt, "tzinfo", None) else dt
    tz_name = local.tzname() or "LOCAL"
    return local.strftime(f"%d %b %Y, %I:%M %p {tz_name}")


def _keyboard(owner_id: int):
    uid = int(owner_id)
    return {
        "inline_keyboard": [
            [{"text": "Daily Task", "callback_data": f"cquest:{uid}:daily", "style": "primary"}],
            [
                {"text": "Weekly Task", "callback_data": f"cquest:{uid}:weekly", "style": "primary"},
                {"text": "Monthly Task", "callback_data": f"cquest:{uid}:monthly", "style": "primary"},
            ],
        ]
    }


def _task_reward(task: dict, period_type: str) -> str:
    reward = REWARD_MAP[str(period_type)][str(task["difficulty"])]
    bits = [f"{sigil_emoji_html()} <b>+{int(reward['sigils']):,} Sigils</b>"]
    if int(reward.get("coins") or 0):
        bits.append(f"{coins_emoji_html()} <b>+{int(reward['coins']):,} Coins</b>")
    if int(reward.get("rubies") or 0):
        bits.append(f"{rubies_emoji_html()} <b>+{int(reward['rubies']):,} Rubies</b>")
    return " • ".join(bits)


def _quest_text(period: dict, tasks: list[dict], completed: set[str], period_type: str) -> str:
    label = PERIOD_LABELS[period_type]
    header_emoji = quest_period_emoji_html(period_type)
    lines = [
        f"<b>╭━━━〔 {header_emoji} CRICKIUM QUEST • {label} 〕━━━╮</b>",
        "",
        f"<blockquote><i>Next refresh: {_refresh_text(period['end_at'])}</i></blockquote>",
        "",
    ]
    for idx, task in enumerate(tasks):
        tid = str(task["id"])
        done = tid in completed
        done_prefix = f"{done_quest_emoji_html()} " if done else ""
        title = html.escape(str(task["title"]))
        description = html.escape(str(task["description"]))
        lines.append(f"↳ {done_prefix}<b>{idx}. {title}</b>")
        lines.append(f"<blockquote><i>{description}</i></blockquote>")
        lines.append(_task_reward(task, period_type))
        if idx != len(tasks) - 1:
            lines.append("<b>──────────────</b>")
    lines.extend(["", f"<b>{len(completed & {str(t['id']) for t in tasks})}/{len(tasks)} Completed</b>", "", "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"])
    return "\n".join(lines)


@register("cquest")
async def cquest_command(message):
    chat_id = int(message["chat"]["id"])
    user_id = int((message.get("from") or {}).get("id") or 0)
    period, tasks, completed = await get_quest_view(user_id, "daily")
    text = _quest_text(period, tasks, completed, "daily")
    await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=_keyboard(user_id))


@register_callback("cquest")
async def cquest_callback(callback_query):
    parts = str(callback_query.get("data") or "").split(":")
    if len(parts) != 3 or parts[2] not in PERIOD_LABELS or not parts[1].isdigit():
        await app.answer_callback_query(callback_query["id"], "Invalid Quest menu.", show_alert=True)
        return
    _, owner_id, period_type = parts
    uid = int((callback_query.get("from") or {}).get("id") or 0)
    if uid != int(owner_id):
        await app.answer_callback_query(callback_query["id"], "This Quest menu belongs to another user.", show_alert=True)
        return
    period, tasks, completed = await get_quest_view(uid, period_type)
    text = _quest_text(period, tasks, completed, period_type)
    await app.edit_message_text(
        callback_query["message"]["chat"]["id"],
        callback_query["message"]["message_id"],
        text,
        parse_mode="HTML",
        reply_markup=_keyboard(uid),
    )
    await app.answer_callback_query(callback_query["id"], f"{PERIOD_LABELS[period_type].title()} loaded")
