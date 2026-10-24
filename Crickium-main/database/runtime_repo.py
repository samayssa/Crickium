"""Persistent bot runtime state used to keep the Telegram authorization session
across stateless Railway deployments/restarts.

Only the bot's own Telegram session string is stored here. No gameplay data is
kept in this table.
"""

from database.query import execute, fetchval

BOT_SESSION_KEY = "telegram_bot_session"


async def get_bot_session() -> str | None:
    return await fetchval(
        "SELECT state_value FROM bot_runtime_state WHERE state_key = $1 LIMIT 1;",
        BOT_SESSION_KEY,
    )


async def save_bot_session(session_string: str) -> None:
    await execute(
        """
        INSERT INTO bot_runtime_state(state_key, state_value, updated_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (state_key)
        DO UPDATE SET state_value = EXCLUDED.state_value,
                      updated_at = NOW();
        """,
        BOT_SESSION_KEY,
        session_string,
    )


async def clear_bot_session() -> None:
    await execute(
        "DELETE FROM bot_runtime_state WHERE state_key = $1;",
        BOT_SESSION_KEY,
    )
