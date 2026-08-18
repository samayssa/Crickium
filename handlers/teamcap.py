from __future__ import annotations

print("teamcap.py loaded")

import html

from handlers.registry import register
from app import app
from database.captain_repo import set_captain_id
from database.squads_repo import get_team_squad
from engines.lineup_engine import load_current_xi


def _parse_player_name(text: str) -> str:
    parts = (text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


@register("teamcap")
async def teamcap_command(message):
    chat_id = message["chat"]["id"]
    user_id = int(message.get("from", {}).get("id") or 0)
    requested = _parse_player_name(message.get("text", ""))

    if not requested:
        await app.send_message(
            chat_id,
            "<b>⚠️ Please provide a player name.</b>\nUsage: <code>/teamcap Virat Kohli</code>",
            parse_mode="HTML",
        )
        return

    squad = await get_team_squad(user_id) or []
    target = next((p for p in squad if str(p.get("name") or "").casefold() == requested.casefold()), None)
    if target is None:
        await app.send_message(
            chat_id,
            f"<b>⚠️ This player is not in your squad. First buy the player, then try to make {html.escape(requested)} captain.</b>",
            parse_mode="HTML",
        )
        return

    xi = await load_current_xi(user_id) or []
    target_id = int(target.get("player_id") or 0)
    xi_ids = {int(p.get("player_id") or 0) for p in xi}
    if target_id not in xi_ids:
        await app.send_message(
            chat_id,
            f"<b>⚠️ This player is not in the Playing XI. First add {html.escape(str(target.get('name') or requested))} to the Playing XI, then try to make them captain.</b>",
            parse_mode="HTML",
        )
        return

    await set_captain_id(user_id, target_id)
    await app.send_message(
        chat_id,
        f"<b>{html.escape(str(target.get('name') or requested))} is now the captain.</b> 🧢",
        parse_mode="HTML",
    )
