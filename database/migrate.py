from database.query import execute

TABLES = {
    "playint_players": """
        CREATE TABLE IF NOT EXISTS playint_players(
            player_id SERIAL PRIMARY KEY,
            engine_key TEXT NOT NULL DEFAULT 'T20I',
            team_code TEXT NOT NULL,
            team_name TEXT NOT NULL,
            name TEXT NOT NULL,
            country TEXT,
            role TEXT NOT NULL,
            bat_level INTEGER NOT NULL,
            bowl_level INTEGER NOT NULL,
            batting_hand TEXT,
            bowling_hand TEXT,
            uploaded_by BIGINT,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(engine_key, team_code, name)
        );
    """,
    "playint_matches": """
        CREATE TABLE IF NOT EXISTS playint_matches(
            match_id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            message_id BIGINT,
            challenger_id BIGINT NOT NULL,
            challenger_username TEXT,
            challenger_name TEXT,
            opponent_id BIGINT NOT NULL,
            opponent_username TEXT,
            opponent_name TEXT,
            challenger_team_code TEXT,
            challenger_team_name TEXT,
            opponent_team_code TEXT,
            opponent_team_name TEXT,
            challenger_xi JSONB NOT NULL DEFAULT '[]'::jsonb,
            opponent_xi JSONB NOT NULL DEFAULT '[]'::jsonb,
            challenger_xi_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
            opponent_xi_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
            status TEXT DEFAULT 'pending',
            pitch TEXT,
            toss_winner_id BIGINT,
            toss_call TEXT,
            toss_result TEXT,
            decision TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "schema_version": """
        CREATE TABLE IF NOT EXISTS schema_version(
            id SERIAL PRIMARY KEY,
            version INTEGER NOT NULL,
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "bot_runtime_state": """
        CREATE TABLE IF NOT EXISTS bot_runtime_state(
            state_key TEXT PRIMARY KEY,
            state_value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "users": """
        CREATE TABLE IF NOT EXISTS users(
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance BIGINT DEFAULT 0,
            total_spent BIGINT DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(),
            last_seen_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "recent_playing_xis": """
        CREATE TABLE IF NOT EXISTS recent_playing_xis(
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            engine_key TEXT NOT NULL,
            team_code TEXT NOT NULL,
            player_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            updated_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (user_id, engine_key, team_code)
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
    "team_squads": """
        CREATE TABLE IF NOT EXISTS team_squads(
            user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
            squad JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "match_challenges": """
        CREATE TABLE IF NOT EXISTS match_challenges(
            challenge_id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            message_id BIGINT,
            challenger_id BIGINT NOT NULL,
            challenger_username TEXT,
            challenger_name TEXT,
            opponent_id BIGINT,
            opponent_username TEXT,
            opponent_name TEXT,
            format TEXT DEFAULT 'T20',
            status TEXT DEFAULT 'pending',
            toss_winner_id BIGINT,
            toss_call TEXT,
            toss_result TEXT,
            decision TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            expires_at TIMESTAMP
        );
    """,
    "player_claims": """
        CREATE TABLE IF NOT EXISTS player_claims(
            claim_id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            player_id BIGINT NOT NULL REFERENCES players(player_id),
            status TEXT DEFAULT 'pending',
            claimed_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "player_user_match_stats": """
        CREATE TABLE IF NOT EXISTS player_user_match_stats(
            match_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL REFERENCES users(user_id),
            player_id BIGINT NOT NULL REFERENCES players(player_id),
            bat_matches INTEGER NOT NULL DEFAULT 0,
            bat_innings INTEGER NOT NULL DEFAULT 0,
            runs INTEGER NOT NULL DEFAULT 0,
            fifties INTEGER NOT NULL DEFAULT 0,
            centuries INTEGER NOT NULL DEFAULT 0,
            bat_balls INTEGER NOT NULL DEFAULT 0,
            dismissals INTEGER NOT NULL DEFAULT 0,
            bowl_matches INTEGER NOT NULL DEFAULT 0,
            bowl_innings INTEGER NOT NULL DEFAULT 0,
            wickets INTEGER NOT NULL DEFAULT 0,
            three_wickets INTEGER NOT NULL DEFAULT 0,
            five_wickets INTEGER NOT NULL DEFAULT 0,
            bowl_balls INTEGER NOT NULL DEFAULT 0,
            bowl_runs INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (match_id, user_id, player_id)
        );
    """,
    "broadcast_targets": """
        CREATE TABLE IF NOT EXISTS broadcast_targets(
            chat_id BIGINT PRIMARY KEY,
            target_type TEXT NOT NULL,
            title TEXT,
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "team_lineups": """
        CREATE TABLE IF NOT EXISTS team_lineups(
            user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
            player_ids JSONB NOT NULL,
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "probability_profiles": """
        CREATE TABLE IF NOT EXISTS probability_profiles(
            profile_id SERIAL PRIMARY KEY,
            profile_key TEXT NOT NULL UNIQUE,
            selectors JSONB NOT NULL DEFAULT '{}'::jsonb,
            probabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
            outcomes JSONB NOT NULL DEFAULT '{}'::jsonb,
            duplicate_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by BIGINT,
            updated_by BIGINT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "special_edition_players": """
        CREATE TABLE IF NOT EXISTS special_edition_players(
            special_player_id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            edition TEXT NOT NULL,
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
    "special_player_card_images": """
        CREATE TABLE IF NOT EXISTS special_player_card_images(
            special_player_id BIGINT PRIMARY KEY REFERENCES special_edition_players(special_player_id) ON DELETE CASCADE,
            file_id TEXT NOT NULL,
            channel_message_id BIGINT,
            uploaded_by BIGINT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "authorized_uploaders": """
        CREATE TABLE IF NOT EXISTS authorized_uploaders(
            user_id BIGINT PRIMARY KEY,
            granted_by BIGINT NOT NULL,
            granted_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "player_card_images": """
        CREATE TABLE IF NOT EXISTS player_card_images(
            player_id BIGINT PRIMARY KEY REFERENCES players(player_id),
            file_id TEXT NOT NULL,
            channel_message_id BIGINT,
            uploaded_by BIGINT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """,
    "template_card_image": """
        CREATE TABLE IF NOT EXISTS template_card_image(
            id INTEGER PRIMARY KEY,
            card_type TEXT,
            file_id TEXT NOT NULL,
            channel_message_id BIGINT,
            uploaded_by BIGINT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """,
    # Dedicated to the /play command's own challenge -> pitch -> toss ->
    # decision -> lineup flow. Kept entirely separate from
    # match_challenges (used by /match) so nothing about /match changes.
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
    # Caches one Telegram file_id per stadium name, so /play's MATCH
    # READY card only has to search + download a stadium photo once -
    # every later match at that same stadium reuses the saved file_id.
    "stadium_images": """
        CREATE TABLE IF NOT EXISTS stadium_images(
            stadium_name TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """,
}


async def migrate():
    print("Checking database...")

    for table_name, ddl in TABLES.items():
        print(f"[migrate] Ensuring table '{table_name}' exists...")
        await execute(ddl)
        print(f"[migrate] Table '{table_name}' OK.")

    print("[migrate] Ensuring 'users.last_seen_at' column exists...")
    await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP DEFAULT NOW();")
    print("[migrate] 'users.last_seen_at' OK.")

    print("[migrate] Ensuring index on players.role exists...")
    await execute("CREATE INDEX IF NOT EXISTS idx_players_role ON players(role);")
    print("[migrate] idx_players_role OK.")

    print("[migrate] Ensuring unique index on special-edition identity...")
    await execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_special_edition_identity ON special_edition_players(LOWER(name), LOWER(edition));")
    await execute("CREATE INDEX IF NOT EXISTS idx_special_edition_name ON special_edition_players(LOWER(name));")
    print("[migrate] special-edition indexes OK.")

    print("[migrate] Ensuring index on player_claims.user_id exists...")
    await execute("CREATE INDEX IF NOT EXISTS idx_player_claims_user ON player_claims(user_id, claimed_at);")
    print("[migrate] idx_player_claims_user OK.")

    print("[migrate] Ensuring index on probability_profiles.profile_key exists...")
    await execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_probability_profiles_key ON probability_profiles(profile_key);")
    print("[migrate] idx_probability_profiles_key OK.")

    print("[migrate] Ensuring 'players.batting_hand' column exists...")
    await execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS batting_hand TEXT;")
    print("[migrate] 'players.batting_hand' OK.")

    print("[migrate] Ensuring 'players.bowling_hand' column exists...")
    await execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS bowling_hand TEXT;")
    print("[migrate] 'players.bowling_hand' OK.")

    print("[migrate] Ensuring 'users.total_spent' column exists...")
    await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_spent BIGINT DEFAULT 0;")
    print("[migrate] 'users.total_spent' OK.")

    print("[migrate] Ensuring 'users.rubies' column exists...")
    await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS rubies BIGINT DEFAULT 0;")
    print("[migrate] 'users.rubies' OK.")

    print("[migrate] Ensuring table 'daily_rewards' exists...")
    await execute(
        """
        CREATE TABLE IF NOT EXISTS daily_rewards(
            user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
            streak INTEGER NOT NULL DEFAULT 0,
            total_claimed BIGINT NOT NULL DEFAULT 0,
            last_claim_at TIMESTAMP,
            next_claim_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT NOW()
        );
        """
    )
    print("[migrate] daily_rewards OK.")

    print("[migrate] Ensuring table 'probability_profiles' has required columns...")
    await execute("ALTER TABLE probability_profiles ADD COLUMN IF NOT EXISTS selectors JSONB NOT NULL DEFAULT '{}'::jsonb;")
    await execute("ALTER TABLE probability_profiles ADD COLUMN IF NOT EXISTS probabilities JSONB NOT NULL DEFAULT '{}'::jsonb;")
    await execute("ALTER TABLE probability_profiles ADD COLUMN IF NOT EXISTS outcomes JSONB NOT NULL DEFAULT '{}'::jsonb;")
    await execute("ALTER TABLE probability_profiles ADD COLUMN IF NOT EXISTS duplicate_counts JSONB NOT NULL DEFAULT '{}'::jsonb;")
    await execute("ALTER TABLE probability_profiles ADD COLUMN IF NOT EXISTS created_by BIGINT;")
    await execute("ALTER TABLE probability_profiles ADD COLUMN IF NOT EXISTS updated_by BIGINT;")
    await execute("ALTER TABLE probability_profiles ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();")
    await execute("ALTER TABLE probability_profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();")
    print("[migrate] probability_profiles columns OK.")

    print("[migrate] Upgrading 'template_card_image' to support separate bat/ball templates...")
    await execute("ALTER TABLE template_card_image DROP CONSTRAINT IF EXISTS template_card_image_single_row;")
    await execute("ALTER TABLE template_card_image ALTER COLUMN id DROP DEFAULT;")
    await execute("ALTER TABLE template_card_image ADD COLUMN IF NOT EXISTS card_type TEXT;")
    # Any pre-existing template (from the old single-template /upload_img
    # template flow) becomes the bat-card template, since that's what it
    # was always used for.
    await execute("UPDATE template_card_image SET card_type = 'bat' WHERE card_type IS NULL;")
    await execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_template_card_image_type ON template_card_image(card_type);")
    print("[migrate] 'template_card_image' upgrade OK.")

    # --- Ruby -> Coin exchange request ledger (one-time callback guard) ---
    print("[migrate] Ensuring table 'coin_exchange_requests' exists...")
    await execute(
        """
        CREATE TABLE IF NOT EXISTS coin_exchange_requests(
            request_id UUID PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            rubies BIGINT NOT NULL CHECK (rubies > 0),
            coins BIGINT NOT NULL CHECK (coins > 0),
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW(),
            processed_at TIMESTAMP
        );
        """
    )
    await execute("CREATE INDEX IF NOT EXISTS idx_coin_exchange_requests_user ON coin_exchange_requests(user_id, created_at DESC);")
    print("[migrate] coin_exchange_requests OK.")

    # --- Level system (Level 1-30, 6 tiers) + franchise identity for /profile ---
    print("[migrate] Ensuring 'users.level' column exists...")
    await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS level INTEGER NOT NULL DEFAULT 1;")
    print("[migrate] 'users.level' OK.")

    print("[migrate] Ensuring 'users.xp' column exists...")
    await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS xp INTEGER NOT NULL DEFAULT 0;")
    print("[migrate] 'users.xp' OK.")

    print("[migrate] Ensuring 'users.franchise_name' column exists...")
    await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS franchise_name TEXT;")
    print("[migrate] 'users.franchise_name' OK.")

    print("[migrate] Ensuring index for level/xp leaderboard ranking exists...")
    await execute("CREATE INDEX IF NOT EXISTS idx_users_level_xp ON users(level DESC, xp DESC);")
    print("[migrate] idx_users_level_xp OK.")

    # --- Tier card images uploaded via '/upload_img <tier>' (bronze/silver/gold/...) ---
    print("[migrate] Ensuring table 'tier_card_images' exists...")
    await execute(
        """
        CREATE TABLE IF NOT EXISTS tier_card_images(
            tier_key TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            channel_message_id BIGINT,
            uploaded_by BIGINT,
            updated_at TIMESTAMP DEFAULT NOW()
        );
        """
    )
    print("[migrate] tier_card_images OK.")

    # --- Career batting/bowling stats for /plstats (cricket player cards,
    # NOT the same as user-level player_stats used by /profile's XP system) ---
    print("[migrate] Ensuring players career-stat columns exist...")
    await execute(
        """
        ALTER TABLE players
            ADD COLUMN IF NOT EXISTS bat_matches INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS bat_innings INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS runs INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS fifties INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS centuries INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS bat_average NUMERIC(8,2) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS strike_rate NUMERIC(8,2) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS bowl_matches INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS bowl_innings INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS wickets INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS three_wickets INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS five_wickets INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS bowl_average NUMERIC(8,2) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS economy NUMERIC(8,2) NOT NULL DEFAULT 0;
        """
    )
    print("[migrate] players career-stat columns OK.")

    # --- PlayInt engine/team scoped player storage ---
    # Existing PlayInt rows belong to the T20I engine. Keep them intact while
    # replacing the old team-only uniqueness rule with engine + team + name.
    print("[migrate] Ensuring playint_players engine scope...")
    await execute("ALTER TABLE playint_players ADD COLUMN IF NOT EXISTS engine_key TEXT NOT NULL DEFAULT 'T20I';")
    await execute("UPDATE playint_players SET engine_key='T20I' WHERE engine_key IS NULL OR engine_key='';")
    await execute("ALTER TABLE playint_players DROP CONSTRAINT IF EXISTS playint_players_team_code_name_key;")
    await execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_playint_players_engine_team_name ON playint_players(engine_key, team_code, name);")
    await execute("CREATE INDEX IF NOT EXISTS idx_playint_players_engine_team ON playint_players(engine_key, team_code);")
    print("[migrate] playint_players engine scope OK.")

    # /plstats stores the same signed squad-facing player_id used by team_squads.
    # Global players are positive IDs; special editions use the negative
    # special_player_id namespace. The old FK to players blocked all writes
    # whenever one special-edition player appeared in a match ledger.
    print("[migrate] Relaxing player_user_match_stats.player_id FK for global + special IDs...")
    await execute("ALTER TABLE player_user_match_stats DROP CONSTRAINT IF EXISTS player_user_match_stats_player_id_fkey;")
    print("[migrate] player_user_match_stats player identity constraint OK.")

    print("Migration Complete.")
