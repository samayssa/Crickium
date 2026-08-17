from __future__ import annotations

print("plstats.py loaded")

import html

from handlers.registry import register
from app import app
from database.players_repo import get_player
from database.squads_repo import get_team_squad
from database.player_user_stats_repo import get_player_user_stats
from utils.country_flags import flag_for
from utils.rarity import get_rarity
from services.card_provider import get_player_card_bytes
from services.player_card import overall_rating


def _escape(value: object | None) -> str:
    return html.escape("" if value is None else str(value))


def _parse_arg(text: str) -> str | None:
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    return arg or None


def _num(value) -> str:
    """Formats a NUMERIC(8,2) DB value without a pointless '.00'."""
    try:
        f = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    return f"{f:.0f}" if f == int(f) else f"{f:.2f}"


def _plstats_text(player: dict, stats: dict) -> str:
    name = _escape(player.get("name") or "Unknown")
    flag = flag_for(player.get("country"))
    bat_level = int(player.get("bat_level") or 0)
    bowl_level = int(player.get("bowl_level") or 0)
    rarity = _escape(get_rarity(overall_rating(bat_level, bowl_level)))

    bat_matches = int(stats.get("bat_matches") or 0)
    bat_innings = int(stats.get("bat_innings") or 0)
    runs = int(stats.get("runs") or 0)
    fifties = int(stats.get("fifties") or 0)
    centuries = int(stats.get("centuries") or 0)
    bat_balls = int(stats.get("bat_balls") or 0)
    dismissals = int(stats.get("dismissals") or 0)
    bat_average = (runs / dismissals) if dismissals else (0.0 if runs == 0 else float(runs))
    strike_rate = (runs / bat_balls * 100.0) if bat_balls else 0.0

    bowl_matches = int(stats.get("bowl_matches") or 0)
    bowl_innings = int(stats.get("bowl_innings") or 0)
    wickets = int(stats.get("wickets") or 0)
    three_wickets = int(stats.get("three_wickets") or 0)
    five_wickets = int(stats.get("five_wickets") or 0)
    bowl_balls = int(stats.get("bowl_balls") or 0)
    bowl_runs = int(stats.get("bowl_runs") or 0)
    bowl_average = (bowl_runs / wickets) if wickets else 0.0
    economy = (bowl_runs / bowl_balls * 6.0) if bowl_balls else 0.0

    def fmt(value: float) -> str:
        return str(int(value)) if value == int(value) else f"{value:.2f}"

    return (
        "<b>╭━━〔 PLAYER STATS 〕━━╮</b>\n\n"
        f"<blockquote>👤 <b>{name}</b>  {flag}</blockquote>\n\n"
        f"<blockquote>🏅 <b>Rarity:</b> {rarity}</blockquote>\n\n"
        f"<blockquote>⚡ <b>Level:</b> ➤ {overall_rating(bat_level, bowl_level)}</blockquote>\n\n"
        f"<blockquote expandable><b>🏏 BATTING OVERVIEW LV {bat_level}</b>\n\n"
        f"├ 🎮 Matches       : {bat_matches}\n"
        f"├ 🏏 Innings       : {bat_innings}\n"
        f"├ 🔥 Runs          : {runs}\n"
        f"├ 💯 50s / 100s    : {fifties} / {centuries}\n"
        f"├ 📊 Average       : {fmt(bat_average)}\n"
        f"╰ ⚡ Strike Rate   : {fmt(strike_rate)}\n"
        "</blockquote>\n"
        "<b>════════════════════</b>\n\n"
        f"<blockquote expandable><b>🎯 BOWLING OVERVIEW LV {bowl_level}</b>\n\n"
        f"├ 🎮 Matches       : {bowl_matches}\n"
        f"├ 🎯 Innings       : {bowl_innings}\n"
        f"├ 🔥 Wickets       : {wickets}\n"
        f"├ 🏅 3W / 5W       : {three_wickets} / {five_wickets}\n"
        f"├ 📊 Average       : {fmt(bowl_average)}\n"
        f"╰ ⚡ Economy        : {fmt(economy)}\n"
        "</blockquote>\n"
        "<b>╰━━━━━━━━━━━━━━━━━━╯</b>"
    )


@register("plstats")
async def plstats_command(message):
    chat_id = message["chat"]["id"]
    user_id = (message.get("from") or {}).get("id")

    print(f"[plstats] /plstats invoked by user_id={user_id}")

    name = _parse_arg(message.get("text", ""))
    if not name:
        await app.send_message(
            chat_id,
            "<b>⚠️ Please tell me which player to look up.</b>\nUsage: <code>/plstats Virat Kohli</code>",
            parse_mode="HTML",
        )
        return

    player = await get_player(name)
    if not player:
        await app.send_message(
            chat_id,
            f"⚠️ No player named <b>{_escape(name)}</b> found. Check the spelling, "
            f"or upload them first with /upload_pl.",
            parse_mode="HTML",
        )
        return

    squad = await get_team_squad(user_id) or []
    owned = any(int(p.get("player_id") or 0) == int(player["player_id"]) for p in squad)
    if not owned:
        await app.send_message(
            chat_id,
            f"⚠️ <b>{_escape(player['name'])}</b> is not currently in your squad.",
            parse_mode="HTML",
        )
        return

    stats = await get_player_user_stats(user_id, int(player["player_id"]))
    text = _plstats_text(player, stats)

    try:
        image_bytes, _is_custom = await get_player_card_bytes(player)
        await app.send_photo(chat_id, photo=image_bytes, caption=text, parse_mode="HTML")
    except Exception as exc:
        print(f"[plstats] Card image failed ({exc!r}), falling back to a text-only message.")
        await app.send_message(chat_id, text, parse_mode="HTML")

    print(f"[plstats] user_id={user_id} looked up stats for player_id={player['player_id']} ({player['name']})")
