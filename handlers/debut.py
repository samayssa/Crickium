print("debut.py loaded")

import html
import json

from handlers.registry import register
from app import app
from database.query import execute, fetchrow
from database.player_user_stats_repo import reset_player_user_stats
from utils.randomiser import generate_debut_xi
from database.lineups_repo import save_lineup_ids


def _level(player: dict) -> int:
    return max(int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0))


def _line(player: dict, number: int, icon: str, captain: bool = False) -> str:
    name = html.escape(str(player.get("name") or "Player"))
    level = _level(player)
    country = html.escape(str(player.get("country") or ""))
    flag = ""
    from utils.country_flags import flag_for
    flag = flag_for(country)
    suffix = " 🧢" if captain else ""
    return f"├ {number}. {name} • {level} {flag} {icon}{suffix}"


def _render_debut_message(squad: list[dict], first_name: str, user_id: int, is_new: bool) -> str:
    # The saved debut XI is already a valid 5 batsmen / 3 all-rounders / 3
    # bowlers structure. One or two batsmen carry is_wicketkeeper=True.
    batsmen = [p for p in squad if p.get("role") == "Batsman" and not p.get("is_wicketkeeper")]
    keepers = [p for p in squad if p.get("role") == "Wicketkeeper" or (p.get("role") == "Batsman" and p.get("is_wicketkeeper"))]
    allrounders = [p for p in squad if p.get("role") == "AllRounder"]
    bowlers = [p for p in squad if p.get("role") == "Bowler"]

    lines = [
        "╭━━━〔 🏏 CRICKIUM DEBUT 〕━━━╮",
        "",
        f"➤ 👤 <b>Player:</b> {html.escape(str(first_name or 'Player'))}",
        f"➤ 🆔 <b>User ID:</b> {int(user_id)}",
        "",
        f"🎉 <b>{'Your first Playing XI has been generated!' if is_new else 'Your Playing XI is already set!'}</b>",
        "",
        "<blockquote>",
        "<b>🏏 Batsmen</b>",
    ]
    for i, player in enumerate(batsmen, 1):
        lines.append(_line(player, i, "🏏", False))
    lines.append("</blockquote>")

    lines += ["", "<blockquote>", "<b>🧤 Wicket-Keeper</b>"]
    for i, player in enumerate(keepers, 1):
        prefix = "╰" if i == len(keepers) else "├"
        name = html.escape(str(player.get("name") or "Player"))
        level = _level(player)
        from utils.country_flags import flag_for
        flag = flag_for(player.get("country"))
        lines.append(f"{prefix} {len(batsmen) + i}. {name} • {level} {flag} 🧤")
    lines.append("</blockquote>")

    lines += ["", "<blockquote>", "<b>🔄 All-Rounders</b>"]
    start = len(batsmen) + len(keepers) + 1
    for i, player in enumerate(allrounders, start):
        lines.append(_line(player, i, "🔄", False))
    lines.append("</blockquote>")

    lines += ["", "<blockquote>", "<b>⚡ Bowlers</b>"]
    start = len(batsmen) + len(keepers) + len(allrounders) + 1
    for idx, player in enumerate(bowlers, start):
        prefix = "╰" if idx == 11 else "├"
        name = html.escape(str(player.get("name") or "Player"))
        level = _level(player)
        from utils.country_flags import flag_for
        flag = flag_for(player.get("country"))
        suffix = " 🧢" if idx == 11 else ""
        lines.append(f"{prefix} {idx}. {name} • {level} {flag} ⚡{suffix}")
    lines.append("</blockquote>")

    lines += [
        "",
        "🏏 <b>Good luck on your Crickium journey!</b>",
        "",
        "╰━━━━━━━━━━━━━━━━━━╯",
    ]
    return "\n".join(lines)


@register("debut")
async def debut_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")
    first_name = from_user.get("first_name") or "Player"

    print(f"[debut] Command invoked by user_id={user_id} username=@{from_user.get('username')}")

    await execute(
        """
        INSERT INTO users (user_id, username, first_name, last_seen_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET username = EXCLUDED.username, first_name = EXCLUDED.first_name, last_seen_at = NOW();
        """,
        user_id, from_user.get("username"), first_name,
    )

    existing = await fetchrow("SELECT squad FROM team_squads WHERE user_id = $1;", user_id)
    if existing:
        squad = existing["squad"]
        if isinstance(squad, str):
            squad = json.loads(squad)
        else:
            squad = json.loads(json.dumps(squad, default=str))
        await app.send_message(
            chat_id,
            _render_debut_message(squad, first_name, user_id, is_new=False),
            parse_mode="HTML",
        )
        return

    try:
        squad = await generate_debut_xi()
    except Exception as exc:
        print(f"[debut] Failed to generate debut XI: {exc!r}")
        await app.send_message(
            chat_id,
            "⚠️ I couldn't generate a valid debut Playing XI from the current player database. Please ask the bot admin to check the uploaded player roster.",
        )
        return

    for player in squad:
        await reset_player_user_stats(user_id, int(player["player_id"]))

    squad_json = json.dumps(squad, default=str)
    await execute(
        """
        INSERT INTO team_squads (user_id, squad, updated_at)
        VALUES ($1, $2::jsonb, NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET squad = EXCLUDED.squad, updated_at = NOW();
        """,
        user_id, squad_json,
    )

    await save_lineup_ids(user_id, [int(p["player_id"]) for p in squad[:11]])
    print(f"[debut] Debut completed and initial Playing XI saved for user_id={user_id}")

    await app.send_message(
        chat_id,
        _render_debut_message(squad, first_name, user_id, is_new=True),
        parse_mode="HTML",
    )
