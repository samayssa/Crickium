from __future__ import annotations

import json
from typing import Any

from database.query import execute, fetch, fetchrow, fetchval, transaction

ACTIVE_DRAFT_STATUSES = (
    "select_mode",
    "await_prize",
    "confirm_prize",
    "overview",
    "await_pool",
    "confirm_pool",
    "ask_group",
    "await_group",
    "confirm_group",
    "final_confirm",
)


async def get_active_draft(creator_id: int):
    return await fetchrow(
        """
        SELECT *
        FROM auction_tournaments
        WHERE creator_id=$1
          AND status = ANY($2::text[])
        ORDER BY tournament_id DESC
        LIMIT 1;
        """,
        int(creator_id),
        list(ACTIVE_DRAFT_STATUSES),
    )


async def create_draft(
    creator_id: int,
    creator_username: str | None,
    creator_name: str | None,
    chat_id: int,
) -> dict:
    async def _tx(conn):
        old = await conn.fetchrow(
            """
            SELECT * FROM auction_tournaments
            WHERE creator_id=$1
              AND status = ANY($2::text[])
            ORDER BY tournament_id DESC
            LIMIT 1
            FOR UPDATE;
            """,
            int(creator_id),
            list(ACTIVE_DRAFT_STATUSES),
        )
        if old:
            return dict(old)

        row = await conn.fetchrow(
            """
            INSERT INTO auction_tournaments(
                creator_id, creator_username, creator_name,
                engine_key, tournament_code, tournament_name,
                auction_mode, status, creation_chat_id
            )
            VALUES($1,$2,$3,'PLAYIPL','IPL','Indian Premier League',TRUE,'select_mode',$4)
            RETURNING *;
            """,
            int(creator_id), creator_username, creator_name, int(chat_id),
        )
        return dict(row)

    return await transaction(_tx)


async def get_tournament(tournament_id: int):
    return await fetchrow(
        "SELECT * FROM auction_tournaments WHERE tournament_id=$1;",
        int(tournament_id),
    )


async def update_tournament(tournament_id: int, **fields: Any):
    allowed = {
        "status",
        "auction_mode",
        "tournament_name",
        "prize_coins",
        "prize_rubies",
        "prize_player",
        "prize_breakdown",
        "pool_preview",
        "pool_errors",
        "pool_count",
        "player_count",
        "host_group_id",
        "host_group_name",
        "host_group_username",
        "group_registration_message_id",
        "self_registration_enabled",
        "overview_message_id",
        "prompt_message_id",
        "created_at_message_id",
    }
    clean = [(k, v) for k, v in fields.items() if k in allowed]
    if not clean:
        return await get_tournament(tournament_id)

    assignments = ", ".join(
        f"{key} = ${index + 2}"
        for index, (key, _value) in enumerate(clean)
    )
    args = [int(tournament_id), *[value for _key, value in clean]]
    return await fetchrow(
        f"UPDATE auction_tournaments SET {assignments}, updated_at=NOW() WHERE tournament_id=$1 RETURNING *;",
        *args,
    )


async def create_ipl_teams(tournament_id: int, teams: list[str]):
    async def _tx(conn):
        for code in teams:
            await conn.execute(
                """
                INSERT INTO auction_tournament_teams(
                    tournament_id, team_code, team_name
                )
                VALUES($1,$2,$3)
                ON CONFLICT (tournament_id,team_code) DO NOTHING;
                """,
                int(tournament_id), code, code,
            )

    await transaction(_tx)


async def fetch_teams(tournament_id: int):
    return await fetch(
        """
        SELECT *
        FROM auction_tournament_teams
        WHERE tournament_id=$1
        ORDER BY team_code;
        """,
        int(tournament_id),
    )


async def get_team(tournament_id: int, team_code: str):
    return await fetchrow(
        """
        SELECT *
        FROM auction_tournament_teams
        WHERE tournament_id=$1 AND team_code=$2;
        """,
        int(tournament_id), team_code,
    )


async def get_user_registration(tournament_id: int, user_id: int):
    return await fetchrow(
        """
        SELECT *
        FROM auction_tournament_teams
        WHERE tournament_id=$1 AND owner_user_id=$2;
        """,
        int(tournament_id), int(user_id),
    )


async def assign_team(
    tournament_id: int,
    team_code: str,
    user_id: int,
    username: str | None,
    first_name: str | None,
    source: str,
) -> tuple[bool, str]:
    async def _tx(conn):
        tournament = await conn.fetchrow(
            "SELECT status FROM auction_tournaments WHERE tournament_id=$1 FOR UPDATE;",
            int(tournament_id),
        )
        if not tournament or tournament["status"] != "created":
            return False, "Tournament is no longer accepting team registrations."

        target = await conn.fetchrow(
            """
            SELECT * FROM auction_tournament_teams
            WHERE tournament_id=$1 AND team_code=$2
            FOR UPDATE;
            """,
            int(tournament_id), team_code,
        )
        if not target:
            return False, "That franchise does not exist in this tournament."
        if target["owner_user_id"] is not None:
            return False, "That franchise has already been assigned."

        existing = await conn.fetchrow(
            """
            SELECT team_code FROM auction_tournament_teams
            WHERE tournament_id=$1 AND owner_user_id=$2
            FOR UPDATE;
            """,
            int(tournament_id), int(user_id),
        )
        if existing:
            return False, f"You already own {existing['team_code']} in this tournament."

        await conn.execute(
            """
            UPDATE auction_tournament_teams
            SET owner_user_id=$3,
                owner_username=$4,
                owner_name=$5,
                ownership_source=$6,
                updated_at=NOW()
            WHERE tournament_id=$1 AND team_code=$2;
            """,
            int(tournament_id), team_code, int(user_id), username, first_name, source,
        )
        return True, "Team assigned successfully."

    return await transaction(_tx)


async def remove_team_owner(tournament_id: int, team_code: str, user_id: int) -> tuple[bool, str]:
    async def _tx(conn):
        row = await conn.fetchrow(
            """
            SELECT * FROM auction_tournament_teams
            WHERE tournament_id=$1 AND team_code=$2
            FOR UPDATE;
            """,
            int(tournament_id), team_code,
        )
        if not row:
            return False, "That franchise does not exist."
        if int(row["owner_user_id"] or 0) != int(user_id):
            return False, "That user is not the owner of this franchise."

        await conn.execute(
            """
            UPDATE auction_tournament_teams
            SET owner_user_id=NULL,
                owner_username=NULL,
                owner_name=NULL,
                ownership_source=NULL,
                updated_at=NOW()
            WHERE tournament_id=$1 AND team_code=$2;
            """,
            int(tournament_id), team_code,
        )
        return True, "Team owner removed."

    return await transaction(_tx)


async def create_pools(tournament_id: int, pools: list[dict]) -> tuple[int, int]:
    async def _tx(conn):
        await conn.execute(
            "DELETE FROM auction_tournament_pool_players WHERE tournament_id=$1;",
            int(tournament_id),
        )
        await conn.execute(
            "DELETE FROM auction_tournament_pools WHERE tournament_id=$1;",
            int(tournament_id),
        )

        pool_count = 0
        player_count = 0
        for pool in pools:
            pool_row = await conn.fetchrow(
                """
                INSERT INTO auction_tournament_pools(
                    tournament_id,pool_no,pool_name,base_price
                )
                VALUES($1,$2,$3,$4)
                RETURNING pool_id;
                """,
                int(tournament_id),
                int(pool["pool_no"]),
                pool["pool_name"],
                int(pool["base_price"]),
            )
            pool_id = int(pool_row["pool_id"])
            pool_count += 1
            for player in pool["players"]:
                await conn.execute(
                    """
                    INSERT INTO auction_tournament_pool_players(
                        tournament_id,pool_id,identity_key,player_id,special_player_id,
                        player_name,edition,is_special,ovr,country,role,
                        bat_level,bowl_level,batting_hand,bowling_hand,base_price,status
                    )
                    VALUES(
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,'available'
                    );
                    """,
                    int(tournament_id), pool_id, player["identity_key"],
                    player.get("player_id"), player.get("special_player_id"),
                    player["name"], player.get("edition"), bool(player["is_special"]),
                    int(player["ovr"]), player.get("country"), player.get("role"),
                    int(player.get("bat_level") or 0), int(player.get("bowl_level") or 0),
                    player.get("batting_hand"), player.get("bowling_hand"), int(pool["base_price"]),
                )
                player_count += 1
        await conn.execute(
            """
            UPDATE auction_tournaments
            SET pool_count=$2, player_count=$3, updated_at=NOW()
            WHERE tournament_id=$1;
            """,
            int(tournament_id), pool_count, player_count,
        )
        return pool_count, player_count

    return await transaction(_tx)


async def fetch_pool_summary(tournament_id: int):
    rows = await fetch(
        """
        SELECT p.pool_no,p.pool_name,p.base_price,
               COUNT(pp.pool_player_id) AS player_count
        FROM auction_tournament_pools p
        LEFT JOIN auction_tournament_pool_players pp
          ON pp.pool_id=p.pool_id
        WHERE p.tournament_id=$1
        GROUP BY p.pool_id
        ORDER BY p.pool_no;
        """,
        int(tournament_id),
    )
    return rows


async def fetch_all_pool_players(tournament_id: int):
    return await fetch(
        """
        SELECT p.pool_no,p.pool_name,p.base_price,pp.*
        FROM auction_tournament_pools p
        JOIN auction_tournament_pool_players pp ON pp.pool_id=p.pool_id
        WHERE p.tournament_id=$1
        ORDER BY p.pool_no, pp.player_name;
        """,
        int(tournament_id),
    )


async def find_user_by_identifier(identifier: str):
    token = str(identifier or "").strip()
    if not token:
        return None
    if token.startswith("@"):
        token = token[1:]
        return await fetchrow(
            "SELECT * FROM users WHERE LOWER(username)=LOWER($1) LIMIT 1;",
            token,
        )
    if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
        return await fetchrow(
            "SELECT * FROM users WHERE user_id=$1 LIMIT 1;",
            int(token),
        )
    return await fetchrow(
        "SELECT * FROM users WHERE LOWER(username)=LOWER($1) LIMIT 1;",
        token,
    )


async def get_tournament_for_prize(chat_id: int, user_id: int):
    row = await fetchrow(
        """
        SELECT * FROM auction_tournaments
        WHERE status='created'
          AND host_group_id=$1
        ORDER BY tournament_id DESC
        LIMIT 1;
        """,
        int(chat_id),
    )
    if row:
        return row

    return await fetchrow(
        """
        SELECT * FROM auction_tournaments
        WHERE status='created'
          AND creator_id=$1
        ORDER BY tournament_id DESC
        LIMIT 1;
        """,
        int(user_id),
    )


def json_load(value: Any, default):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return default
    return value
