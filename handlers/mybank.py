print("mybank.py loaded")

from handlers.registry import register
from app import app
from database.query import execute, fetchrow
from utils.mentions import mention_html


@register("mybank")
async def mybank_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")
    username = from_user.get("username")
    first_name = from_user.get("first_name")

    print(f"[mybank] /mybank invoked by user_id={user_id}")

    await execute(
        """
        INSERT INTO users (user_id, username, first_name, last_seen_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, last_seen_at = NOW();
        """,
        user_id, username, first_name,
    )

    row = await fetchrow("SELECT balance, total_spent, rubies FROM users WHERE user_id = $1;", user_id)
    balance = int(row["balance"] or 0) if row else 0
    rubies = int(row["rubies"] or 0) if row else 0
    total_spent = int(row["total_spent"] or 0) if row else 0
    total_earned = balance + total_spent

    owner_display = mention_html(user_id, username, first_name)

    text = (
        "<b>╭━━━〔 🏦 MY BANK 〕━━━╮</b>\n"
        "\n"
        f"👤 <b>{owner_display}</b>\n"
        "\n"
        "<blockquote>\n"
        f"<b>🪙 Coins   • {balance:,}</b>\n"
        f"<b>💎 Rubies  • {rubies:,}</b>\n"
        "</blockquote>\n"
        "\n"
        "<blockquote>\n"
        f"<b>📈 Earned  • 🪙 {total_earned:,}</b>\n"
        f"<b>📉 Spent   • 🪙 {total_spent:,}</b>\n"
        "</blockquote>\n"
        "\n"
        "💳 <b>Account Status:</b> 🟢 Active\n"
        "\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )

    await app.send_message(chat_id, text, parse_mode="HTML")
    print(f"[mybank] Sent bank statement to user_id={user_id}: balance={balance}, total_spent={total_spent}")
