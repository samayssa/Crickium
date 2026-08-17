from __future__ import annotations

from .db import execute


TABLES = {
    "users": """
        CREATE TABLE IF NOT EXISTS users(
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance BIGINT DEFAULT 0,
            rubies BIGINT DEFAULT 0,
            total_spent BIGINT DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(),
            last_seen_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "players": """
        CREATE TABLE IF NOT EXISTS players(
            player_id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            country TEXT,
            role TEXT NOT NULL,
            bat_level INTEGER NOT NULL,
            bowl_level INTEGER NOT NULL,
            batting_hand TEXT,
            bowling_hand TEXT,
            uploaded_by BIGINT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "player_stats": """
        CREATE TABLE IF NOT EXISTS player_stats(
            user_id BIGINT PRIMARY KEY,
            matches INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            runs INTEGER DEFAULT 0,
            wickets INTEGER DEFAULT 0
        );
    """,
    "matches": """
        CREATE TABLE IF NOT EXISTS matches(
            match_id SERIAL PRIMARY KEY,
            creator BIGINT,
            status TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "play_matches": """
        CREATE TABLE IF NOT EXISTS play_matches(
            match_id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            message_id BIGINT,
            challenger_id BIGINT NOT NULL,
            challenger_username TEXT,
            challenger_name TEXT,
            opponent_id BIGINT NOT NULL,
            opponent_username TEXT,
            opponent_name TEXT,
            status TEXT DEFAULT 'pending',
            pitch TEXT,
            toss_winner_id BIGINT,
            toss_call TEXT,
            toss_result TEXT,
            decision TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "daily_rewards": """
        CREATE TABLE IF NOT EXISTS daily_rewards(
            user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
            streak INTEGER NOT NULL DEFAULT 0,
            total_claimed BIGINT NOT NULL DEFAULT 0,
            last_claim_at TIMESTAMP,
            next_claim_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "team_squads": """
        CREATE TABLE IF NOT EXISTS team_squads(
            user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
            squad JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """,
}


async def migrate() -> None:
    for name, ddl in TABLES.items():
        print(f"[miniapp_backend] ensuring table: {name}")
        await execute(ddl)

    await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS rubies BIGINT DEFAULT 0;")
    await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_spent BIGINT DEFAULT 0;")
    await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP DEFAULT NOW();")
