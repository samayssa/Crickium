from __future__ import annotations

print("squad.py loaded")

import html

from handlers.registry import register
from app import app
from engines.lineup_engine import load_squad, load_current_xi
from database.captain_repo import get_captain_id
from utils.country_flags import flag_for
from utils.PremiumEmoji import ovr_emoji_html
from utils.user_identity import get_user_identity, inline_identity


async def _captain_id(user_id: int) -> int | None:
    return await get_captain_id(user_id)


def _icon(player: dict) -> str:
    role = str(player.get("role") or "").lower()
    if "wicket" in role or bool(player.get("is_wicketkeeper")):
        return "🧤"
    if "all" in role:
        return "🔄"
    if "bowl" in role:
        return "⚡"
    return "🏏"


def _overall(player: dict) -> int:
    return max(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))


def _squad_ovr(players: list[dict]) -> int:
    return sum(_overall(player) for player in players)


@register("squad")
async def squad_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")
    first_name = from_user.get("first_name")

    print(f"[squad] /squad invoked by user_id={user_id}")

    squad = await load_squad(user_id)
    if not squad:
        await app.send_message(chat_id, "⚠️ No squad found yet. Use /debut first to create your team.")
        return

    identity = await get_user_identity(user_id, first_name)
    captain_id = await _captain_id(user_id)
    xi = await load_current_xi(user_id) or []
    xi_ids = {int(p.get("player_id") or 0) for p in xi}
    ordered_squad = list(xi) + [p for p in squad if int(p.get("player_id") or 0) not in xi_ids]
    ordered_squad = ordered_squad[:25]

    lines = [
        "╭━━━〔 🏏 SQUAD 〕━━━╮",
        f"➤ {inline_identity(identity)} • 👥 {len(ordered_squad)}/25",
        f"➤ {ovr_emoji_html()} Squad OVR: {_squad_ovr(ordered_squad)}",
        "",
        "<blockquote>",
    ]

    total = len(ordered_squad)
    for i, player in enumerate(ordered_squad, start=1):
        name = html.escape(str(player.get("name") or "Player"))
        level = _overall(player)
        flag = flag_for(player.get("country"))
        icon = _icon(player)
        cap = " 🧢" if captain_id and int(player.get("player_id") or 0) == captain_id else ""
        prefix = "╰" if i == total else "├"
        lines.append(f"{prefix} {i}. {name} • {level} {flag} {icon}{cap}")
    lines += [
        "</blockquote>",
        "",
        f"➤ 🏏 Playing XI: {min(len(xi), 11)}",
        f"➤ 🔁 Substitutes: {max(0, len(squad) - min(len(xi), 11))}",
        "",
        "╰━━━━━━━━━━━━━━━━━━╯",
    ]

    await app.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
    print(f"[squad] Sent squad report for user_id={user_id} ({len(squad)} players)")
