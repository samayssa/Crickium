"""Telegram texts (HTML) for the tournament + auction. Pure functions: rows in, strings out.

Every string stays well below Telegram's 4096 character limit and is safe to send with
``parse_mode="HTML"`` (all user supplied values are escaped).
"""
from __future__ import annotations

import html
import os
import re
from typing import Any, Mapping, Sequence

from services import tournament_core as tc
from utils.country_flags import flag_for
from utils.style import batting_style_text, bowling_style_text

ROOM_LABEL = "🏛 Open Auction Room"
HUB_LABEL = "🏆 Open Tournament"
LINE = "━━━━━━━━━━━━━━━"


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def bot_username() -> str:
    username = os.getenv("MINIAPP_BOT_USERNAME", "CrickiumBot").strip().lstrip("@")
    return username if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username) else "CrickiumBot"


def miniapp_link(param: str) -> str:
    return f"https://t.me/{bot_username()}?startapp={param}&mode=fullscreen"


def room_link(tid: int) -> str:
    return miniapp_link(f"auc_{int(tid)}")


def hub_link(tid: int) -> str:
    return miniapp_link(f"tour_{int(tid)}")


def button(text: str, data: str | None = None, *, url: str | None = None, style: str | None = None) -> dict:
    item: dict[str, Any] = {"text": text}
    if url:
        item["url"] = url
    else:
        item["callback_data"] = data
    if style:
        item["style"] = style
    return item


def room_button(tid: int) -> dict:
    """The blue (primary) Mini App button every auction message carries."""
    return button(ROOM_LABEL, url=room_link(tid), style="primary")


def hub_button(tid: int, label: str = HUB_LABEL) -> dict:
    return button(label, url=hub_link(tid), style="primary")


def markup(*rows: Sequence[dict]) -> dict:
    return {"inline_keyboard": [list(r) for r in rows if r]}


# ---------------------------------------------------------------------------
# small formatters
# ---------------------------------------------------------------------------
def team_short(team: Mapping[str, Any] | None) -> str:
    if not team:
        return "—"
    return str(team.get("short") or team.get("base_code") or team.get("name") or "TEAM")


def team_label(team: Mapping[str, Any] | None, owner: bool = True) -> str:
    """<b>CSK</b> · Chennai Super Kings · Aman"""
    if not team:
        return "—"
    parts = [f"<b>{esc(team_short(team))}</b>"]
    name = str(team.get("name") or "")
    if name and name.strip().lower() != team_short(team).strip().lower():
        parts.append(esc(name))
    if owner and team.get("owner_name"):
        parts.append(f"<i>{esc(team['owner_name'])}</i>")
    return " · ".join(parts)


def role_icon(role: Any) -> str:
    return tc.ROLE_ICON.get(tc.norm_role(role), "🏏")


def role_label(role: Any) -> str:
    return tc.ROLE_LABEL.get(tc.norm_role(role), "Player")


def role_short(role: Any) -> str:
    return tc.ROLE_SHORT.get(tc.norm_role(role), "BAT")


def player_heading(lot: Mapping[str, Any]) -> str:
    flag = flag_for(lot.get("country")) if lot.get("country") else ""
    tag = " ✈️" if lot.get("overseas") else ""
    return f"{role_icon(lot.get('role'))} <b>{esc(lot.get('name'))}</b> {flag}{tag}".strip()


def money(lakh: Any) -> str:
    return tc.fmt_money(lakh)


def multiple(final: int, base: int) -> str:
    if not base or final <= base:
        return ""
    ratio = final / base
    return f" · {ratio:.1f}× base" if ratio >= 1.5 else ""


# ---------------------------------------------------------------------------
# auction messages
# ---------------------------------------------------------------------------
def lot_card(t: Mapping[str, Any], lot: Mapping[str, Any], total_lots: int) -> str:
    """Message A - the player's card, pinned in the group."""
    ovr = tc.player_ovr(lot)
    round_tag = "  ·  ⚡ <b>Accelerated round</b>" if int(lot.get("round") or 1) > 1 else ""
    set_line = f"{esc(lot.get('set_name'))} ({int(lot.get('set_pos') or 1)}/{int(lot.get('set_size') or 1)})"
    pool_name = esc(lot.get("pool_name")) if lot.get("pool_name") else ""
    return (
        f"🏛 <b>AUCTION</b> · {esc(t.get('name'))}{round_tag}\n"
        f"<blockquote>{player_heading(lot)}</blockquote>"
        f"<blockquote>🎭 <b>Role</b> ➤ {esc(role_label(lot.get('role')))}\n"
        f"⭐ <b>Overall</b> ➤ {ovr}\n"
        f"🏏 <b>Bat</b> ➤ {int(lot.get('bat_level') or 0)}   ⚡ <b>Bowl</b> ➤ {int(lot.get('bowl_level') or 0)}\n"
        f"🧤 <b>Bat Style</b> ➤ {esc(batting_style_text(lot.get('batting_hand')))}\n"
        f"🎯 <b>Bowl Style</b> ➤ {esc(bowling_style_text(lot.get('bowling_hand')))}</blockquote>"
        f"<blockquote>💰 <b>Base Price</b> ➤ <b>{money(lot.get('base_lakh'))}</b>\n"
        f"📦 <b>Set</b> ➤ {set_line}\n"
        f"{('🏷 <b>Pool</b> ➤ ' + pool_name + chr(10)) if pool_name else ''}"
        f"🔢 <b>Lot</b> ➤ {int(lot.get('ord') or 0)} of {int(total_lots or 0)}</blockquote>"
        f"⏱ <i>{tc.BID_SECONDS}s timer — it resets on every bid</i>"
    )


def _bid_trail(bids: Sequence[Mapping[str, Any]], limit: int = 3) -> str:
    chips = []
    for b in list(bids)[:limit]:
        chips.append(f"{esc(b.get('short') or b.get('base_code') or '?')} {money(b.get('amount_lakh'))}")
    return "  ‹  ".join(chips)


def ticker(t: Mapping[str, Any], lot: Mapping[str, Any], total_lots: int, leader: Mapping[str, Any] | None,
           bids: Sequence[Mapping[str, Any]], note: str = "") -> str:
    """Message B - the live bidding message (edited on every bid)."""
    has_bid = int(lot.get("bid_count") or 0) > 0
    head = f"🔴 <b>LIVE BIDDING</b>  ·  Lot {int(lot.get('ord') or 0)}/{int(total_lots or 0)}"
    if int(lot.get("round") or 1) > 1:
        head += "  ·  ⚡ Accelerated"
    lines = [head, f"<blockquote>{player_heading(lot)}  <i>({role_short(lot.get('role'))})</i></blockquote>"]
    if has_bid and leader:
        lines.append(f"💰 <b>Current bid</b> ➤ <b>{money(lot.get('cur_bid_lakh'))}</b>")
        lines.append(f"👑 <b>Leading</b> ➤ {team_label(leader)}")
        trail = _bid_trail(bids)
        if trail:
            lines.append(f"<blockquote>📜 {trail}</blockquote>")
    else:
        lines.append(f"💰 <b>Base price</b> ➤ <b>{money(lot.get('base_lakh'))}</b>")
        lines.append("🙋 <i>No bids yet — be the first!</i>")
    lines.append(note if note else f"⏱ <i>{tc.BID_SECONDS}s — the clock resets on every bid</i>")
    return "\n".join(lines)


def going_note(stage: str) -> str:
    return {"once": "⏳ <b>Going once…</b>", "twice": "⚠️ <b>Going twice…</b>"}.get(stage, "")


def sold_text(t: Mapping[str, Any], lot: Mapping[str, Any], team: Mapping[str, Any] | None, total_lots: int) -> str:
    final, base = int(lot.get("sold_lakh") or lot.get("cur_bid_lakh") or 0), int(lot.get("base_lakh") or 0)
    purse = ""
    if team:
        purse = (f"\n💼 <b>Purse left</b> ➤ {money(team.get('purse_left'))}   ·   "
                 f"<b>Squad</b> ➤ {int(team.get('squad_count') or 0)}/{int((t or {}).get('squad_max') or 0)}")
    return (
        f"🔨 <b>SOLD!</b>  ·  Lot {int(lot.get('ord') or 0)}/{int(total_lots or 0)}\n"
        f"<blockquote>{player_heading(lot)}</blockquote>"
        f"🏆 <b>Bought by</b> ➤ {team_label(team)}\n"
        f"💰 <b>Price</b> ➤ <b>{money(final)}</b> <i>(base {money(base)}{multiple(final, base)})</i>"
        f"{purse}"
    )


def unsold_text(t: Mapping[str, Any], lot: Mapping[str, Any], total_lots: int, skipped: bool = False) -> str:
    why = "skipped by the host" if skipped else "no bids"
    return (
        f"❌ <b>UNSOLD</b>  ·  Lot {int(lot.get('ord') or 0)}/{int(total_lots or 0)}\n"
        f"<blockquote>{player_heading(lot)}</blockquote>"
        f"💰 <b>Base price</b> ➤ {money(lot.get('base_lakh'))}  <i>({why})</i>"
    )


def intro(t: Mapping[str, Any], teams: Sequence[Mapping[str, Any]], total_lots: int, n_sets: int, start_in: int) -> str:
    ladder = tc.ladder_rows(t.get("ladder"))
    ladder_text = "\n".join(f"   {esc(a)} ➤ <b>{esc(b)}</b>" for a, b in ladder)
    overseas = int(t.get("overseas_squad_max") or 0)
    team_rows = "\n".join(
        f"{i}. {team_label(team)}" for i, team in enumerate(teams, start=1)
    )
    return (
        f"🏛 <b>{esc(t.get('name'))} — AUCTION</b>\n"
        f"⏳ <b>Starting in {int(start_in)}s</b>\n"
        f"<blockquote>🏏 <b>Players</b> ➤ {int(total_lots)} in {int(n_sets)} sets\n"
        f"💼 <b>Purse</b> ➤ {money(t.get('purse_lakh'))} per team\n"
        f"👥 <b>Squad</b> ➤ {int(t.get('squad_min') or 0)}–{int(t.get('squad_max') or 0)} players"
        f"{(chr(10) + '✈️ <b>Overseas</b> ➤ max ' + str(overseas) + ' in a squad') if overseas else ''}\n"
        f"⏱ <b>Timer</b> ➤ {tc.BID_SECONDS}s, resets on every bid</blockquote>"
        f"<blockquote>📈 <b>Bid increments</b>\n{ladder_text}</blockquote>"
        f"<b>Teams</b>\n{team_rows}\n\n"
        f"💡 <i>Bid with the button under each player, or in the Auction Room.</i>"
    )


def paused_text(by: str | None = None) -> str:
    return "⏸ <b>Auction paused</b>" + (f" by {esc(by)}" if by else "") + "\n<i>The clock is frozen — bidding resumes where it stopped.</i>"


def resumed_text(by: str | None = None) -> str:
    return "▶️ <b>Auction resumed</b>" + (f" by {esc(by)}" if by else "") + "\n<i>Bidding is open again.</i>"


def accelerated_text(n_players: int) -> str:
    return (f"⚡ <b>ACCELERATED ROUND</b>\n<blockquote>{int(n_players)} unsold players come back for one more chance, "
            f"at their original base prices.</blockquote>")


def finish_text(t: Mapping[str, Any], teams: Sequence[Mapping[str, Any]], buys: Sequence[Mapping[str, Any]], sold: int, unsold: int,
                ended_early: bool = False) -> str:
    rows = []
    for team in sorted(teams, key=lambda x: int(x.get("team_no") or 0)):
        rows.append(f"<b>{esc(team_short(team))}</b> · {int(team.get('squad_count') or 0)} players · purse {money(team.get('purse_left'))}")
    top = []
    for i, buy in enumerate(buys[:5], start=1):
        top.append(f"{i}. {esc(buy.get('name'))} ➤ <b>{money(buy.get('price_lakh'))}</b> ({esc(buy.get('short') or '')})")
    head = "🛑 <b>AUCTION ENDED</b>" if ended_early else "🏁 <b>AUCTION COMPLETE</b>"
    return (
        f"{head} · {esc(t.get('name'))}\n"
        f"<blockquote>✅ <b>Sold</b> ➤ {int(sold)}   ❌ <b>Unsold</b> ➤ {int(unsold)}</blockquote>"
        + (f"<blockquote>💎 <b>Top buys</b>\n" + "\n".join(top) + "</blockquote>" if top else "")
        + f"<blockquote>👥 <b>Squads</b>\n" + "\n".join(rows) + "</blockquote>"
        + "<i>Squads were topped up automatically where needed. The host can now start the tournament.</i>"
    )


def pin_warning() -> str:
    return ("⚠️ <b>I can't pin messages here.</b>\n"
            "Make me an admin with the <b>Pin Messages</b> right so every new player's card can be pinned. "
            "The auction continues without pinning.")


# ---------------------------------------------------------------------------
# bid feedback (alert text for the person who tapped)
# ---------------------------------------------------------------------------
def bid_feedback(result: Mapping[str, Any]) -> str:
    """Short plain text (callback alert / Mini App toast) for a place_bid result."""
    if result.get("ok"):
        return f"✅ Bid placed: {money(result.get('price'))}"
    reason = str(result.get("reason") or "")
    price = result.get("price")
    limit = result.get("limit")
    if reason == "leading":
        return "👑 You already have the highest bid."
    if reason == "moved":
        return f"⚡ Someone bid first — the price is now {money(price)}."
    if reason == "closed":
        return "🔒 This lot is closed."
    if reason == "paused":
        return "⏸ The auction is paused."
    if reason == "not_in":
        return "Only team owners can bid in this auction."
    if reason == "no_team":
        return "Your team was not found in this tournament."
    if reason == "busy":
        return "⏳ The auction is busy for a moment — tap again."
    if reason == "squad_full":
        return "🚫 Your squad is full."
    if reason == "overseas_full":
        return "🚫 Your overseas quota is full."
    if reason == "purse":
        left = f" You can bid up to {money(limit)}." if limit is not None and int(limit) > 0 else ""
        return f"💸 Not enough purse for {money(price)}.{left}"
    return "Could not place the bid. Try again."
