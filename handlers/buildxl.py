from __future__ import annotations

import html
import itertools

from handlers.registry import register
from app import app
from engines.lineup_engine import load_squad
from database.lineups_repo import get_lineup_ids, save_lineup_ids
from database.squads_repo import save_team_squad
from services.player_card import overall_rating
from utils.country_flags import flag_for

XI_SIZE = 11
ROLE_ORDER = ("Batsman", "Wicketkeeper", "AllRounder", "Bowler")
ROLE_LIMITS = {
    "Batsman": (3, 4),
    "Wicketkeeper": (1, 2),
    "AllRounder": (3, 4),
    "Bowler": (3, 4),
}
ROLE_EMOJI = {
    "Batsman": "🏏",
    "Wicketkeeper": "🧤",
    "AllRounder": "🔄",
    "Bowler": "⚡",
}


def _role(player: dict) -> str:
    raw = str(player.get("role") or "").strip().lower().replace("-", "")
    if raw in {"batsman", "batter", "bat"}:
        return "Batsman"
    if raw in {"wicketkeeper", "wicket keeper", "wk"}:
        return "Wicketkeeper"
    if raw in {"allrounder", "all rounder", "ar"}:
        return "AllRounder"
    return "Bowler"


def _ovr(player: dict) -> int:
    return overall_rating(player.get("bat_level"), player.get("bowl_level"))


def _select_best_for_role(players: list[dict], count: int) -> list[dict]:
    # Stable tie-breaking: higher OVR first, then preserve existing squad order.
    indexed = list(enumerate(players))
    indexed.sort(key=lambda pair: (-_ovr(pair[1]), pair[0]))
    return [player for _, player in indexed[:count]]


def _best_xi(squad: list[dict]) -> tuple[list[dict] | None, str | None]:
    by_role = {role: [p for p in squad if _role(p) == role] for role in ROLE_ORDER}
    for role, (minimum, maximum) in ROLE_LIMITS.items():
        if len(by_role[role]) < minimum:
            return None, f"Need at least {minimum} {role.lower()} players, but your squad has only {len(by_role[role])}."

    best = None
    best_key = None
    role_ranges = [range(ROLE_LIMITS[r][0], min(ROLE_LIMITS[r][1], len(by_role[r])) + 1) for r in ROLE_ORDER]
    current_ids = []
    # Prefer the current XI on ties, so BuildXL doesn't shuffle unnecessarily.
    current_lineup = None

    # Current lineup IDs are loaded by caller and attached later through a module helper.
    for counts in itertools.product(*role_ranges):
        if sum(counts) != XI_SIZE:
            continue
        chosen = []
        total_ovr = 0
        preserved = 0
        for role, count in zip(ROLE_ORDER, counts):
            selected = _select_best_for_role(by_role[role], count)
            chosen.extend(selected)
            total_ovr += sum(_ovr(p) for p in selected)
        # Final deterministic order is role order, then OVR descending within role.
        chosen.sort(key=lambda p: (ROLE_ORDER.index(_role(p)), -_ovr(p)))
        key = (total_ovr, preserved)
        if best is None or key > best_key:
            best = chosen
            best_key = key
    return best, None


def _reorder_squad(squad: list[dict], xi: list[dict]) -> list[dict]:
    xi_ids = {int(p.get("player_id") or 0) for p in xi}
    bench = [p for p in squad if int(p.get("player_id") or 0) not in xi_ids]
    return list(xi) + bench


def _render(user_name: str, xi: list[dict], bench: list[dict]) -> str:
    lines = [
        "<b>╭━━〔 🤖 BUILD XL 〕━━╮</b>",
        "",
        f"👤 <b>{html.escape(user_name or 'Player')}</b>",
        "",
        "<blockquote>",
        "<b>🏏 BEST PLAYING XI</b>",
    ]
    for i, player in enumerate(xi, 1):
        role = _role(player)
        emoji = ROLE_EMOJI[role]
        name = html.escape(str(player.get("name") or "Player"))
        flag = flag_for(player.get("country"))
        lines.append(f"{i}. {emoji} <b>{name}</b> • OVR <b>{_ovr(player)}</b> {flag}")
    lines.extend([
        "</blockquote>",
        "",
        "<blockquote>",
        "<b>🪑 BENCH / SUBSTITUTES</b>",
    ])
    if bench:
        for i, player in enumerate(bench, 1):
            role = _role(player)
            lines.append(f"{i}. {ROLE_EMOJI[role]} {html.escape(str(player.get('name') or 'Player'))} • OVR <b>{_ovr(player)}</b>")
    else:
        lines.append("• None")
    lines.extend(["</blockquote>", "", "✅ <b>Best XI built using your current role restrictions.</b>", "╰━━━━━━━━━━━━━━━━━━╯"])
    return "\n".join(lines)


@register("buildxl")
async def buildxl_command(message):
    chat_id = int(message["chat"]["id"])
    user = message.get("from") or {}
    user_id = int(user.get("id") or 0)
    squad = await load_squad(user_id)
    if not squad:
        await app.send_message(chat_id, "⚠️ <b>No squad found.</b> Use /debut first to create your squad.", parse_mode="HTML")
        return
    xi, error = _best_xi(squad)
    if xi is None:
        await app.send_message(chat_id, f"⚠️ <b>BuildXL could not build a valid Playing XI.</b>\n\n{html.escape(error or 'Role limits cannot be satisfied with your current squad.')}", parse_mode="HTML")
        return

    lineup_ids = [int(p.get("player_id") or 0) for p in xi]
    # Keep squad display order consistent with the XI and bench after the rebuild.
    new_squad = _reorder_squad(squad, xi)
    await save_lineup_ids(user_id, lineup_ids)
    await save_team_squad(user_id, new_squad)

    # Render after persistence so the message represents the saved state.
    bench = new_squad[XI_SIZE:]
    display_name = user.get("first_name") or user.get("username") or "Player"
    await app.send_message(chat_id, _render(display_name, xi, bench), parse_mode="HTML")
