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
    await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS sigils BIGINT DEFAULT 0;")
    await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_sigils_earned BIGINT DEFAULT 0;")
    await execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_sigils_spent BIGINT DEFAULT 0;")

                                                                              
                                                                          
                                                              
    await execute("""
        CREATE TABLE IF NOT EXISTS pack_inventory(
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            pack_key TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(user_id, pack_key)
        );
    """)
    await execute("""
        CREATE TABLE IF NOT EXISTS pack_purchase_requests(
            request_id UUID PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            pack_key TEXT NOT NULL,
            price_sigils BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','cancelled','failed')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            processed_at TIMESTAMPTZ
        );
    """)
    await execute("ALTER TABLE IF EXISTS pack_purchase_requests ADD COLUMN IF NOT EXISTS client_request_id TEXT;")
    await execute("CREATE INDEX IF NOT EXISTS idx_pack_purchase_requests_user_status ON pack_purchase_requests(user_id,status,created_at DESC);")
    await execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pack_purchase_requests_user_client_request ON pack_purchase_requests(user_id,client_request_id) WHERE client_request_id IS NOT NULL;")
    await execute("""
        CREATE TABLE IF NOT EXISTS pack_openings(
            opening_id UUID PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            pack_key TEXT NOT NULL,
            cards JSONB NOT NULL DEFAULT '[]'::jsonb,
            coins BIGINT NOT NULL DEFAULT 0,
            rubies BIGINT NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'opened',
            opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    await execute("ALTER TABLE IF EXISTS pack_openings ADD COLUMN IF NOT EXISTS client_request_id TEXT;")
    await execute("CREATE INDEX IF NOT EXISTS idx_pack_openings_user_time ON pack_openings(user_id,opened_at DESC);")
    await execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pack_openings_user_client_request ON pack_openings(user_id,client_request_id) WHERE client_request_id IS NOT NULL;")
    await execute("""
        CREATE TABLE IF NOT EXISTS pack_card_inventory(
            card_id BIGSERIAL PRIMARY KEY,
            opening_id UUID NOT NULL REFERENCES pack_openings(opening_id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            player_id BIGINT NOT NULL,
            card_kind TEXT NOT NULL CHECK (card_kind IN ('core','bonus')),
            position INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(opening_id,position)
        );
    """)
    await execute("CREATE INDEX IF NOT EXISTS idx_pack_card_inventory_user ON pack_card_inventory(user_id,created_at DESC);")
    await execute(
        """
        CREATE TABLE IF NOT EXISTS miniapp_kitbag_claims(
            claim_id UUID PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            player_id BIGINT NOT NULL,
            player_kind TEXT NOT NULL DEFAULT 'global',
            opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status TEXT NOT NULL DEFAULT 'added',
            replaced_player_id BIGINT,
            resolved_at TIMESTAMPTZ
        );
        """
    )
                                                                                
                                                                                      
    await execute("ALTER TABLE miniapp_kitbag_claims ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'added';")
    await execute("ALTER TABLE miniapp_kitbag_claims ADD COLUMN IF NOT EXISTS replaced_player_id BIGINT;")
    await execute("ALTER TABLE miniapp_kitbag_claims ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;")
                                                                                 
                                                                                 
    await execute("ALTER TABLE miniapp_kitbag_claims ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'free';")
    await execute(
        "CREATE INDEX IF NOT EXISTS idx_miniapp_kitbag_claims_user_time ON miniapp_kitbag_claims(user_id, opened_at DESC);"
    )
                                                                          
                                                                   
    await execute("""
        CREATE TABLE IF NOT EXISTS miniapp_gift_campaigns(
            gift_id BIGSERIAL PRIMARY KEY,
            created_by BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','ready','scheduled','distributing','distributed','cancelled')),
            step TEXT NOT NULL DEFAULT 'choose_type',
            title TEXT NOT NULL DEFAULT 'Happy Diwali',
            theme TEXT NOT NULL DEFAULT 'normal' CHECK (theme IN ('normal','diwali')),
            reward_type TEXT NOT NULL DEFAULT 'multiple',
            reward_config JSONB NOT NULL DEFAULT '{}'::jsonb,
            image_file_id TEXT,
            scheduled_at TIMESTAMPTZ,
            admin_chat_id BIGINT,
            admin_message_id BIGINT,
            admin_message_is_photo BOOLEAN NOT NULL DEFAULT FALSE,
            recipient_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            distributed_at TIMESTAMPTZ
        );
    """)
    await execute("""
        CREATE TABLE IF NOT EXISTS miniapp_gift_user_rewards(
            gift_id BIGINT NOT NULL REFERENCES miniapp_gift_campaigns(gift_id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            reward JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','opened','claimed')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            opened_at TIMESTAMPTZ,
            claimed_at TIMESTAMPTZ,
            PRIMARY KEY(gift_id,user_id)
        );
    """)
    await execute("CREATE INDEX IF NOT EXISTS idx_miniapp_gifts_user_status ON miniapp_gift_user_rewards(user_id,status,gift_id DESC);")
    await execute("CREATE INDEX IF NOT EXISTS idx_miniapp_gifts_scheduled ON miniapp_gift_campaigns(status,scheduled_at);")

                                                                              
                                                                             
    await execute("""
        CREATE TABLE IF NOT EXISTS miniapp_ad_attempts(
            ymid TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            usage_date DATE NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            event_type TEXT,
            reward_event_type TEXT,
            zone_id BIGINT,
            sub_zone_id BIGINT,
            request_var TEXT,
            estimated_price NUMERIC(14,7),
            claim_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            rewarded_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            provider TEXT NOT NULL DEFAULT 'legacy'
        );
    """)
    await execute("ALTER TABLE miniapp_ad_attempts ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'legacy';")
                                                                         
    await execute("UPDATE miniapp_ad_attempts SET status='expired',updated_at=NOW() WHERE status='pending' AND provider <> 'adsgram';")
    await execute(
        "CREATE INDEX IF NOT EXISTS idx_miniapp_ad_attempts_user_date ON miniapp_ad_attempts(user_id, usage_date, status);"
    )
    await execute(
        "CREATE INDEX IF NOT EXISTS idx_miniapp_ad_attempts_user_rewarded_at ON miniapp_ad_attempts(user_id, rewarded_at DESC) WHERE status='rewarded';"
    )
                                                                                      
    await execute("""
        CREATE TABLE IF NOT EXISTS miniapp_news_articles(
            article_id BIGSERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            image_file_id TEXT NOT NULL,
            created_by BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_published BOOLEAN NOT NULL DEFAULT TRUE
        );
    """)
    await execute("CREATE INDEX IF NOT EXISTS idx_miniapp_news_articles_published ON miniapp_news_articles(is_published, created_at DESC);")

    try:
        from database import achievements_repo, giveaway_repo, miniapp_game_repo
        await achievements_repo.ensure_schema()
        await miniapp_game_repo.ensure_schema()
        await giveaway_repo.ensure_schema()
    except Exception as exc:
        print(f"[miniapp_backend] Achievements/Play Game schema skipped: {exc!r}")
