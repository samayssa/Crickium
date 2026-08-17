print("mybank.py loaded")

from handlers.registry import register
from app import app
from database.query import execute, fetchrow
from utils.mentions import mention


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

    row = await fetchrow("SELECT balance, total_spent FROM users WHERE user_id = $1;", user_id)
    balance = row["balance"] if row else 0
    total_spent = row["total_spent"] if row else 0

    owner_display = mention(user_id, username, first_name)

    text = (
        "*💰 Your Current Bank Balance*\n"
        "\n"
        f"*👤 Account Owner:* {owner_display}\n"
        f"*💵 Balance:* {balance}\n"
        f"*💸 Total Spent:* {total_spent}\n"
        "*🪙 Rubi*"
    )

    await app.send_message(chat_id, text, parse_mode="Markdown")
    print(f"[mybank] Sent bank statement to user_id={user_id}: balance={balance}, total_spent={total_spent}")
