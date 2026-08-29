from __future__ import annotations

import re
from database.query import execute, fetch, fetchrow, fetchval

ENGINE_KEY = 'IPL'

_ROLE_MAP = {
    'batsman': 'Batsman', 'bat': 'Batsman', 'batter': 'Batsman',
    'bowler': 'Bowler', 'bowl': 'Bowler',
    'allrounder': 'AllRounder', 'all-rounder': 'AllRounder', 'all rounder': 'AllRounder', 'ar': 'AllRounder',
    'wicketkeeper': 'Wicketkeeper', 'wicket-keeper': 'Wicketkeeper', 'wicket keeper': 'Wicketkeeper', 'wk': 'Wicketkeeper',
}
_BAT_RE = re.compile(r'^(RH|LH)\s*-\s*BAT\s+(\d{1,3})$', re.I)
_BOWL_RE = re.compile(r'^(RAF|LAF|RAM|LAM|RAO|LAO|RAL|LAL)\s+(\d{1,3})$', re.I)

_SCHEMA_READY = False


async def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    await execute(
        '''
        CREATE TABLE IF NOT EXISTS playipl_matches(
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
        '''
    )
    await execute('CREATE INDEX IF NOT EXISTS idx_playipl_matches_chat_status ON playipl_matches(chat_id, status);')
    await execute('CREATE INDEX IF NOT EXISTS idx_playipl_matches_user_status ON playipl_matches(challenger_id, opponent_id, status);')
    _SCHEMA_READY = True


def parse_playipl_player_line(line: str):
    fields = re.findall(r'\[([^\[\]]*)\]', line.strip())
    if len(fields) != 5:
        return None, 'expected 5 bracketed player fields'
    name, country, raw_role, raw_bat, raw_bowl = [f.strip() for f in fields]
    role = _ROLE_MAP.get(raw_role.lower())
    if not name or not role:
        return None, 'invalid name or role'
    bm = _BAT_RE.match(raw_bat)
    bw = _BOWL_RE.match(raw_bowl)
    if not bm or not bw:
        return None, f'invalid batting/bowling field in line: {line!r}'
    bat_level = int(bm.group(2)); bowl_level = int(bw.group(2))
    if not 0 <= bat_level <= 100 or not 0 <= bowl_level <= 100:
        return None, 'levels must be 0-100'
    return {
        'name': name, 'country': country or None, 'role': role,
        'bat_level': bat_level, 'bowl_level': bowl_level,
        'batting_hand': bm.group(1).upper(), 'bowling_hand': bw.group(1).upper(),
    }, None


async def create_match(chat_id, challenger_id, challenger_username, challenger_name,
                       opponent_id, opponent_username, opponent_name):
    await ensure_schema()
    return await fetchrow(
        '''
        INSERT INTO playipl_matches
        (chat_id, challenger_id, challenger_username, challenger_name,
         opponent_id, opponent_username, opponent_name, status)
        VALUES ($1,$2,$3,$4,$5,$6,$7,'pending')
        RETURNING *;
        ''',
        chat_id, challenger_id, challenger_username, challenger_name,
        opponent_id, opponent_username, opponent_name,
    )


async def get_match(match_id):
    await ensure_schema()
    return await fetchrow('SELECT * FROM playipl_matches WHERE match_id=$1;', match_id)


async def update_status(match_id, status):
    await ensure_schema()
    await execute('UPDATE playipl_matches SET status=$1 WHERE match_id=$2;', status, match_id)


async def set_message_id(match_id, message_id):
    await ensure_schema()
    await execute('UPDATE playipl_matches SET message_id=$1 WHERE match_id=$2;', message_id, match_id)


async def set_team(match_id, user_id, team_code, team_name):
    row = await get_match(match_id)
    if not row:
        return
    if int(user_id) == int(row['challenger_id']):
        await execute(
            "UPDATE playipl_matches SET challenger_team_code=$1, challenger_team_name=$2, status='team_selection' WHERE match_id=$3;",
            team_code, team_name, match_id,
        )
    elif int(user_id) == int(row['opponent_id']):
        await execute(
            "UPDATE playipl_matches SET opponent_team_code=$1, opponent_team_name=$2, status='team_selection' WHERE match_id=$3;",
            team_code, team_name, match_id,
        )


async def set_xi(match_id, user_id, player_ids, is_challenger: bool | None = None):
    import json
    row = await get_match(match_id)
    if not row:
        return
    if is_challenger is None:
        is_challenger = int(user_id) == int(row['challenger_id'])
    field = 'challenger_xi' if is_challenger else 'opponent_xi'
    await execute(f'UPDATE playipl_matches SET {field}=$1::jsonb WHERE match_id=$2;', json.dumps(list(player_ids)), match_id)


async def set_xi_confirmed(match_id, user_id):
    row = await get_match(match_id)
    if not row:
        return
    field = 'challenger_xi_confirmed' if int(user_id) == int(row['challenger_id']) else 'opponent_xi_confirmed'
    await execute(f'UPDATE playipl_matches SET {field}=TRUE WHERE match_id=$1;', match_id)


async def set_pitch(match_id, pitch):
    await ensure_schema()
    await execute("UPDATE playipl_matches SET pitch=$1, status='pitch_selected' WHERE match_id=$2;", pitch, match_id)


async def set_toss(match_id, winner_id, call, result):
    await ensure_schema()
    await execute(
        "UPDATE playipl_matches SET toss_winner_id=$1, toss_call=$2, toss_result=$3, status='toss_done' WHERE match_id=$4;",
        winner_id, call, result, match_id,
    )


async def set_decision(match_id, decision):
    await ensure_schema()
    await execute("UPDATE playipl_matches SET decision=$1, status='lineup' WHERE match_id=$2;", decision, match_id)


async def get_active_match_in_chat(chat_id):
    await ensure_schema()
    return await fetchrow(
        """SELECT * FROM playipl_matches WHERE chat_id=$1 AND status IN
        ('pending','accepted','team_selection','pitch_selected','toss_done','lineup','live')
        ORDER BY match_id DESC LIMIT 1;""",
        chat_id,
    )


async def get_active_match_for_user(user_id):
    await ensure_schema()
    return await fetchrow(
        """SELECT * FROM playipl_matches WHERE (challenger_id=$1 OR opponent_id=$1) AND status IN
        ('pending','accepted','team_selection','pitch_selected','toss_done','lineup','live')
        ORDER BY match_id DESC LIMIT 1;""",
        user_id,
    )


async def get_team_players(team_code, limit=None, offset=0):
    query = 'SELECT * FROM playint_players WHERE engine_key=$1 AND team_code=$2 ORDER BY player_id ASC'
    args = [ENGINE_KEY, team_code]
    if limit is not None:
        query += ' LIMIT $3 OFFSET $4'
        args.extend([limit, offset])
    rows = await fetch(query + ';', *args)
    return [dict(r) for r in rows]


async def get_teams_player_ids(team_code, player_ids):
    if not player_ids:
        return []
    rows = await fetch(
        'SELECT * FROM playint_players WHERE engine_key=$1 AND team_code=$2 AND player_id = ANY($3::int[]) ORDER BY player_id ASC;',
        ENGINE_KEY, team_code, player_ids,
    )
    return [dict(r) for r in rows]


async def upsert_playipl_player(team_code, team_name, player, uploaded_by):
    await ensure_schema()
    row = await fetchrow(
        '''
        INSERT INTO playint_players
        (engine_key, team_code, team_name, name, country, role, bat_level, bowl_level, batting_hand, bowling_hand, uploaded_by)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        ON CONFLICT (engine_key, team_code, name) DO UPDATE SET
            team_name=EXCLUDED.team_name,
            country=EXCLUDED.country,
            role=EXCLUDED.role,
            bat_level=EXCLUDED.bat_level,
            bowl_level=EXCLUDED.bowl_level,
            batting_hand=EXCLUDED.batting_hand,
            bowling_hand=EXCLUDED.bowling_hand,
            uploaded_by=EXCLUDED.uploaded_by
        RETURNING *;
        ''',
        ENGINE_KEY, team_code, team_name, player['name'], player['country'], player['role'],
        player['bat_level'], player['bowl_level'], player['batting_hand'], player['bowling_hand'], uploaded_by,
    )
    return True, dict(row) if row else None
