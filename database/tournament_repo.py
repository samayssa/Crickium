"""Tournament storage (tournaments, teams, fixtures, match days, per-match player lines).

All identifiers are small per-tournament counters instead of database serials:
``tid`` is the tournament number shown to people ("Tournament #7"), ``team_no``,
``fno`` (match number) and ``ord`` (auction lot number) count up inside one
tournament. They fit comfortably into the 64-byte callback-data limit and into
virtual team codes (``CSK7``) and they behave the same on Postgres and
CockroachDB (no ``unique_rowid`` surprises).

Every state change is a single conditional UPDATE ... RETURNING or a short
transaction, so a double tap, the bot worker and the Mini App can never move a
tournament through the same step twice.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from database.query import execute, fetch, fetchrow, fetchval, transaction

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS tour_tournaments(
        tid BIGINT PRIMARY KEY,
        kind TEXT NOT NULL DEFAULT 'user',
        name TEXT NOT NULL,
        engine TEXT NOT NULL DEFAULT 'PLAYIPL',
        preset TEXT NOT NULL DEFAULT 'ipl',
        mode TEXT NOT NULL DEFAULT 'direct',
        status TEXT NOT NULL DEFAULT 'setup',
        creator_id BIGINT NOT NULL,
        creator_name TEXT,
        host_chat_id BIGINT,
        host_chat_title TEXT,
        max_teams INTEGER NOT NULL DEFAULT 8,
        purse_lakh BIGINT NOT NULL DEFAULT 0,
        ladder JSONB NOT NULL DEFAULT '[[100,5],[200,10],[500,20],[null,25]]'::jsonb,
        squad_min INTEGER NOT NULL DEFAULT 11,
        squad_max INTEGER NOT NULL DEFAULT 25,
        overseas_squad_max INTEGER NOT NULL DEFAULT 0,
        xi_overseas_max INTEGER NOT NULL DEFAULT 0,
        min_base_lakh INTEGER NOT NULL DEFAULT 20,
        window_start_min INTEGER NOT NULL DEFAULT 1020,
        window_end_min INTEGER NOT NULL DEFAULT 1260,
        tz_min INTEGER NOT NULL DEFAULT 330,
        legs INTEGER NOT NULL DEFAULT 0,
        prize JSONB NOT NULL DEFAULT '{}'::jsonb,
        setup_step TEXT NOT NULL DEFAULT 'engine',
        setup_msg_id BIGINT,
        lobby_msg_id BIGINT,
        champion_team_no INTEGER,
        champion_user_id BIGINT,
        runner_up_team_no INTEGER,
        runner_up_user_id BIGINT,
        league_done BOOLEAN NOT NULL DEFAULT FALSE,
        current_day INTEGER NOT NULL DEFAULT 0,
        last_tick_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        cleaned BOOLEAN NOT NULL DEFAULT FALSE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_tour_tournaments_status ON tour_tournaments(status, tid);",
    "CREATE INDEX IF NOT EXISTS idx_tour_tournaments_chat ON tour_tournaments(host_chat_id, status);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_tour_tournaments_one_draft ON tour_tournaments(creator_id) WHERE status = 'setup';",
    """
    CREATE TABLE IF NOT EXISTS tour_teams(
        tid BIGINT NOT NULL,
        team_no INTEGER NOT NULL,
        code TEXT NOT NULL,
        base_code TEXT NOT NULL,
        name TEXT NOT NULL,
        short TEXT,
        owner_id BIGINT,
        owner_name TEXT,
        owner_username TEXT,
        purse_left BIGINT NOT NULL DEFAULT 0,
        spent BIGINT NOT NULL DEFAULT 0,
        squad_count INTEGER NOT NULL DEFAULT 0,
        overseas_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (tid, team_no)
    );
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_tour_teams_base ON tour_teams(tid, base_code);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_tour_teams_owner ON tour_teams(tid, owner_id) WHERE owner_id IS NOT NULL;",
    """
    CREATE TABLE IF NOT EXISTS tour_members(
        tid BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        team_no INTEGER,
        username TEXT,
        first_name TEXT,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (tid, user_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_tour_members_user ON tour_members(user_id);",
    """
    CREATE TABLE IF NOT EXISTS tour_auction(
        tid BIGINT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'idle',
        cur_ord INTEGER,
        round INTEGER NOT NULL DEFAULT 1,
        total_lots INTEGER NOT NULL DEFAULT 0,
        version BIGINT NOT NULL DEFAULT 0,
        paused_left_ms INTEGER,
        next_lot_at TIMESTAMPTZ,
        pinned_msg_id BIGINT,
        pin_failed BOOLEAN NOT NULL DEFAULT FALSE,
        accel_done BOOLEAN NOT NULL DEFAULT FALSE,
        started_at TIMESTAMPTZ,
        ended_at TIMESTAMPTZ
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tour_lots(
        tid BIGINT NOT NULL,
        ord INTEGER NOT NULL,
        round INTEGER NOT NULL DEFAULT 1,
        set_idx INTEGER NOT NULL DEFAULT 0,
        set_code TEXT NOT NULL DEFAULT '',
        set_name TEXT NOT NULL DEFAULT '',
        pool_name TEXT NOT NULL DEFAULT '',
        set_pos INTEGER NOT NULL DEFAULT 1,
        set_size INTEGER NOT NULL DEFAULT 1,
        pkey TEXT NOT NULL,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        country TEXT,
        bat_level INTEGER NOT NULL DEFAULT 0,
        bowl_level INTEGER NOT NULL DEFAULT 0,
        batting_hand TEXT,
        bowling_hand TEXT,
        overseas BOOLEAN NOT NULL DEFAULT FALSE,
        card JSONB NOT NULL DEFAULT '{}'::jsonb,
        base_lakh INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        cur_bid_lakh INTEGER NOT NULL DEFAULT 0,
        leader_team_no INTEGER,
        bid_count INTEGER NOT NULL DEFAULT 0,
        started_at TIMESTAMPTZ,
        deadline_at TIMESTAMPTZ,
        ended_at TIMESTAMPTZ,
        sold_team_no INTEGER,
        sold_lakh INTEGER,
        a_msg_id BIGINT,
        b_msg_id BIGINT,
        said_once BOOLEAN NOT NULL DEFAULT FALSE,
        said_twice BOOLEAN NOT NULL DEFAULT FALSE,
        said_mask INTEGER NOT NULL DEFAULT 0,
        shown INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (tid, ord)
    );
    """,
    "ALTER TABLE tour_lots ADD COLUMN IF NOT EXISTS said_mask INTEGER NOT NULL DEFAULT 0;",
    "CREATE INDEX IF NOT EXISTS idx_tour_lots_status ON tour_lots(tid, status, ord);",
    """
    CREATE TABLE IF NOT EXISTS tour_bids(
        tid BIGINT NOT NULL,
        ord INTEGER NOT NULL,
        bid_no INTEGER NOT NULL,
        team_no INTEGER NOT NULL,
        user_id BIGINT,
        amount_lakh INTEGER NOT NULL,
        at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (tid, ord, bid_no)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tour_squad(
        tid BIGINT NOT NULL,
        team_no INTEGER NOT NULL,
        pkey TEXT NOT NULL,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        country TEXT,
        bat_level INTEGER NOT NULL DEFAULT 0,
        bowl_level INTEGER NOT NULL DEFAULT 0,
        batting_hand TEXT,
        bowling_hand TEXT,
        overseas BOOLEAN NOT NULL DEFAULT FALSE,
        price_lakh INTEGER NOT NULL DEFAULT 0,
        lot_ord INTEGER,
        source TEXT NOT NULL DEFAULT 'auction',
        card JSONB NOT NULL DEFAULT '{}'::jsonb,
        xi BOOLEAN NOT NULL DEFAULT FALSE,
        PRIMARY KEY (tid, team_no, pkey)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tour_fixtures(
        tid BIGINT NOT NULL,
        fno INTEGER NOT NULL,
        stage TEXT NOT NULL DEFAULT 'league',
        day_no INTEGER NOT NULL DEFAULT 1,
        seq INTEGER NOT NULL DEFAULT 1,
        home_team_no INTEGER NOT NULL,
        away_team_no INTEGER NOT NULL,
        home_user_id BIGINT,
        away_user_id BIGINT,
        status TEXT NOT NULL DEFAULT 'scheduled',
        result_type TEXT,
        winner_team_no INTEGER,
        winner_user_id BIGINT,
        present_home BOOLEAN NOT NULL DEFAULT FALSE,
        present_away BOOLEAN NOT NULL DEFAULT FALSE,
        ready_home BOOLEAN NOT NULL DEFAULT FALSE,
        ready_away BOOLEAN NOT NULL DEFAULT FALSE,
        engine TEXT,
        match_id BIGINT,
        chat_id BIGINT,
        home_runs INTEGER,
        home_wkts INTEGER,
        home_balls INTEGER,
        away_runs INTEGER,
        away_wkts INTEGER,
        away_balls INTEGER,
        result_text TEXT,
        potm TEXT,
        queued_at TIMESTAMPTZ,
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        PRIMARY KEY (tid, fno)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_tour_fixtures_day ON tour_fixtures(tid, day_no, seq);",
    "CREATE INDEX IF NOT EXISTS idx_tour_fixtures_match ON tour_fixtures(engine, match_id);",
    """
    CREATE TABLE IF NOT EXISTS tour_days(
        tid BIGINT NOT NULL,
        day_no INTEGER NOT NULL,
        play_date DATE NOT NULL,
        opens_at TIMESTAMPTZ NOT NULL,
        remind_at TIMESTAMPTZ NOT NULL,
        closes_at TIMESTAMPTZ NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        opened_at TIMESTAMPTZ,
        reminded_at TIMESTAMPTZ,
        closed_at TIMESTAMPTZ,
        card_msg_id BIGINT,
        PRIMARY KEY (tid, day_no)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tour_player_match(
        tid BIGINT NOT NULL,
        fno INTEGER NOT NULL,
        team_no INTEGER NOT NULL,
        pkey TEXT NOT NULL,
        name TEXT NOT NULL,
        batted BOOLEAN NOT NULL DEFAULT FALSE,
        runs INTEGER NOT NULL DEFAULT 0,
        balls INTEGER NOT NULL DEFAULT 0,
        fours INTEGER NOT NULL DEFAULT 0,
        sixes INTEGER NOT NULL DEFAULT 0,
        dismissed BOOLEAN NOT NULL DEFAULT FALSE,
        fifty_balls INTEGER,
        hundred_balls INTEGER,
        wickets INTEGER NOT NULL DEFAULT 0,
        bowl_balls INTEGER NOT NULL DEFAULT 0,
        bowl_runs INTEGER NOT NULL DEFAULT 0,
        potm BOOLEAN NOT NULL DEFAULT FALSE,
        mvp DOUBLE PRECISION NOT NULL DEFAULT 0,
        PRIMARY KEY (tid, fno, team_no, pkey)
    );
    """,
]

_ready = False

LIVE_STATUSES = ("registration", "auction", "ready", "live")
ACTIVE_STATUSES = ("setup", "registration", "auction", "ready", "live")
_JSON_FIELDS = ("ladder", "prize", "card")

# columns update_fields may touch (everything else goes through dedicated functions)
_TOURNAMENT_FIELDS = {
    "kind", "name", "engine", "preset", "mode", "status", "creator_name", "host_chat_id", "host_chat_title",
    "max_teams", "purse_lakh", "ladder", "squad_min", "squad_max", "overseas_squad_max", "xi_overseas_max",
    "min_base_lakh", "window_start_min", "window_end_min", "tz_min", "legs", "prize", "setup_step", "setup_msg_id",
    "lobby_msg_id", "champion_team_no", "champion_user_id", "runner_up_team_no", "runner_up_user_id",
    "league_done", "current_day", "started_at", "completed_at", "cleaned",
}


class TourError(Exception):
    """An expected, user-facing failure. ``code`` is a short machine readable reason."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


async def ensure_schema() -> None:
    global _ready
    if _ready:
        return
    for statement in SCHEMA_STATEMENTS:
        await execute(statement)
    _ready = True


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return default if value is None else value


def _row(raw: Any) -> dict | None:
    if raw is None:
        return None
    row = dict(raw)
    for key in _JSON_FIELDS:
        if key in row:
            row[key] = _json(row[key], {} if key != "ladder" else [])
    return row


def _rows(raws: Iterable[Any]) -> list[dict]:
    return [r for r in (_row(x) for x in raws) if r is not None]


# ---------------------------------------------------------------------------
# tournaments
# ---------------------------------------------------------------------------
async def create_draft(creator_id: int, creator_name: str | None, chat_id: int, chat_title: str | None, kind: str = "user") -> dict:
    """A wizard draft. A creator has at most one draft; an existing one is returned instead."""

    async def _tx(conn):
        existing = await conn.fetchrow("SELECT * FROM tour_tournaments WHERE creator_id=$1 AND status='setup';", int(creator_id))
        if existing:
            return existing, False
        tid = int(await conn.fetchval("SELECT COALESCE(MAX(tid), 0) + 1 FROM tour_tournaments;"))
        row = await conn.fetchrow(
            """
            INSERT INTO tour_tournaments(tid, kind, name, creator_id, creator_name, host_chat_id, host_chat_title)
            VALUES($1, $2, $3, $4, $5, $6, $7) RETURNING *;
            """,
            tid, kind, "New tournament", int(creator_id), creator_name, int(chat_id), chat_title,
        )
        return row, True

    last: Exception | None = None
    for _ in range(6):
        try:
            row, created = await transaction(_tx)
            out = _row(row) or {}
            out["_created"] = created
            return out
        except Exception as exc:                       # a concurrent create picked the same tid
            if getattr(exc, "sqlstate", None) == "23505" or "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                last = exc
                continue
            raise
    raise last or RuntimeError("could not allocate a tournament number")


async def get(tid: int) -> dict | None:
    return _row(await fetchrow("SELECT * FROM tour_tournaments WHERE tid=$1;", int(tid)))


async def update_fields(tid: int, **fields: Any) -> dict | None:
    sets, args = [], [int(tid)]
    for key, value in fields.items():
        if key not in _TOURNAMENT_FIELDS:
            raise ValueError(f"unknown tournament field {key!r}")
        args.append(json.dumps(value) if key in {"ladder", "prize"} else value)
        sets.append(f"{key}=${len(args)}::jsonb" if key in {"ladder", "prize"} else f"{key}=${len(args)}")
    if not sets:
        return await get(tid)
    return _row(await fetchrow(f"UPDATE tour_tournaments SET {', '.join(sets)} WHERE tid=$1 RETURNING *;", *args))


async def cas_status(tid: int, from_statuses: Iterable[str], to_status: str, **extra: Any) -> dict | None:
    """Move a tournament to ``to_status`` only if it is currently in one of ``from_statuses``."""
    args: list[Any] = [int(tid), list(from_statuses), to_status]
    sets = ["status=$3"]
    for key, value in extra.items():
        if key not in _TOURNAMENT_FIELDS:
            raise ValueError(f"unknown tournament field {key!r}")
        args.append(json.dumps(value) if key in {"ladder", "prize"} else value)
        sets.append(f"{key}=${len(args)}::jsonb" if key in {"ladder", "prize"} else f"{key}=${len(args)}")
    return _row(await fetchrow(
        f"UPDATE tour_tournaments SET {', '.join(sets)} WHERE tid=$1 AND status = ANY($2::text[]) RETURNING *;", *args))


async def delete_draft(tid: int) -> None:
    await execute("DELETE FROM tour_tournaments WHERE tid=$1 AND status='setup';", int(tid))


async def draft_of(creator_id: int) -> dict | None:
    return _row(await fetchrow("SELECT * FROM tour_tournaments WHERE creator_id=$1 AND status='setup';", int(creator_id)))


async def for_chat(chat_id: int, statuses: Iterable[str] = LIVE_STATUSES) -> list[dict]:
    return _rows(await fetch(
        "SELECT * FROM tour_tournaments WHERE host_chat_id=$1 AND status = ANY($2::text[]) ORDER BY tid DESC;",
        int(chat_id), list(statuses)))


async def list_active(statuses: Iterable[str] = ("live", "auction", "ready")) -> list[dict]:
    return _rows(await fetch(
        "SELECT * FROM tour_tournaments WHERE status = ANY($1::text[]) ORDER BY tid;", list(statuses)))


async def list_official(limit: int = 30) -> list[dict]:
    return _rows(await fetch(
        "SELECT * FROM tour_tournaments WHERE kind='official' AND status NOT IN ('setup','cancelled') ORDER BY tid DESC LIMIT $1;",
        int(limit)))


async def list_for_user(user_id: int, limit: int = 40) -> list[dict]:
    return _rows(await fetch(
        """
        SELECT t.* FROM tour_tournaments t
        WHERE t.status NOT IN ('setup','cancelled')
          AND (t.creator_id = $1 OR EXISTS (SELECT 1 FROM tour_members m WHERE m.tid = t.tid AND m.user_id = $1))
        ORDER BY t.tid DESC LIMIT $2;
        """,
        int(user_id), int(limit)))


async def list_recent(limit: int = 30) -> list[dict]:
    return _rows(await fetch(
        "SELECT * FROM tour_tournaments WHERE status NOT IN ('setup','cancelled') ORDER BY tid DESC LIMIT $1;", int(limit)))


async def touch_tick(tid: int) -> None:
    await execute("UPDATE tour_tournaments SET last_tick_at=NOW() WHERE tid=$1;", int(tid))


# ---------------------------------------------------------------------------
# teams + members
# ---------------------------------------------------------------------------
async def teams(tid: int) -> list[dict]:
    return _rows(await fetch("SELECT * FROM tour_teams WHERE tid=$1 ORDER BY team_no;", int(tid)))


async def team(tid: int, team_no: int) -> dict | None:
    return _row(await fetchrow("SELECT * FROM tour_teams WHERE tid=$1 AND team_no=$2;", int(tid), int(team_no)))


async def team_of_user(tid: int, user_id: int) -> dict | None:
    return _row(await fetchrow("SELECT * FROM tour_teams WHERE tid=$1 AND owner_id=$2;", int(tid), int(user_id)))


async def team_by_base(tid: int, base_code: str) -> dict | None:
    return _row(await fetchrow("SELECT * FROM tour_teams WHERE tid=$1 AND base_code=$2;", int(tid), str(base_code)))


async def add_team(tid: int, base_code: str, code: str, name: str, short: str | None, owner: dict | None, purse: int = 0,
                   statuses: Iterable[str] = ("registration",)) -> dict:
    """Create a team (optionally with an owner). Raises TourError: closed / full / taken / has_team."""

    async def _tx(conn):
        tour = await conn.fetchrow("SELECT status, max_teams FROM tour_tournaments WHERE tid=$1 FOR UPDATE;", int(tid))
        if tour is None:
            raise TourError("missing", "Tournament not found.")
        if tour["status"] not in set(statuses):
            raise TourError("closed", "Registration is closed.")
        count = int(await conn.fetchval("SELECT COUNT(*) FROM tour_teams WHERE tid=$1;", int(tid)))
        if count >= int(tour["max_teams"]):
            raise TourError("full", "All team slots are taken.")
        if await conn.fetchval("SELECT 1 FROM tour_teams WHERE tid=$1 AND base_code=$2;", int(tid), str(base_code)):
            raise TourError("taken", "That team is already taken.")
        if owner and await conn.fetchval("SELECT 1 FROM tour_teams WHERE tid=$1 AND owner_id=$2;", int(tid), int(owner["id"])):
            raise TourError("has_team", "You already own a team in this tournament.")
        team_no = int(await conn.fetchval("SELECT COALESCE(MAX(team_no), 0) + 1 FROM tour_teams WHERE tid=$1;", int(tid)))
        row = await conn.fetchrow(
            """
            INSERT INTO tour_teams(tid, team_no, code, base_code, name, short, owner_id, owner_name, owner_username, purse_left)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *;
            """,
            int(tid), team_no, str(code), str(base_code), str(name), short,
            int(owner["id"]) if owner else None, (owner or {}).get("first_name"), (owner or {}).get("username"), int(purse),
        )
        if owner:
            await _upsert_member(conn, int(tid), owner, team_no)
        return row

    return _row(await transaction(_tx)) or {}


async def _upsert_member(conn, tid: int, user: dict, team_no: int | None) -> None:
    await conn.execute(
        """
        INSERT INTO tour_members(tid, user_id, team_no, username, first_name, active)
        VALUES($1,$2,$3,$4,$5,TRUE)
        ON CONFLICT (tid, user_id) DO UPDATE SET team_no=EXCLUDED.team_no, username=EXCLUDED.username,
            first_name=EXCLUDED.first_name, active=TRUE;
        """,
        int(tid), int(user["id"]), team_no, user.get("username"), user.get("first_name"),
    )


async def assign_owner(tid: int, team_no: int, user: dict, allow_statuses: Iterable[str] = ("registration", "auction", "ready", "live")) -> dict:
    """Give a user a team. Raises TourError: closed / missing / owned / has_team."""

    async def _tx(conn):
        tour = await conn.fetchrow("SELECT status FROM tour_tournaments WHERE tid=$1 FOR UPDATE;", int(tid))
        if tour is None or tour["status"] not in set(allow_statuses):
            raise TourError("closed", "This tournament is not accepting changes right now.")
        row = await conn.fetchrow("SELECT * FROM tour_teams WHERE tid=$1 AND team_no=$2 FOR UPDATE;", int(tid), int(team_no))
        if row is None:
            raise TourError("missing", "Team not found.")
        if row["owner_id"] is not None and int(row["owner_id"]) != int(user["id"]):
            raise TourError("owned", "That team already has an owner.")
        if await conn.fetchval("SELECT 1 FROM tour_teams WHERE tid=$1 AND owner_id=$2 AND team_no<>$3;", int(tid), int(user["id"]), int(team_no)):
            raise TourError("has_team", "That user already owns another team here.")
        updated = await conn.fetchrow(
            "UPDATE tour_teams SET owner_id=$3, owner_name=$4, owner_username=$5 WHERE tid=$1 AND team_no=$2 RETURNING *;",
            int(tid), int(team_no), int(user["id"]), user.get("first_name"), user.get("username"),
        )
        await _upsert_member(conn, int(tid), user, int(team_no))
        return updated

    return _row(await transaction(_tx)) or {}


async def remove_owner(tid: int, team_no: int, delete_if_registration: bool = True) -> dict | None:
    """Take the owner off a team. During registration an emptied team slot disappears again."""

    async def _tx(conn):
        row = await conn.fetchrow("SELECT * FROM tour_teams WHERE tid=$1 AND team_no=$2 FOR UPDATE;", int(tid), int(team_no))
        if row is None:
            return None
        status = await conn.fetchval("SELECT status FROM tour_tournaments WHERE tid=$1;", int(tid))
        if row["owner_id"] is not None:
            await conn.execute("UPDATE tour_members SET active=FALSE, team_no=NULL WHERE tid=$1 AND user_id=$2;", int(tid), int(row["owner_id"]))
        if status == "registration" and delete_if_registration:
            await conn.execute("DELETE FROM tour_teams WHERE tid=$1 AND team_no=$2;", int(tid), int(team_no))
            return {**dict(row), "deleted": True}
        updated = await conn.fetchrow(
            "UPDATE tour_teams SET owner_id=NULL, owner_name=NULL, owner_username=NULL WHERE tid=$1 AND team_no=$2 RETURNING *;",
            int(tid), int(team_no))
        return dict(updated)

    return _row(await transaction(_tx))


async def drop_ownerless_teams(tid: int) -> int:
    rows = await fetch("DELETE FROM tour_teams WHERE tid=$1 AND owner_id IS NULL RETURNING team_no;", int(tid))
    return len(rows)


async def set_team_purse(tid: int, team_no: int, purse_lakh: int) -> None:
    await execute("UPDATE tour_teams SET purse_left=$3 WHERE tid=$1 AND team_no=$2;", int(tid), int(team_no), int(purse_lakh))


async def set_all_purses(tid: int, purse_lakh: int) -> None:
    await execute("UPDATE tour_teams SET purse_left=$2 WHERE tid=$1;", int(tid), int(purse_lakh))


async def rename_team(tid: int, team_no: int, name: str) -> None:
    await execute("UPDATE tour_teams SET name=$3 WHERE tid=$1 AND team_no=$2;", int(tid), int(team_no), name)


async def members(tid: int) -> list[dict]:
    return _rows(await fetch("SELECT * FROM tour_members WHERE tid=$1 ORDER BY joined_at;", int(tid)))


# ---------------------------------------------------------------------------
# fixtures + days
# ---------------------------------------------------------------------------
async def create_fixtures(tid: int, fixtures: list[dict]) -> list[dict]:
    """fixtures: [{stage, day_no, seq, home, away}] -> inserted rows (fno assigned in order)."""

    async def _tx(conn):
        base = int(await conn.fetchval("SELECT COALESCE(MAX(fno), 0) FROM tour_fixtures WHERE tid=$1;", int(tid)))
        out = []
        for index, fx in enumerate(fixtures, start=1):
            row = await conn.fetchrow(
                """
                INSERT INTO tour_fixtures(tid, fno, stage, day_no, seq, home_team_no, away_team_no)
                VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING *;
                """,
                int(tid), base + index, fx.get("stage", "league"), int(fx["day_no"]), int(fx.get("seq", index)),
                int(fx["home"]), int(fx["away"]),
            )
            out.append(dict(row))
        return out

    return await transaction(_tx)


async def fixtures(tid: int, status: str | None = None) -> list[dict]:
    if status:
        return _rows(await fetch("SELECT * FROM tour_fixtures WHERE tid=$1 AND status=$2 ORDER BY day_no, seq, fno;", int(tid), status))
    return _rows(await fetch("SELECT * FROM tour_fixtures WHERE tid=$1 ORDER BY day_no, seq, fno;", int(tid)))


async def fixture(tid: int, fno: int) -> dict | None:
    return _row(await fetchrow("SELECT * FROM tour_fixtures WHERE tid=$1 AND fno=$2;", int(tid), int(fno)))


async def fixture_by_match(engine: str, match_id: int) -> dict | None:
    return _row(await fetchrow(
        "SELECT * FROM tour_fixtures WHERE engine=$1 AND match_id=$2 ORDER BY tid DESC, fno DESC LIMIT 1;",
        str(engine).upper(), int(match_id)))


async def day_fixtures(tid: int, day_no: int) -> list[dict]:
    return _rows(await fetch("SELECT * FROM tour_fixtures WHERE tid=$1 AND day_no=$2 ORDER BY seq, fno;", int(tid), int(day_no)))


async def fixtures_of_user(tid: int, user_id: int) -> list[dict]:
    return _rows(await fetch(
        """
        SELECT f.* FROM tour_fixtures f
        JOIN tour_teams t ON t.tid = f.tid AND t.owner_id = $2 AND (t.team_no = f.home_team_no OR t.team_no = f.away_team_no)
        WHERE f.tid = $1 ORDER BY f.day_no, f.seq;
        """,
        int(tid), int(user_id)))


async def set_fixture_users(tid: int, fno: int, home_user: int | None, away_user: int | None) -> None:
    await execute("UPDATE tour_fixtures SET home_user_id=$3, away_user_id=$4 WHERE tid=$1 AND fno=$2 AND status<>'done';",
                  int(tid), int(fno), home_user, away_user)


async def set_presence(tid: int, fno: int, side: str, ready: bool = False) -> dict | None:
    """Mark one side present (and optionally ready). Presence never goes back to False."""
    present_col = "present_home" if side == "home" else "present_away"
    ready_col = "ready_home" if side == "home" else "ready_away"
    extra = f", {ready_col}=TRUE" if ready else ""
    return _row(await fetchrow(
        f"UPDATE tour_fixtures SET {present_col}=TRUE{extra} WHERE tid=$1 AND fno=$2 AND status='scheduled' RETURNING *;",
        int(tid), int(fno)))


async def queue_fixture(tid: int, fno: int) -> None:
    await execute("UPDATE tour_fixtures SET queued_at=COALESCE(queued_at, NOW()) WHERE tid=$1 AND fno=$2;", int(tid), int(fno))


async def claim_fixture_start(tid: int, fno: int, home_user: int, away_user: int, chat_id: int, engine: str) -> dict | None:
    """scheduled -> live (exactly once). The match id is attached afterwards with attach_match()."""
    return _row(await fetchrow(
        """
        UPDATE tour_fixtures SET status='live', started_at=NOW(), home_user_id=$3, away_user_id=$4, chat_id=$5, engine=$6,
               present_home=TRUE, present_away=TRUE
        WHERE tid=$1 AND fno=$2 AND status='scheduled' RETURNING *;
        """,
        int(tid), int(fno), int(home_user), int(away_user), int(chat_id), str(engine).upper()))


async def attach_match(tid: int, fno: int, match_id: int) -> None:
    await execute("UPDATE tour_fixtures SET match_id=$3 WHERE tid=$1 AND fno=$2;", int(tid), int(fno), int(match_id))


async def release_fixture(tid: int, fno: int) -> None:
    """A match that could not be created goes back to the queue."""
    await execute(
        "UPDATE tour_fixtures SET status='scheduled', started_at=NULL, match_id=NULL WHERE tid=$1 AND fno=$2 AND status='live';",
        int(tid), int(fno))


async def finish_fixture(tid: int, fno: int, result: dict, lines: list[dict]) -> dict | None:
    """Store the result (once) and the per-player lines. Returns the finished fixture or None if it was already done."""

    async def _tx(conn):
        row = await conn.fetchrow(
            """
            UPDATE tour_fixtures SET status='done', result_type=$3, winner_team_no=$4, winner_user_id=$5,
                   home_runs=$6, home_wkts=$7, home_balls=$8, away_runs=$9, away_wkts=$10, away_balls=$11,
                   result_text=$12, potm=$13, completed_at=NOW(),
                   present_home=TRUE, present_away=TRUE
            WHERE tid=$1 AND fno=$2 AND status IN ('live','scheduled') RETURNING *;
            """,
            int(tid), int(fno), result["result_type"], result.get("winner_team_no"), result.get("winner_user_id"),
            result.get("home_runs"), result.get("home_wkts"), result.get("home_balls"),
            result.get("away_runs"), result.get("away_wkts"), result.get("away_balls"),
            result.get("result_text"), result.get("potm"),
        )
        if row is None:
            return None
        for ln in lines:
            await conn.execute(
                """
                INSERT INTO tour_player_match(tid, fno, team_no, pkey, name, batted, runs, balls, fours, sixes, dismissed,
                    fifty_balls, hundred_balls, wickets, bowl_balls, bowl_runs, potm, mvp)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
                ON CONFLICT (tid, fno, team_no, pkey) DO UPDATE SET batted=EXCLUDED.batted, runs=EXCLUDED.runs,
                    balls=EXCLUDED.balls, fours=EXCLUDED.fours, sixes=EXCLUDED.sixes, dismissed=EXCLUDED.dismissed,
                    fifty_balls=EXCLUDED.fifty_balls, hundred_balls=EXCLUDED.hundred_balls, wickets=EXCLUDED.wickets,
                    bowl_balls=EXCLUDED.bowl_balls, bowl_runs=EXCLUDED.bowl_runs, potm=EXCLUDED.potm, mvp=EXCLUDED.mvp;
                """,
                int(tid), int(fno), int(ln["team_id"]), ln["player_key"], ln["player_name"], bool(ln.get("batted")),
                int(ln.get("runs") or 0), int(ln.get("balls") or 0), int(ln.get("fours") or 0), int(ln.get("sixes") or 0),
                bool(ln.get("dismissed")), ln.get("fifty_balls"), ln.get("hundred_balls"),
                int(ln.get("wickets") or 0), int(ln.get("bowl_balls") or 0), int(ln.get("bowl_runs") or 0),
                bool(ln.get("potm")), float(ln.get("mvp") or 0),
            )
        return dict(row)

    return await transaction(_tx)


async def reset_fixture(tid: int, fno: int) -> None:
    """Playoff fixture with no result: play it again."""
    await execute(
        "UPDATE tour_fixtures SET status='scheduled', engine=NULL, match_id=NULL, started_at=NULL, queued_at=NULL, "
        "present_home=FALSE, present_away=FALSE, ready_home=FALSE, ready_away=FALSE WHERE tid=$1 AND fno=$2;",
        int(tid), int(fno))


async def move_fixtures_to_day(tid: int, fnos: list[int], day_no: int) -> None:
    await execute("UPDATE tour_fixtures SET day_no=$3 WHERE tid=$1 AND fno = ANY($2::int[]) AND status<>'done';",
                  int(tid), list(fnos), int(day_no))


async def days(tid: int) -> list[dict]:
    return _rows(await fetch("SELECT * FROM tour_days WHERE tid=$1 ORDER BY day_no;", int(tid)))


async def day(tid: int, day_no: int) -> dict | None:
    return _row(await fetchrow("SELECT * FROM tour_days WHERE tid=$1 AND day_no=$2;", int(tid), int(day_no)))


async def create_day(tid: int, day_no: int, play_date, opens_at, remind_at, closes_at) -> dict | None:
    return _row(await fetchrow(
        """
        INSERT INTO tour_days(tid, day_no, play_date, opens_at, remind_at, closes_at)
        VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT (tid, day_no) DO NOTHING RETURNING *;
        """,
        int(tid), int(day_no), play_date, opens_at, remind_at, closes_at))


async def day_step(tid: int, day_no: int, from_status: str, to_status: str, stamp_col: str) -> dict | None:
    """pending->open / open->closed, exactly once; stamps opened_at / closed_at."""
    if stamp_col not in {"opened_at", "closed_at"}:
        raise ValueError(stamp_col)
    return _row(await fetchrow(
        f"UPDATE tour_days SET status=$4, {stamp_col}=NOW() WHERE tid=$1 AND day_no=$2 AND status=$3 RETURNING *;",
        int(tid), int(day_no), from_status, to_status))


async def mark_reminded(tid: int, day_no: int) -> dict | None:
    return _row(await fetchrow(
        "UPDATE tour_days SET reminded_at=NOW() WHERE tid=$1 AND day_no=$2 AND reminded_at IS NULL RETURNING *;",
        int(tid), int(day_no)))


async def set_day_card(tid: int, day_no: int, message_id: int | None) -> None:
    await execute("UPDATE tour_days SET card_msg_id=$3 WHERE tid=$1 AND day_no=$2;", int(tid), int(day_no), message_id)


async def shift_day(tid: int, day_no: int, play_date, opens_at, remind_at, closes_at) -> None:
    await execute(
        "UPDATE tour_days SET play_date=$3, opens_at=$4, remind_at=$5, closes_at=$6 WHERE tid=$1 AND day_no=$2;",
        int(tid), int(day_no), play_date, opens_at, remind_at, closes_at)


async def extend_day(tid: int, day_no: int, remind_at, closes_at) -> None:
    await execute(
        "UPDATE tour_days SET remind_at=$3, closes_at=$4, reminded_at=NULL WHERE tid=$1 AND day_no=$2;",
        int(tid), int(day_no), remind_at, closes_at)


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------
_BOARDS: dict[str, tuple[str, str]] = {
    # key: (select columns/aggregates, order by)  - grouped per player
    "orange": ("SUM(runs) AS value, SUM(balls) AS balls, COUNT(*) FILTER (WHERE batted) AS inns, MAX(runs) AS best",
               "value DESC, balls ASC"),
    "purple": ("SUM(wickets) AS value, SUM(bowl_runs) AS runs_given, SUM(bowl_balls) AS bowl_balls",
               "value DESC, runs_given ASC, bowl_balls DESC"),
    "mvp": ("ROUND(SUM(mvp)::numeric, 1) AS value, COUNT(*) AS matches", "value DESC"),
    "fifties": ("SUM(CASE WHEN runs >= 50 AND runs < 100 THEN 1 ELSE 0 END) AS value, SUM(runs) AS runs", "value DESC, runs DESC"),
    "hundreds": ("SUM(CASE WHEN runs >= 100 THEN 1 ELSE 0 END) AS value, SUM(runs) AS runs", "value DESC, runs DESC"),
    "sixes": ("SUM(sixes) AS value, SUM(runs) AS runs", "value DESC, runs DESC"),
    "fours": ("SUM(fours) AS value, SUM(runs) AS runs", "value DESC, runs DESC"),
    "sr": ("ROUND((SUM(runs) * 100.0 / NULLIF(SUM(balls), 0))::numeric, 1) AS value, SUM(runs) AS runs, SUM(balls) AS balls",
           "value DESC, runs DESC"),
    "economy": ("ROUND((SUM(bowl_runs) * 6.0 / NULLIF(SUM(bowl_balls), 0))::numeric, 2) AS value, SUM(wickets) AS wickets, SUM(bowl_balls) AS bowl_balls",
                "value ASC, wickets DESC"),
}
_BOARD_HAVING = {
    "orange": "SUM(runs) > 0", "purple": "SUM(wickets) > 0", "mvp": "COUNT(*) > 0",
    "fifties": "SUM(CASE WHEN runs >= 50 THEN 1 ELSE 0 END) > 0", "hundreds": "SUM(CASE WHEN runs >= 100 THEN 1 ELSE 0 END) > 0",
    "sixes": "SUM(sixes) > 0", "fours": "SUM(fours) > 0", "sr": "SUM(balls) >= 30", "economy": "SUM(bowl_balls) >= 12",
}
# single-innings records (one row per player per match)
_RECORDS: dict[str, tuple[str, str, str]] = {
    "fastest50": ("fifty_balls AS value, runs", "fifty_balls IS NOT NULL", "fifty_balls ASC, runs DESC"),
    "fastest100": ("hundred_balls AS value, runs", "hundred_balls IS NOT NULL", "hundred_balls ASC, runs DESC"),
    "highest": ("runs AS value, balls, dismissed", "batted", "runs DESC, balls ASC"),
    "bestbowl": ("wickets AS value, bowl_runs AS runs_given, bowl_balls", "wickets > 0", "wickets DESC, bowl_runs ASC"),
}
BOARD_KEYS = tuple(_BOARDS) + tuple(_RECORDS)


async def leaders(tid: int, board: str, limit: int = 10) -> list[dict]:
    """Top players for a board key (see BOARD_KEYS). Rows carry name, team_no and board specific columns."""
    if board in _BOARDS:
        cols, order = _BOARDS[board]
        having = _BOARD_HAVING[board]
        rows = await fetch(
            f"""
            SELECT MAX(name) AS name, team_no, {cols}
            FROM tour_player_match WHERE tid=$1
            GROUP BY team_no, pkey HAVING {having}
            ORDER BY {order}, name ASC LIMIT $2;
            """,
            int(tid), int(limit))
        return [dict(r) for r in rows]
    if board in _RECORDS:
        cols, where, order = _RECORDS[board]
        rows = await fetch(
            f"SELECT name, team_no, fno, {cols} FROM tour_player_match WHERE tid=$1 AND {where} ORDER BY {order}, name ASC LIMIT $2;",
            int(tid), int(limit))
        return [dict(r) for r in rows]
    raise ValueError(f"unknown board {board!r}")


async def career(user_id: int) -> dict[str, int]:
    """Lifetime tournament numbers for one user."""
    uid = int(user_id)
    played = int(await fetchval(
        """
        SELECT COUNT(*) FROM tour_members m JOIN tour_tournaments t ON t.tid = m.tid
        WHERE m.user_id=$1 AND t.status IN ('live','completed');
        """, uid) or 0)
    won = int(await fetchval("SELECT COUNT(*) FROM tour_tournaments WHERE champion_user_id=$1;", uid) or 0)
    runner = int(await fetchval("SELECT COUNT(*) FROM tour_tournaments WHERE runner_up_user_id=$1;", uid) or 0)
    row = await fetchrow(
        """
        SELECT COUNT(*) FILTER (WHERE winner_user_id = $1) AS wins,
               COUNT(*) FILTER (WHERE winner_user_id IS NOT NULL AND winner_user_id <> $1) AS losses
        FROM tour_fixtures
        WHERE status='done' AND result_type IN ('win','walkover') AND (home_user_id=$1 OR away_user_id=$1);
        """, uid)
    wins = int(row["wins"] or 0) if row else 0
    losses = int(row["losses"] or 0) if row else 0
    return {"played": played, "won": won, "runner_up": runner, "match_wins": wins, "match_losses": losses}


async def cleanup_candidates(days_after: int = 3) -> list[dict]:
    return _rows(await fetch(
        """
        SELECT * FROM tour_tournaments
        WHERE status IN ('completed','cancelled') AND cleaned=FALSE AND mode='auction'
          AND COALESCE(completed_at, created_at) < NOW() - ($1::int * INTERVAL '1 day');
        """, int(days_after)))


# ---------------------------------------------------------------------------
# schedule editing + worker probes
# ---------------------------------------------------------------------------
async def clear_schedule(tid: int) -> int:
    """Remove every fixture that has not started (and the days that are left empty). Refuses (returns -1)
    when a match is running or finished - a manual schedule can only replace an untouched one."""

    async def _tx(conn):
        if await conn.fetchval("SELECT 1 FROM tour_fixtures WHERE tid=$1 AND status IN ('live','done') LIMIT 1;", int(tid)):
            return -1
        rows = await conn.fetch("DELETE FROM tour_fixtures WHERE tid=$1 RETURNING fno;", int(tid))
        await conn.execute("DELETE FROM tour_days WHERE tid=$1;", int(tid))
        return len(rows)

    return int(await transaction(_tx))


async def _quiet_fetchrow(query: str, *args):
    from database.connection import get_pool
    async with get_pool().acquire() as conn:
        return await conn.fetchrow(query, *args)


async def _quiet_fetch(query: str, *args):
    from database.connection import get_pool
    async with get_pool().acquire() as conn:
        return await conn.fetch(query, *args)


async def live_ids() -> list[int]:
    return [int(r["tid"]) for r in await _quiet_fetch("SELECT tid FROM tour_tournaments WHERE status='live' ORDER BY tid;")]


async def probe(tid: int) -> dict | None:
    """What the match-day worker needs each tick: DB clock, unfinished/queued counts, the next unclosed day."""
    row = await _quiet_fetchrow(
        """
        SELECT NOW() AS db_now, t.last_tick_at, t.status,
          (SELECT COUNT(*) FROM tour_fixtures f WHERE f.tid=t.tid AND f.status<>'done') AS undone,
          (SELECT COUNT(*) FROM tour_fixtures f WHERE f.tid=t.tid AND f.status='scheduled' AND f.ready_home AND f.ready_away) AS queued
        FROM tour_tournaments t WHERE t.tid=$1;
        """, int(tid))
    if row is None:
        return None
    out = dict(row)
    day = await _quiet_fetchrow("SELECT * FROM tour_days WHERE tid=$1 AND status IN ('pending','open') ORDER BY day_no LIMIT 1;", int(tid))
    out["day"] = dict(day) if day else None
    return out


async def ready_fixtures(tid: int) -> list[dict]:
    return _rows(await _quiet_fetch(
        "SELECT * FROM tour_fixtures WHERE tid=$1 AND status='scheduled' AND ready_home AND ready_away ORDER BY COALESCE(queued_at, NOW()), day_no, seq;",
        int(tid)))


async def next_fixture_of_team(tid: int, team_no: int) -> dict | None:
    """The team's earliest fixture that has not been played (any day - players may play ahead of the calendar)."""
    return _row(await fetchrow(
        "SELECT * FROM tour_fixtures WHERE tid=$1 AND status IN ('scheduled','live') AND (home_team_no=$2 OR away_team_no=$2) "
        "ORDER BY day_no, seq LIMIT 1;", int(tid), int(team_no)))


async def non_closed_days(tid: int) -> list[dict]:
    return _rows(await fetch("SELECT * FROM tour_days WHERE tid=$1 AND status IN ('pending','open') ORDER BY day_no;", int(tid)))


async def reopen_day(tid: int, day_no: int) -> None:
    await execute("UPDATE tour_days SET status='open', closed_at=NULL WHERE tid=$1 AND day_no=$2;", int(tid), int(day_no))


async def clear_ready(tid: int, fno: int) -> None:
    await execute("UPDATE tour_fixtures SET present_home=FALSE, present_away=FALSE, ready_home=FALSE, ready_away=FALSE, queued_at=NULL "
                  "WHERE tid=$1 AND fno=$2 AND status='scheduled';", int(tid), int(fno))
