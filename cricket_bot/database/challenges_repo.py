from database.query import execute, fetchrow


async def create_challenge(chat_id, challenger_id, challenger_username, challenger_name,
                            opponent_id, opponent_username, opponent_name,
                            format_="T20", expires_in_seconds=60):
    row = await fetchrow(
        """
        INSERT INTO match_challenges (
            chat_id, challenger_id, challenger_username, challenger_name,
            opponent_id, opponent_username, opponent_name,
            format, status, expires_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending', NOW() + ($9 || ' seconds')::interval)
        RETURNING *;
        """,
        chat_id, challenger_id, challenger_username, challenger_name,
        opponent_id, opponent_username, opponent_name,
        format_, str(expires_in_seconds),
    )
    return dict(row)


async def set_message_id(challenge_id, message_id):
    await execute(
        "UPDATE match_challenges SET message_id = $1 WHERE challenge_id = $2;",
        message_id, challenge_id,
    )


async def get_challenge(challenge_id):
    row = await fetchrow("SELECT * FROM match_challenges WHERE challenge_id = $1;", challenge_id)
    return dict(row) if row else None


async def update_status(challenge_id, status):
    await execute(
        "UPDATE match_challenges SET status = $1 WHERE challenge_id = $2;",
        status, challenge_id,
    )


async def set_opponent_id(challenge_id, opponent_id):
    await execute(
        "UPDATE match_challenges SET opponent_id = $1 WHERE challenge_id = $2;",
        opponent_id, challenge_id,
    )


async def set_toss(challenge_id, toss_winner_id, toss_call, toss_result):
    await execute(
        """
        UPDATE match_challenges
        SET toss_winner_id = $1, toss_call = $2, toss_result = $3, status = 'toss_done'
        WHERE challenge_id = $4;
        """,
        toss_winner_id, toss_call, toss_result, challenge_id,
    )


async def set_decision(challenge_id, decision):
    await execute(
        "UPDATE match_challenges SET decision = $1, status = 'in_progress' WHERE challenge_id = $2;",
        decision, challenge_id,
    )
