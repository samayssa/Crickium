from database.query import execute, fetchrow, fetchval


async def get_last_claim(user_id: int):
    row = await fetchrow(
        "SELECT * FROM player_claims WHERE user_id = $1 ORDER BY claimed_at DESC LIMIT 1;",
        user_id,
    )
    return dict(row) if row else None


async def seconds_since_last_claim(user_id: int) -> float | None:
    row = await fetchrow(
        "SELECT EXTRACT(EPOCH FROM (NOW() - claimed_at)) AS elapsed FROM player_claims "
        "WHERE user_id = $1 ORDER BY claimed_at DESC LIMIT 1;",
        user_id,
    )
    if not row:
        return None
    return float(row["elapsed"])


async def get_random_player():
    row = await fetchrow("SELECT * FROM players ORDER BY random() LIMIT 1;")
    return dict(row) if row else None


async def create_claim(user_id: int, player_id: int):
    row = await fetchrow(
        """
        INSERT INTO player_claims (user_id, player_id, status)
        VALUES ($1, $2, 'pending')
        RETURNING *;
        """,
        user_id, player_id,
    )
    return dict(row)


async def get_claim(claim_id: int):
    row = await fetchrow("SELECT * FROM player_claims WHERE claim_id = $1;", claim_id)
    return dict(row) if row else None


async def set_claim_status(claim_id: int, status: str):
    await execute("UPDATE player_claims SET status = $1 WHERE claim_id = $2;", status, claim_id)


async def count_retained(user_id: int) -> int:
    return await fetchval(
        "SELECT COUNT(*) FROM player_claims WHERE user_id = $1 AND status = 'retained';",
        user_id,
    )
