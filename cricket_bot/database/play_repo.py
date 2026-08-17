from database.query import execute, fetchrow

# Statuses a match can sit in while it is still "in progress" - i.e. it
# occupies the one-match-per-group slot and its two players are considered
# "in a game". Terminal statuses (declined/completed/ended) are excluded so
# a finished match never blocks a new /play.
ACTIVE_STATUSES = ("pending", "accepted", "pitch_selected", "toss_done", "lineup")


async def create_match(chat_id, challenger_id, challenger_username, challenger_name,
                        opponent_id, opponent_username, opponent_name):
    row = await fetchrow(
        """
        INSERT INTO play_matches (
            chat_id, challenger_id, challenger_username, challenger_name,
            opponent_id, opponent_username, opponent_name, status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending')
        RETURNING *;
        """,
        chat_id, challenger_id, challenger_username, challenger_name,
        opponent_id, opponent_username, opponent_name,
    )
    return dict(row)


async def get_match(match_id):
    row = await fetchrow("SELECT * FROM play_matches WHERE match_id = $1;", match_id)
    return dict(row) if row else None


async def get_active_match_in_chat(chat_id):
    """Returns the currently in-progress match for this chat, if any.
    Used to enforce: only one match can run in a group at a time."""
    row = await fetchrow(
        """
        SELECT * FROM play_matches
        WHERE chat_id = $1 AND status = ANY($2::text[])
        ORDER BY match_id DESC LIMIT 1;
        """,
        chat_id, list(ACTIVE_STATUSES),
    )
    return dict(row) if row else None


async def get_active_match_for_user(user_id):
    """Returns the currently in-progress match a user is part of (as
    either challenger or opponent) in ANY chat, if any. Used to block a
    player from starting/accepting a second match before finishing the
    one they're already in."""
    row = await fetchrow(
        """
        SELECT * FROM play_matches
        WHERE (challenger_id = $1 OR opponent_id = $1) AND status = ANY($2::text[])
        ORDER BY match_id DESC LIMIT 1;
        """,
        user_id, list(ACTIVE_STATUSES),
    )
    return dict(row) if row else None


async def set_message_id(match_id, message_id):
    await execute("UPDATE play_matches SET message_id = $1 WHERE match_id = $2;", message_id, match_id)


async def update_status(match_id, status):
    await execute("UPDATE play_matches SET status = $1 WHERE match_id = $2;", status, match_id)


async def set_pitch(match_id, pitch):
    await execute(
        "UPDATE play_matches SET pitch = $1, status = 'pitch_selected' WHERE match_id = $2;",
        pitch, match_id,
    )


async def set_toss(match_id, toss_winner_id, toss_call, toss_result):
    await execute(
        """
        UPDATE play_matches
        SET toss_winner_id = $1, toss_call = $2, toss_result = $3, status = 'toss_done'
        WHERE match_id = $4;
        """,
        toss_winner_id, toss_call, toss_result, match_id,
    )


async def set_decision(match_id, decision):
    await execute(
        "UPDATE play_matches SET decision = $1, status = 'lineup' WHERE match_id = $2;",
        decision, match_id,
    )
