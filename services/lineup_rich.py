import html

from app import app
from services.card_identity import display_card_name
from services.player_card import overall_rating
from utils.style import batting_style_text, bowling_style_text

ROLE_EMOJI = {"batsman": "🏏", "bowler": "⚡", "allrounder": "🔄", "wicketkeeper": "🧤"}
ROLE_LABEL = {"batsman": "Batsman", "bowler": "Bowler", "allrounder": "All-Rounder", "wicketkeeper": "Wicketkeeper"}
_KEEPER_ALIASES = {"wicketkeeper", "wicket-keeper", "wicket keeper", "wk", "keeper"}


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def role_key(player: dict) -> str:
    raw = str(player.get("role") or "").strip().lower().replace("_", "").replace(" ", "")
    if raw in {alias.replace(" ", "") for alias in _KEEPER_ALIASES}:
        return "wicketkeeper"
    if raw in {"allrounder", "all-rounder"}:
        return "allrounder"
    return raw if raw in ROLE_EMOJI else "batsman"


def player_ovr(player: dict) -> int:
    return int(overall_rating(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0)))


def average_ovr(players: list[dict]) -> str:
    if not players:
        return "0"
    value = sum(player_ovr(p) for p in players) / len(players)
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _player_block(player: dict) -> str:
    key = role_key(player)
    bat_style = batting_style_text(player.get("batting_hand"))
    bowl_style = bowling_style_text(player.get("bowling_hand"))
    bat_line = f"{_esc(bat_style)} · " if bat_style != "Unknown" else ""
    bowl_line = f"{_esc(bowl_style)} · " if bowl_style != "Unknown" else ""
    return (
        "<details>"
        f"<summary>{ROLE_EMOJI[key]} <b>{_esc(display_card_name(player))}</b> · {player_ovr(player)}</summary>"
        f"<p>🎽 <b>Role:</b> {ROLE_LABEL[key]}</p>"
        f"<p>🏏 <b>Batting:</b> {bat_line}Bat Lv. {int(player.get('bat_level') or 0)}</p>"
        f"<p>🥎 <b>Bowling:</b> {bowl_line}Ball Lv. {int(player.get('bowl_level') or 0)}</p>"
        "</details>"
    )


def build_rich(team_name: str, owner_name: str, xi: list[dict], bench: list[dict] | None = None) -> str:
    parts = [
        f"<h2>{_esc(team_name)}</h2>",
        f"<p>👤 <b>{_esc(owner_name)}</b> · 📋 Playing XI · ⭐ Avg OVR <b>{average_ovr(xi)}</b></p>",
        "<hr/>",
    ]
    parts.extend(_player_block(p) for p in xi)
    if bench:
        parts.append("<hr/>")
        parts.append("<p>🪑 <b>Bench</b></p>")
        parts.extend(_player_block(p) for p in bench)
    parts.append("<footer>🔒 Playing XI locked · Tap a player to see details</footer>")
    return "\n".join(parts)


def build_fallback(team_name: str, owner_html: str, xi: list[dict], bench: list[dict] | None = None) -> str:
    lines = [
        f"<b>📋 {_esc(team_name)} — PLAYING XI</b>",
        f"👤 {owner_html} · ⭐ Avg OVR <b>{average_ovr(xi)}</b>",
        "",
        "<blockquote>" + "\n".join(
            f"{i + 1}. {ROLE_EMOJI[role_key(p)]} {_esc(display_card_name(p))} · {player_ovr(p)}" for i, p in enumerate(xi)
        ) + "</blockquote>",
    ]
    if bench:
        lines.append("")
        lines.append("<blockquote><b>🪑 Bench</b>\n" + "\n".join(
            f"• {ROLE_EMOJI[role_key(p)]} {_esc(display_card_name(p))} · {player_ovr(p)}" for p in bench
        ) + "</blockquote>")
    lines.append("\n🔒 <b>Playing XI Locked</b>")
    return "\n".join(lines)


async def send_lineup(chat_id, rich_html: str, fallback_html: str, reply_markup=None):
    try:
        return await app.send_rich_message(chat_id, rich_html, reply_markup=reply_markup)
    except Exception as exc:
        print(f"[lineup_rich] rich message failed, using HTML fallback: {exc!r}")
        return await app.send_message(chat_id, fallback_html, parse_mode="HTML", reply_markup=reply_markup)
