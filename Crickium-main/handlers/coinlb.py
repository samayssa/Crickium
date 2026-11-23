from __future__ import annotations

import html

from handlers.registry import register
from app import app
from database.query import fetch


@register("coinlb")
async def coinlb_command(message):
    """Show the top 10 coin holders, with expandable wallet details."""
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    if not chat_id:
        return

    rows = await fetch(
        """SELECT user_id, username, first_name,
                  COALESCE(balance, 0) AS coins,
                  COALESCE(rubies, 0) AS rubies,
                  COALESCE(sigils, 0) AS sigils
           FROM users
           WHERE COALESCE(balance, 0) > 0
           ORDER BY COALESCE(balance, 0) DESC, user_id ASC
           LIMIT 10;"""
    )

    if not rows:
        await app.send_message(chat_id, "🪙 No coin holders to display yet.")
        return

    def _display_name(row) -> str:
        first_name = str(row.get("first_name") or "").strip()
        username = str(row.get("username") or "").strip().lstrip("@")
        if first_name and username:
            return f"{first_name} (@{username})"
        return first_name or (f"@{username}" if username else f"Player {int(row['user_id'])}")

    medals = ["🥇", "🥈", "🥉"]
    blocks = [
        "<h2>🪙 CRICKIUM COIN LEADERBOARD</h2>",
        "<p>Top 10 players ranked by their current coin balance. Tap a player to expand their complete wallet.</p>",
        "<hr/>",
    ]
    for index, row in enumerate(rows, start=1):
        user_id = int(row["user_id"])
        name = html.escape(_display_name(row))
        coin_balance = int(row["coins"] or 0)
        rubies = int(row["rubies"] or 0)
        sigils = int(row["sigils"] or 0)
        medal = medals[index - 1] if index <= 3 else f"#{index}"
        blocks.append(
            "<details>"
            f"<summary>{medal} <b>{name}</b> — <b>{coin_balance:,} coins</b></summary>"
            f"<p>🆔 <b>User ID:</b> <code>{user_id}</code></p>"
            f"<p>🪙 <b>Coins:</b> {coin_balance:,}</p>"
            f"<p>💎 <b>Rubies:</b> {rubies:,}</p>"
            f"<p>🔮 <b>Sigils (SG):</b> {sigils:,}</p>"
            "</details>"
        )
    blocks.append("<footer>Balances are live values from the Crickium wallet database.</footer>")
    rich_html = "\n".join(blocks)

    try:
        await app.send_rich_message(
            chat_id,
            rich_html,
            message_thread_id=message.get("message_thread_id"),
        )
    except Exception as exc:
        # Keep the command usable with clients/servers that do not yet support
        # Bot API Rich Messages. This fallback is not expandable.
        print(f"[coinlb] Rich Message delivery failed; using plain HTML fallback: {exc!r}")
        lines = ["🪙 <b>CRICKIUM COIN LEADERBOARD</b>", "Top 10 players by current coin balance", ""]
        for index, row in enumerate(rows, start=1):
            name = html.escape(_display_name(row))
            lines.append(
                f"<b>#{index} {name}</b> — 🪙 {int(row['coins'] or 0):,}"
                f" | 💎 {int(row['rubies'] or 0):,}"
                f" | 🔮 SG {int(row['sigils'] or 0):,}"
            )
        await app.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
