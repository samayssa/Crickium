from __future__ import annotations

print("mybank.py loaded")

from handlers.registry import register
from app import app
from database.query import execute, fetchrow
from utils.mentions import mention_html
from utils.PremiumEmoji import coins_emoji_html, rubies_emoji_html, sigil_emoji_html


@register("mybank")
async def mybank_command(message):
    chat_id = int(message["chat"]["id"])
    from_user = message.get("from", {})
    user_id = int(from_user.get("id") or 0)
    username = from_user.get("username")
    first_name = from_user.get("first_name")

    await execute(
        """
        INSERT INTO users (user_id, username, first_name, last_seen_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (user_id) DO UPDATE
        SET username = EXCLUDED.username, first_name = EXCLUDED.first_name, last_seen_at = NOW();
        """,
        user_id, username, first_name,
    )

    row = await fetchrow(
        """
        SELECT
            balance,
            total_spent,
            rubies,
            total_rubies_spent,
            sigils,
            total_sigils_spent
        FROM users
        WHERE user_id = $1;
        """,
        user_id,
    )
    balance = int(row["balance"] or 0) if row else 0
    total_spent = int(row["total_spent"] or 0) if row else 0
    rubies = int(row["rubies"] or 0) if row else 0
    total_rubies_spent = int(row["total_rubies_spent"] or 0) if row else 0
    sigils = int(row["sigils"] or 0) if row else 0
    total_sigils_spent = int(row["total_sigils_spent"] or 0) if row else 0

    owner_display = mention_html(user_id, username, first_name)
    coins = coins_emoji_html()
    rubies_emoji = rubies_emoji_html()
    sigils_emoji = sigil_emoji_html()

    text = (
        "<b>╭━━━〔 🏦 MY BANK 〕━━━╮</b>\n\n"
        f"👤 <b>{owner_display}</b>\n\n"
        "<blockquote>\n"
        f"{coins} <b>COINS</b>\n"
        f"├ Balance : <b>{balance:,}</b>\n"
        f"╰ Spent   : <b>{total_spent:,}</b>\n"
        "\n"
        f"{rubies_emoji} <b>RUBIES</b>\n"
        f"├ Balance : <b>{rubies:,}</b>\n"
        f"╰ Spent   : <b>{total_rubies_spent:,}</b>\n"
        "\n"
        f"{sigils_emoji} <b>SIGILS</b>\n"
        f"├ Balance : <b>{sigils:,}</b>\n"
        f"╰ Spent   : <b>{total_sigils_spent:,}</b>\n"
        "</blockquote>\n\n"
        "<blockquote><i>All balances and spending are recorded from valid bot transactions.</i></blockquote>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )

    await app.send_message(chat_id, text, parse_mode="HTML")
