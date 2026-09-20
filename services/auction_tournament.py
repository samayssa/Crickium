from __future__ import annotations

import html
import json
import re
from typing import Any

from app import app
from database.auction_tournament_repo import (
    ACTIVE_DRAFT_STATUSES,
    assign_team,
    create_ipl_teams,
    create_pools,
    fetch_all_pool_players,
    fetch_pool_summary,
    fetch_teams,
    find_user_by_identifier,
    get_active_draft,
    get_team,
    get_tournament,
    get_tournament_for_prize,
    get_user_registration,
    remove_team_owner,
    update_tournament,
)
from database.query import execute, fetchrow, fetchval, transaction
from database.squads_repo import get_team_squad, save_team_squad
from services.player_card import overall_rating
from utils.PremiumEmoji import get_ipl_team_emoji
from utils.style import batting_style_text, bowling_style_text
from utils.country_flags import flag_for

# Avoid a dependency on any existing game's rendering layer. These are the
# canonical IPL teams already used by the existing PlayIPL engine.
IPL_TEAM_MAP = {
    "CSK": "Chennai Super Kings",
    "DC": "Delhi Capitals",
    "GT": "Gujarat Titans",
    "KKR": "Kolkata Knight Riders",
    "LSG": "Lucknow Super Giants",
    "MI": "Mumbai Indians",
    "PBKS": "Punjab Kings",
    "RR": "Rajasthan Royals",
    "RCB": "Royal Challengers Bengaluru",
    "SRH": "Sunrisers Hyderabad",
}
IPL_TEAM_ORDER = list(IPL_TEAM_MAP)

MIN_PRIZE_COINS = 2_500_000
MIN_PRIZE_RUBIES = 1_000
MIN_PRIZE_PLAYER_OVR = 85
MAX_PRIZE_PLAYER_OVR = 99

_COIN_RE = re.compile(r"^([\d,]+)\s*(?:CR|CRORE|CRS)?$", re.I)
_AMOUNT_RE = re.compile(r"^([\d,]+(?:\.\d+)?)\s*(CR|CRORE|L|LAKH|LAKHS|K)?$", re.I)
_POOL_RE = re.compile(
    r"^\s*\[Pool\s+(\d+)\s*:\s*(.+?)\]\s*$",
    re.I,
)
_BASE_RE = re.compile(r"^\s*Base\s*:\s*(.+?)\s*$", re.I)


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def creator_display(creator: dict) -> str:
    username = str(creator.get("username") or "").strip()
    if username:
        return f"@{esc(username.lstrip('@'))}"
    return esc(creator.get("first_name") or creator.get("creator_name") or "Tournament Host")


def team_button_emoji_html(code: str) -> str:
    emoji_id = get_ipl_team_emoji(code)
    return emoji_id or ""


def parse_amount(raw: str) -> int | None:
    value = str(raw or "").strip().replace(",", "")
    match = _AMOUNT_RE.match(value)
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "").upper()
    if unit in {"CR", "CRORE"}:
        number *= 10_000_000
    elif unit in {"L", "LAKH", "LAKHS"}:
        number *= 100_000
    elif unit == "K":
        number *= 1_000
    if number <= 0 or number != int(number):
        return None
    return int(number)


def parse_prize_input(text: str) -> tuple[dict | None, str | None]:
    raw = str(text or "").strip()
    match = re.match(
        r"^\s*([\d,]+)\s*,\s*([\d,]+)(?:\s*,\s*\((.*?)\))?\s*$",
        raw,
        re.S,
    )
    if not match:
        return None, (
            "Invalid format. Reply to the prize prompt with:\n"
            "<code>3000000, 1500</code>\n"
            "or\n"
            "<code>3000000, 1500, (Virat Kohli)</code>"
        )

    coins = int(match.group(1).replace(",", ""))
    rubies = int(match.group(2).replace(",", ""))
    player_name = (match.group(3) or "").strip() or None

    if coins < MIN_PRIZE_COINS:
        return None, f"Minimum tournament prize is <b>{MIN_PRIZE_COINS:,} Coins</b>."
    if rubies < MIN_PRIZE_RUBIES:
        return None, f"Minimum tournament prize is <b>{MIN_PRIZE_RUBIES:,} Rubies</b>."

    return {
        "coins": coins,
        "rubies": rubies,
        "player_query": player_name,
    }, None


async def resolve_owned_prize_player(user_id: int, query: str | None) -> tuple[dict | None, str | None]:
    if not query:
        return None, None

    squad = await get_team_squad(int(user_id)) or []
    q = str(query).strip().casefold()
    matches: list[dict] = []
    for raw in squad:
        player = dict(raw)
        base_name = str(player.get("name") or "").strip()
        edition = str(player.get("edition") or "").strip()
        display = f"{base_name} ({edition})" if edition else base_name
        candidates = {base_name.casefold(), display.casefold()}
        if q in candidates:
            player["ovr"] = overall_rating(player.get("bat_level"), player.get("bowl_level"))
            matches.append(player)

    if not matches:
        return None, f"You do not own a player card named <b>{esc(query)}</b> in your squad."
    if len(matches) > 1:
        names = ", ".join(
            esc(f"{p.get('name','Player')} ({p.get('edition')})") if p.get("edition") else esc(p.get("name"))
            for p in matches
        )
        return None, f"That player name is ambiguous in your squad. Matching cards: {names}"

    player = matches[0]
    ovr = int(player.get("ovr") or 0)
    if ovr < MIN_PRIZE_PLAYER_OVR or ovr > MAX_PRIZE_PLAYER_OVR:
        return None, (
            f"<b>{esc(player.get('name'))}</b> is overall <b>{ovr}</b>. "
            f"Prize-pool player cards must be between <b>{MIN_PRIZE_PLAYER_OVR}-{MAX_PRIZE_PLAYER_OVR}</b> overall."
        )

    player["identity_key"] = f"{'special' if player.get('is_special') else 'global'}:{abs(int(player.get('player_id') or 0))}"
    return player, None


def build_prize_breakdown(coins: int, rubies: int, player: dict | None) -> list[dict]:
    """Build a cricket-style 11-award prize structure.

    The creator's total coin/ruby pool is distributed using fixed percentages
    instead of an equal 1/11 split:
      31% winner, 20% runner-up, 13% third, 10% fourth,
       5% orange cap,  5% purple cap, 7% MVP, 4% emerging player,
       2% most sixes,   2% most fours,  1% best strike rate.

    The optional player card is displayed separately as a card prize.
    """
    labels = [
        "🏆 Winning Team",
        "🥈 Runner-up",
        "🥉 3rd Position",
        "4️⃣ 4th Position",
        "🟠 Orange Cap",
        "🟣 Purple Cap",
        "⭐ MVP",
        "🌱 Emerging Player",
        "💥 Most Sixes",
        "4️⃣ Most Fours",
        "🎯 Best Strike Rate",
    ]
    percentages = [31, 20, 13, 10, 5, 5, 7, 4, 2, 2, 1]

    def allocate(total: int) -> list[int]:
        total = max(0, int(total))
        raw = [(total * pct) // 100 for pct in percentages]
        remainder = total - sum(raw)
        # Largest prize gets any integer rounding remainder so the published
        # total always equals the creator's exact pool.  The remainder is
        # normally tiny relative to the prize and never changes the intended
        # ordering.
        raw[0] += remainder
        return raw

    coin_values = allocate(coins)
    ruby_values = allocate(rubies)
    result = []
    for index, label in enumerate(labels):
        result.append(
            {
                "part": index + 1,
                "label": label,
                "coins": coin_values[index],
                "rubies": ruby_values[index],
                "percentage": percentages[index],
                "player": None,
            }
        )

    # A creator-supplied player card is an additional non-cash prize.  Keep it
    # attached to the prize structure without using one of the 11 cash/ruby
    # slices for a card.
    if player:
        result[-1]["player"] = player

    return result


def render_prize_confirmation(prize: dict) -> str:
    player = prize.get("player")
    player_line = "No player card" if not player else (
        f"<b>{esc(player.get('name'))}</b> • OVR {int(player.get('ovr') or 0)}"
        + (f" • {esc(player.get('edition'))}" if player.get("edition") else "")
    )
    return (
        "<b>╭━━〔 💰 PRIZE POOL REVIEW 〕━━╮</b>\n\n"
        "<blockquote>"
        f"🪙 <b>Coins</b>  : {int(prize['coins']):,}\n"
        f"💎 <b>Rubies</b> : {int(prize['rubies']):,}\n"
        f"🎴 <b>Player</b> : {player_line}"
        "</blockquote>\n\n"
        "<b>Are these prize-pool details correct?</b>\n"
        "Choose <b>Yes, go ahead</b> to lock them, <b>No, change it</b> to enter them again, or <b>Cancel</b> to stop tournament creation.\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def render_prize_prompt(error: str | None = None) -> str:
    extra = f"\n\n<blockquote>⚠️ {error}</blockquote>" if error else ""
    return (
        "<b>╭━━〔 💰 SET TOURNAMENT PRIZE 〕━━╮</b>\n\n"
        "Reply to this message with the prize using exactly:\n\n"
        "<code>3000000, 1500</code>\n"
        "or\n"
        "<code>3000000, 1500, (Virat Kohli)</code>\n\n"
        "<blockquote>"
        f"🪙 Minimum Coins : <b>{MIN_PRIZE_COINS:,}</b>\n"
        f"💎 Minimum Rubies: <b>{MIN_PRIZE_RUBIES:,}</b>\n"
        f"🎴 Optional Player: must be owned by you and OVR {MIN_PRIZE_PLAYER_OVR}-{MAX_PRIZE_PLAYER_OVR}."
        "</blockquote>\n\n"
        "The optional player card is taken from the creator's squad only when the tournament is finally created."
        f"{extra}\n\n<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def render_tournament_overview(tournament: dict, *, show_setpool: bool = True) -> str:
    player = tournament.get("prize_player")
    player_data = player if isinstance(player, dict) else {}
    player_line = "None"
    if player_data:
        player_line = (
            f"{esc(player_data.get('name'))}"
            + (f" ({esc(player_data.get('edition'))})" if player_data.get("edition") else "")
            + f" • OVR {int(player_data.get('ovr') or 0)}"
        )
    pools = int(tournament.get("pool_count") or 0)
    players = int(tournament.get("player_count") or 0)
    group_name = tournament.get("host_group_name") or "Not set"
    group_line = esc(group_name)
    body = [
        "<b>╭━━〔 🏏 IPL TOURNAMENT OVERVIEW 〕━━╮</b>",
        "",
        f"🏆 <b>Tournament</b> : {esc(tournament.get('tournament_name') or 'Indian Premier League')}",
        "🎯 <b>Format</b>      : IPL • T20",
        "🔨 <b>Mode</b>        : Auction Tournament",
        f"👤 <b>Hosted by</b>   : {creator_display(tournament)}",
        f"🪙 <b>Coins</b>       : {int(tournament.get('prize_coins') or 0):,}",
        f"💎 <b>Rubies</b>      : {int(tournament.get('prize_rubies') or 0):,}",
        f"🎴 <b>Prize Player</b> : {player_line}",
        f"📦 <b>Auction Pools</b>: {pools} pools • {players} players",
        f"👥 <b>Host Group</b>   : {group_line}",
    ]
    if show_setpool:
        body += [
            "",
            "<blockquote>"
            "📥 <b>Next step</b>\n"
            "Set the auction player pool with <code>/setpool IPL</code> and reply to that command with either the Telegram text format or a .txt file."
            "</blockquote>",
        ]
    return "\n".join(body + ["", "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"])


def render_group_review(tournament: dict) -> str:
    return (
        "<b>╭━━〔 🌐 HOST GROUP REVIEW 〕━━╮</b>\n\n"
        f"🌐 <b>Group</b>       : {esc(tournament.get('host_group_name') or 'Unknown')}\n"
        f"🆔 <b>Group ID</b>   : <code>{int(tournament.get('host_group_id') or 0)}</code>\n"
        f"🏆 <b>Tournament</b> : {esc(tournament.get('tournament_name') or 'Indian Premier League')}\n"
        "🎯 <b>Mode</b>        : Auction Tournament\n"
        f"🪙 <b>Prize</b>       : {int(tournament.get('prize_coins') or 0):,} Coins • {int(tournament.get('prize_rubies') or 0):,} Rubies\n"
        f"📦 <b>Auction</b>     : {int(tournament.get('pool_count') or 0)} pools • {int(tournament.get('player_count') or 0)} players\n\n"
        "<blockquote>"
        "✅ The bot will announce the tournament in this public group and pin the live team-registration message.\n"
        "⚠️ The bot must already be in the group with full admin permissions."
        "</blockquote>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def render_final_review(tournament: dict) -> str:
    group = tournament.get("host_group_name") or "Not set"
    return (
        "<b>╭━━〔 🏏 FINAL TOURNAMENT REVIEW 〕━━╮</b>\n\n"
        f"🏆 <b>Tournament</b> : {esc(tournament.get('tournament_name') or 'Indian Premier League')}\n"
        "🎯 <b>Format</b>      : IPL • T20\n"
        "🔨 <b>Mode</b>        : Auction Tournament\n"
        f"👤 <b>Hosted by</b>   : {creator_display(tournament)}\n"
        f"🪙 <b>Coins</b>       : {int(tournament.get('prize_coins') or 0):,}\n"
        f"💎 <b>Rubies</b>      : {int(tournament.get('prize_rubies') or 0):,}\n"
        f"🎴 <b>Prize Player</b> : {esc((tournament.get('prize_player') or {}).get('name') if isinstance(tournament.get('prize_player'), dict) else 'None')}\n"
        f"📦 <b>Auction</b>     : {int(tournament.get('pool_count') or 0)} pools • {int(tournament.get('player_count') or 0)} players\n"
        f"🌐 <b>Group</b>       : {esc(group)}\n\n"
        "<blockquote>"
        "Everything above is ready. Press <b>Yes, create</b> to create the tournament."
        "</blockquote>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def render_creation_success(tournament: dict, group_ok: bool, group_error: str | None = None) -> str:
    lines = [
        "<b>╭━━〔 ✅ TOURNAMENT CREATED 〕━━╮</b>",
        "",
        f"👤 <b>Hosted by</b>   : {creator_display(tournament)}",
        f"🏆 <b>Tournament</b> : {esc(tournament.get('tournament_name') or 'Indian Premier League')}",
        "🎯 <b>Format</b>      : IPL • T20",
        "🔨 <b>Mode</b>        : Auction Tournament",
        f"🪙 <b>Prize</b>       : {int(tournament.get('prize_coins') or 0):,} Coins • {int(tournament.get('prize_rubies') or 0):,} Rubies",
        f"📦 <b>Auction</b>     : {int(tournament.get('pool_count') or 0)} pools • {int(tournament.get('player_count') or 0)} players",
        "",
    ]
    if not tournament.get("host_group_id"):
        lines += [
            "🌐 <b>Host Group</b>   : Not set",
            "<blockquote>No group was selected. The tournament is created and its team-registration board is not posted to a group.</blockquote>",
        ]
    elif group_ok:
        lines += [
            f"🌐 <b>Group</b>       : {esc(tournament.get('host_group_name') or 'Tournament Group')}",
            "",
            "📌 <b>Live registration has been posted and pinned in the host group.</b>",
        ]
    else:
        lines += [
            "⚠️ <b>Group announcement was not completed.</b>",
            f"<blockquote>{esc(group_error or 'Telegram rejected the announcement request.')}</blockquote>",
        ]
    lines += ["", "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"]
    return "\n".join(lines)


def render_group_announcement(tournament: dict, teams: list[dict]) -> str:
    lines = [
        "<b>╭━━〔 🏏 IPL AUCTION TOURNAMENT 〕━━╮</b>",
        "",
        f"🏆 <b>Tournament</b> : {esc(tournament.get('tournament_name') or 'Indian Premier League')}",
        f"👤 <b>Hosted by</b>   : {creator_display(tournament)}",
        f"🪙 <b>Prize</b>       : {int(tournament.get('prize_coins') or 0):,} Coins • {int(tournament.get('prize_rubies') or 0):,} Rubies",
        f"📦 <b>Auction Pool</b>: {int(tournament.get('pool_count') or 0)} pools • {int(tournament.get('player_count') or 0)} players",
        "",
        "<blockquote>",
        "<b>🏷️ FRANCHISE STATUS</b>",
    ]
    for row in teams:
        code = str(row["team_code"])
        team = IPL_TEAM_MAP.get(code, code)
        if row["owner_user_id"]:
            owner = row.get("owner_username") or row.get("owner_name") or str(row["owner_user_id"])
            lines.append(f"✅ {esc(team)} → {esc(owner if str(owner).startswith('@') else '@' + str(owner))}")
        else:
            lines.append(f"🟢 {esc(team)} → Available")
    lines += [
        "</blockquote>",
        "",
        "<b>Tap an available franchise below to request it.</b>",
        "",
        "<blockquote>"
        "ℹ️ <b>Self-registration</b> can be enabled by the tournament host.\n"
        "🛠️ The host can also use <code>/teamown</code> for manual assignment/removal.\n"
        "🏆 To view the complete prize split, send <code>/prize</code>."
        "</blockquote>",
        "",
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>",
    ]
    return "\n".join(lines)


def team_keyboard(tournament_id: int, teams: list[dict], *, self_registration: bool) -> dict:
    rows = []
    current = []
    for row in teams:
        code = str(row["team_code"])
        label = f"{IPL_TEAM_MAP.get(code, code)}"
        if row["owner_user_id"]:
            label = f"✅ {label}"
            style = "danger"
        else:
            label = f"{label}"
            style = "primary"
        button = {
            "text": label,
            "callback_data": f"tour_team:{int(tournament_id)}:{code}",
            "style": style,
        }
        emoji_id = team_button_emoji_html(code)
        if emoji_id:
            button["icon_custom_emoji_id"] = emoji_id
        current.append(button)
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append([
        {
            "text": "✅ Self-registration ENABLED" if self_registration else "⚪ Enable self-registration",
            "callback_data": f"tour_selfreg:{int(tournament_id)}",
            "style": "success" if self_registration else "primary",
        }
    ])
    return {"inline_keyboard": rows}


def create_type_keyboard(user_id: int) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Indian Premier League", "callback_data": f"tour_type:IPL:{int(user_id)}", "style": "primary"}],
            [{"text": "T20 World Cup Tournament", "callback_data": f"tour_type:T20WC:{int(user_id)}", "style": "success"}],
            [{"text": "Custom Tournament", "callback_data": f"tour_type:CUSTOM:{int(user_id)}", "style": "danger"}],
            [{"text": "❌ Cancel", "callback_data": f"tour_new_cancel:{int(user_id)}", "style": "danger"}],
        ]
    }


def mode_keyboard(tournament_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🔨 Auction Tour", "callback_data": f"tour_mode:{tournament_id}:auction", "style": "primary"},
                {"text": "🏏 Without Auction Tour", "callback_data": f"tour_mode:{tournament_id}:noauction", "style": "primary"},
            ],
            [{"text": "❌ Cancel", "callback_data": f"tour_cancel:{tournament_id}", "style": "danger"}],
        ]
    }


def cancel_keyboard(tournament_id: int) -> dict:
    return {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": f"tour_cancel:{tournament_id}", "style": "danger"}]]}


def confirm_prize_keyboard(tournament_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Yes, go ahead", "callback_data": f"tour_prize_confirm:{tournament_id}", "style": "success"},
                {"text": "🔄 No, change it", "callback_data": f"tour_prize_change:{tournament_id}", "style": "primary"},
            ],
            [{"text": "❌ Cancel", "callback_data": f"tour_cancel:{tournament_id}", "style": "danger"}],
        ]
    }


def pool_confirmation_keyboard(tournament_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Yes, go ahead", "callback_data": f"tour_pool_confirm:{tournament_id}", "style": "success"},
                {"text": "🔄 No, change it", "callback_data": f"tour_pool_change:{tournament_id}", "style": "primary"},
            ],
            [{"text": "❌ Cancel", "callback_data": f"tour_cancel:{tournament_id}", "style": "danger"}],
        ]
    }


def group_choice_keyboard(tournament_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Yes, I want", "callback_data": f"tour_group_yes:{tournament_id}", "style": "success"},
                {"text": "No, I don't want", "callback_data": f"tour_group_no:{tournament_id}", "style": "primary"},
            ],
            [{"text": "❌ Cancel", "callback_data": f"tour_cancel:{tournament_id}", "style": "danger"}],
        ]
    }


def final_create_keyboard(tournament_id: int, *, group_review: bool = False) -> dict:
    if group_review:
        return {
            "inline_keyboard": [
                [
                    {"text": "✅ Yes, go ahead", "callback_data": f"tour_group_confirm:{tournament_id}", "style": "success"},
                    {"text": "🔄 No, change it", "callback_data": f"tour_group_change:{tournament_id}", "style": "primary"},
                ],
                [{"text": "❌ Cancel", "callback_data": f"tour_cancel:{tournament_id}", "style": "danger"}],
            ]
        }
    return {
        "inline_keyboard": [
            [{"text": "✅ Yes, create", "callback_data": f"tour_create:{tournament_id}", "style": "success"}],
            [{"text": "❌ Cancel", "callback_data": f"tour_cancel:{tournament_id}", "style": "danger"}],
        ]
    }


def teamown_keyboard(tournament_id: int, target_user_id: int) -> dict:
    rows = []
    row = []
    for code in IPL_TEAM_ORDER:
        b = {
            "text": code,
            "callback_data": f"tour_teamown_pick:{int(tournament_id)}:{int(target_user_id)}:{code}",
            "style": "primary",
        }
        emoji_id = team_button_emoji_html(code)
        if emoji_id:
            b["icon_custom_emoji_id"] = emoji_id
        row.append(b)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "❌ Cancel", "callback_data": f"tour_ui_cancel:{int(tournament_id)}", "style": "danger"}])
    return {"inline_keyboard": rows}


def teamown_confirm_keyboard(tournament_id: int, target_user_id: int, team_code: str) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "✅ Yes, assign", "callback_data": f"tour_teamown_confirm:{tournament_id}:{target_user_id}:{team_code}", "style": "success"}],
            [{"text": "❌ Cancel", "callback_data": f"tour_ui_cancel:{tournament_id}", "style": "danger"}],
        ]
    }


def remove_confirm_keyboard(tournament_id: int, target_user_id: int, team_code: str) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "✅ Yes, remove", "callback_data": f"tour_teamown_remove:{tournament_id}:{target_user_id}:{team_code}", "style": "success"}],
            [{"text": "❌ Cancel", "callback_data": f"tour_ui_cancel:{tournament_id}", "style": "danger"}],
        ]
    }


def render_pool_prompt() -> str:
    return (
        "<b>╭━━〔 📦 SET AUCTION POOL 〕━━╮</b>\n\n"
        "Use <code>/setpool IPL</code> and reply to this command with either a Telegram text message or a <code>.txt</code> file using exactly this syntax:\n\n"
        "<pre>"
        "[Pool 1: Marquee]\n"
        "Base: 2.0 Cr\n"
        "Virat Kohli\n"
        "Rohit Sharma\n"
        "Jasprit Bumrah\n"
        "Pat Cummins\n\n"
        "[Pool 2: Batsmen]\n"
        "Base: 1.0 Cr\n"
        "Travis Head\n"
        "Shubman Gill\n"
        "Rinku Singh"
        "</pre>\n\n"
        "<blockquote>"
        "• Pool tag and pool number are mandatory.\n"
        "• Base price accepts values such as <b>2.0 Cr</b>, <b>1.0 Cr</b>, <b>50 L</b>, <b>75 L</b>.\n"
        "• Players may come from the global player pool or Special Edition pool.\n"
        "• The bot validates every pool, line and player before saving anything."
        "</blockquote>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def parse_pool_text(raw: str) -> tuple[list[dict], list[dict]]:
    lines = str(raw or "").splitlines()
    pools: list[dict] = []
    errors: list[dict] = []
    current: dict | None = None
    expected_pool = 1
    seen_pool_nos: set[int] = set()
    seen_pool_names: set[str] = set()
    i = 0
    while i < len(lines):
        original = lines[i]
        line = original.strip()
        i += 1
        if not line:
            continue

        pm = _POOL_RE.match(line)
        if pm:
            if current is not None:
                if not current["player_lines"]:
                    errors.append({"pool": current["pool_no"], "line": i - 1, "detail": "Pool has no players."})
                pools.append(current)
            pool_no = int(pm.group(1))
            pool_name = pm.group(2).strip()
            if pool_no != expected_pool:
                errors.append({"pool": pool_no, "line": i, "detail": f"Expected Pool {expected_pool}, found Pool {pool_no}."})
            if pool_no in seen_pool_nos:
                errors.append({"pool": pool_no, "line": i, "detail": "Duplicate pool number."})
            if pool_name.casefold() in seen_pool_names:
                errors.append({"pool": pool_no, "line": i, "detail": "Duplicate pool name."})
            seen_pool_nos.add(pool_no)
            seen_pool_names.add(pool_name.casefold())
            current = {
                "pool_no": pool_no,
                "pool_name": pool_name,
                "base_price": None,
                "players": [],
                "player_lines": [],
            }
            expected_pool = max(expected_pool, pool_no + 1)
            continue

        if current is None:
            errors.append({"pool": "?", "line": i, "detail": "Content appeared before the first [Pool N: Name] header."})
            continue

        bm = _BASE_RE.match(line)
        if bm:
            amount = parse_amount(bm.group(1))
            if amount is None:
                errors.append({"pool": current["pool_no"], "line": i, "detail": f"Invalid base price: {line}"})
            else:
                current["base_price"] = amount
            continue

        current["player_lines"].append((i, line))

    if current is not None:
        if not current["player_lines"]:
            errors.append({"pool": current["pool_no"], "line": len(lines), "detail": "Pool has no players."})
        pools.append(current)

    for pool in pools:
        if pool["base_price"] is None:
            errors.append({"pool": pool["pool_no"], "line": 0, "detail": "Missing Base: line."})

    return pools, errors


async def validate_pool_players(pools: list[dict], errors: list[dict]) -> tuple[list[dict], list[dict], int, int]:
    seen: set[str] = set()
    success = 0
    failed = 0
    normalized: list[dict] = []
    pool_errors = list(errors)

    for pool in pools:
        clean_pool = {
            "pool_no": int(pool["pool_no"]),
            "pool_name": str(pool["pool_name"]),
            "base_price": int(pool["base_price"] or 0),
            "players": [],
        }
        for line_no, player_text in pool.get("player_lines", []):
            raw_name = str(player_text).strip()
            base = raw_name
            edition = None
            special_match = re.match(r"^(.+?)\s*\(([^()]+)\)\s*$", raw_name)
            if special_match:
                base = special_match.group(1).strip()
                edition = special_match.group(2).strip()

            if edition:
                row = await fetchrow(
                    """
                    SELECT * FROM special_edition_players
                    WHERE LOWER(name)=LOWER($1) AND LOWER(edition)=LOWER($2)
                    LIMIT 1;
                    """,
                    base, edition,
                )
                if not row:
                    pool_errors.append({"pool": pool["pool_no"], "line": line_no, "detail": f"Special Edition player not found: {raw_name}"})
                    failed += 1
                    continue
                player = dict(row)
                player["is_special"] = True
                player["special_player_id"] = int(player["special_player_id"])
                player["player_id"] = None
                identity = f"special:{player['special_player_id']}"
            else:
                global_row = await fetchrow(
                    "SELECT * FROM players WHERE LOWER(name)=LOWER($1) LIMIT 1;",
                    base,
                )
                if global_row:
                    # A plain player name is always acceptable. If the name
                    # exists in the global pool, use that canonical global
                    # card. Do not reject it merely because Special Edition
                    # cards with the same display name also exist.
                    player = dict(global_row)
                    player["is_special"] = False
                    player["special_player_id"] = None
                    identity = f"global:{int(player['player_id'])}"
                else:
                    # A plain name is also valid for a Special Edition player
                    # when there is no global card with that name. This keeps
                    # the auction pool format source-agnostic: pool authors
                    # can write the player name normally for either source.
                    special_row = await fetchrow(
                        """
                        SELECT *
                        FROM special_edition_players
                        WHERE LOWER(name)=LOWER($1)
                        ORDER BY special_player_id ASC
                        LIMIT 1;
                        """,
                        base,
                    )
                    if not special_row:
                        pool_errors.append({"pool": pool["pool_no"], "line": line_no, "detail": f"Player not found in global or Special Edition pool: {raw_name}"})
                        failed += 1
                        continue
                    player = dict(special_row)
                    player["is_special"] = True
                    player["special_player_id"] = int(player["special_player_id"])
                    player["player_id"] = None
                    identity = f"special:{player['special_player_id']}"

            if identity in seen:
                pool_errors.append({"pool": pool["pool_no"], "line": line_no, "detail": f"Duplicate auction player: {raw_name}"})
                failed += 1
                continue
            seen.add(identity)
            player["identity_key"] = identity
            player["edition"] = player.get("edition") if player.get("is_special") else None
            player["ovr"] = overall_rating(player.get("bat_level"), player.get("bowl_level"))
            player["country"] = player.get("country")
            player["role"] = player.get("role")
            clean_pool["players"].append(player)
            success += 1

        if clean_pool["players"]:
            normalized.append(clean_pool)

    return normalized, pool_errors, success, failed


async def public_group_info(group_id: int) -> tuple[dict | None, str | None]:
    try:
        chat = await app._call_bot_api("getChat", json_payload={"chat_id": int(group_id)})
    except Exception as exc:
        return None, f"Unable to access that group: {exc}"

    chat_type = str(chat.get("type") or "").lower()
    username = str(chat.get("username") or "").strip()
    if chat_type not in {"group", "supergroup"}:
        return None, "The host chat must be a Telegram group or supergroup."
    if not username:
        return None, "The host group must be public and have a Telegram username."

    try:
        me = await app.get_me()
        member = await app._call_bot_api(
            "getChatMember",
            json_payload={"chat_id": int(group_id), "user_id": int(me["id"])},
        )
    except Exception as exc:
        return None, f"I could not verify the bot's membership in that group: {exc}"

    status = str(member.get("status") or "").lower()
    if status == "creator":
        pass
    elif status == "administrator":
        missing = []
        # These permissions directly cover posting, pinning, and maintaining
        # the live tournament registration board. If Telegram explicitly
        # reports a permission as false, surface it to the host.
        for key, label in (
            ("can_manage_chat", "manage chat"),
            ("can_delete_messages", "delete messages"),
            ("can_pin_messages", "pin messages"),
        ):
            if key in member and member.get(key) is False:
                missing.append(label)
        if missing:
            return None, "Bot admin permissions missing: " + ", ".join(missing) + "."
    else:
        return None, "The bot is not an administrator in that group."

    return {
        "id": int(chat["id"]),
        "title": chat.get("title") or chat.get("first_name") or "Public Group",
        "username": username,
    }, None


async def create_registration_announcement(tournament_id: int) -> tuple[bool, str | None]:
    tournament = await get_tournament(tournament_id)
    if not tournament or not tournament["host_group_id"]:
        return True, None

    try:
        teams = await fetch_teams(tournament_id)
        sent = await app.send_message(
            int(tournament["host_group_id"]),
            render_group_announcement(tournament, teams),
            parse_mode="HTML",
            reply_markup=team_keyboard(
                tournament_id,
                teams,
                self_registration=bool(tournament["self_registration_enabled"]),
            ),
        )
        message_id = int(sent.get("message_id") if isinstance(sent, dict) else sent["message_id"])
        try:
            await app.pin_chat_message(int(tournament["host_group_id"]), message_id, disable_notification=True)
        except Exception as exc:
            return False, f"Tournament message was posted, but Telegram did not allow pinning it: {exc}"

        await update_tournament(
            tournament_id,
            group_registration_message_id=message_id,
        )
        return True, None
    except Exception as exc:
        return False, str(exc)


async def update_group_board(tournament_id: int):
    tournament = await get_tournament(tournament_id)
    if not tournament or not tournament["host_group_id"] or not tournament["group_registration_message_id"]:
        return
    teams = await fetch_teams(tournament_id)
    try:
        await app.edit_message_text(
            int(tournament["host_group_id"]),
            int(tournament["group_registration_message_id"]),
            render_group_announcement(tournament, teams),
            parse_mode="HTML",
            reply_markup=team_keyboard(
                tournament_id,
                teams,
                self_registration=bool(tournament["self_registration_enabled"]),
            ),
        )
    except Exception as exc:
        print(f"[auction_tournament] live team board edit failed: {exc!r}")


async def reserve_prize_player_on_create(tournament_id: int) -> tuple[bool, str | None]:
    """Move the optional prize player out of the creator's squad atomically."""
    async def _tx(conn):
        row = await conn.fetchrow(
            "SELECT creator_id, prize_player FROM auction_tournaments WHERE tournament_id=$1 FOR UPDATE;",
            int(tournament_id),
        )
        if not row:
            return False, "Tournament not found."
        prize = row["prize_player"]
        if prize is None:
            return True, None
        if isinstance(prize, str):
            import json
            prize = json.loads(prize)
        target_pid = int(prize.get("player_id") or 0)
        if not target_pid:
            return False, "The prize player record is invalid."

        squad_row = await conn.fetchrow(
            "SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;",
            int(row["creator_id"]),
        )
        if not squad_row:
            return False, "The creator no longer has a squad."
        squad = squad_row["squad"]
        if isinstance(squad, str):
            import json
            squad = json.loads(squad)
        squad = list(squad or [])
        new_squad = [p for p in squad if int((p or {}).get("player_id") or 0) != target_pid]
        if len(new_squad) == len(squad):
            return False, f"The prize player <b>{esc(prize.get('name'))}</b> is no longer in the creator's squad. Please change the prize player."

        await conn.execute(
            "UPDATE team_squads SET squad=$2::jsonb, updated_at=NOW() WHERE user_id=$1;",
            int(row["creator_id"]),
            json.dumps(new_squad, default=str),
        )
        return True, None

    return await transaction(_tx)


async def create_tournament_final(tournament_id: int) -> tuple[bool, str | None]:
    ok, error = await reserve_prize_player_on_create(tournament_id)
    if not ok:
        return False, error

    await update_tournament(tournament_id, status="created")
    return await create_registration_announcement(tournament_id)


async def handle_tournament_reply(message: dict) -> bool:
    reply = message.get("reply_to_message") or {}
    reply_id = int(reply.get("message_id") or 0)
    if reply_id <= 0:
        return False
    sender = message.get("from") or {}
    user_id = int(sender.get("id") or 0)
    draft = await get_active_draft(user_id)
    if not draft:
        return False
    if int(draft.get("prompt_message_id") or 0) != reply_id:
        return False

    status = str(draft.get("status") or "")
    text = str(message.get("text") or "").strip()
    chat_id = int((message.get("chat") or {}).get("id") or draft.get("creation_chat_id") or 0)
    draft_id = int(draft["tournament_id"])

    if status == "await_prize":
        parsed, error = parse_prize_input(text)
        if error:
            try:
                await app.edit_message_text(
                    chat_id, reply_id, render_prize_prompt(error),
                    parse_mode="HTML", reply_markup=cancel_keyboard(draft_id),
                )
            except Exception as exc:
                print(f"[auction_tournament] prize validation prompt edit failed: {exc!r}")
                sent = await app.send_message(
                    chat_id, render_prize_prompt(error),
                    parse_mode="HTML", reply_markup=cancel_keyboard(draft_id),
                )
                await update_tournament(draft_id, prompt_message_id=int(sent["message_id"]))
            return True

        player, player_error = await resolve_owned_prize_player(user_id, parsed["player_query"])
        if player_error:
            try:
                await app.edit_message_text(
                    chat_id, reply_id, render_prize_prompt(player_error),
                    parse_mode="HTML", reply_markup=cancel_keyboard(draft_id),
                )
            except Exception as exc:
                print(f"[auction_tournament] prize-player prompt edit failed: {exc!r}")
                sent = await app.send_message(
                    chat_id, render_prize_prompt(player_error),
                    parse_mode="HTML", reply_markup=cancel_keyboard(draft_id),
                )
                await update_tournament(draft_id, prompt_message_id=int(sent["message_id"]))
            return True

        parsed["player"] = player
        breakdown = build_prize_breakdown(parsed["coins"], parsed["rubies"], player)

        # Valid input advances the state atomically first. The old prompt and
        # the creator's reply are then removed, and a brand-new confirmation
        # message becomes the next reply target. This avoids edits against a
        # message that Telegram may have stale reply metadata for.
        await update_tournament(
            draft_id,
            status="confirm_prize",
            prize_coins=int(parsed["coins"]),
            prize_rubies=int(parsed["rubies"]),
            prize_player=player,
            prize_breakdown=breakdown,
            prompt_message_id=0,
        )
        try:
            await app.delete_message(chat_id, reply_id)
        except Exception as exc:
            print(f"[auction_tournament] prize prompt delete failed: {exc!r}")
        submitted_id = int(message.get("message_id") or 0)
        if submitted_id:
            try:
                await app.delete_message(chat_id, submitted_id)
            except Exception as exc:
                print(f"[auction_tournament] prize reply delete failed: {exc!r}")

        sent = await app.send_message(
            chat_id,
            render_prize_confirmation(parsed),
            parse_mode="HTML",
            reply_markup=confirm_prize_keyboard(draft_id),
        )
        await update_tournament(draft_id, prompt_message_id=int(sent["message_id"]))
        return True

    return False


def prize_command_text(tournament: dict, pool_players: list[dict]) -> str:
    player = tournament.get("prize_player")
    if isinstance(player, str):
        try:
            player = json.loads(player)
        except Exception:
            player = None

    # Always rebuild from the stored total pool so tournaments created with an
    # older equal-split implementation are displayed using the corrected
    # structure too.
    breakdown = build_prize_breakdown(
        int(tournament.get("prize_coins") or 0),
        int(tournament.get("prize_rubies") or 0),
        player if isinstance(player, dict) else None,
    )

    lines = [
        "<b>╭━━〔 🏆 IPL PRIZE POOL 〕━━╮</b>",
        "",
        f"🏆 <b>Tournament</b> : {esc(tournament.get('tournament_name') or 'Indian Premier League')}",
        f"🪙 <b>Total Coins</b> : {int(tournament.get('prize_coins') or 0):,}",
        f"💎 <b>Total Rubies</b>: {int(tournament.get('prize_rubies') or 0):,}",
        "",
        "<blockquote>",
        "<b>11-SECTION PRIZE DISTRIBUTION</b>",
    ]

    for item in breakdown:
        lines.append(
            f"{int(item.get('part') or 0)}. {esc(item.get('label'))} "
            f"({int(item.get('percentage') or 0)}%) → "
            f"🪙 {int(item.get('coins') or 0):,} • 💎 {int(item.get('rubies') or 0):,}"
        )

    lines.extend([
        "</blockquote>",
        "",
        "<blockquote>",
        "<b>🎴 PLAYER CARD PRIZE</b>",
    ])
    card_item = breakdown[-1].get("player") if breakdown else None
    if card_item:
        lines.append(
            f"🎴 {esc(card_item.get('name'))}"
            + (f" ({esc(card_item.get('edition'))})" if card_item.get('edition') else "")
            + f" • OVR {int(card_item.get('ovr') or 0)}"
        )
    else:
        lines.append("No optional player-card prize added.")
    lines.append("</blockquote>")

    lines += [
        "",
        "<blockquote expandable><b>🎴 AUCTION PLAYER POOL</b>",
    ]
    if pool_players:
        current_pool = None
        for row in pool_players:
            if current_pool != row["pool_no"]:
                current_pool = row["pool_no"]
                lines.append(
                    f"\n<b>Pool {int(row['pool_no'])}: {esc(row['pool_name'])}</b> "
                    f"• Base {int(row['base_price']):,}"
                )
            special = " • Special Edition" if row["is_special"] else ""
            edition = f" ({esc(row['edition'])})" if row.get("edition") else ""
            lines.append(
                f"├ {esc(row['player_name'])}{edition} • OVR {int(row['ovr'] or 0)}{special}"
            )
    else:
        lines.append("No auction players have been saved.")
    lines += [
        "</blockquote>",
        "",
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>",
    ]
    return "\n".join(lines)
