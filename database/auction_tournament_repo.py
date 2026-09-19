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

    json_fields = {
        "prize_player",
        "prize_breakdown",
        "pool_preview",
        "pool_errors",
    }
    encoded_clean = []
    for key, value in clean:
        if key in json_fields and value is not None and not isinstance(value, str):
            value = json.dumps(value, default=str)
        encoded_clean.append((key, value))

    clean = encoded_clean
    assignments = ", ".join(
        f"{key} = ${index + 2}"
        for index, (key, _value) in enumerate(clean)
    )
    args = [int(tournament_id), *[value for _key, value in clean]]
    row = await fetchrow(
        f"UPDATE auction_tournaments SET {assignments}, updated_at=NOW() WHERE tournament_id=$1 RETURNING *;",
        *args,
    )
    if row:
        await _sync_session_mirror(int(tournament_id))
    return row


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

    result = await transaction(_tx)
    if result[0]:
        await _sync_session_mirror(int(tournament_id))
    return result


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

    result = await transaction(_tx)
    if result[0]:
        await _sync_session_mirror(int(tournament_id))
    return result


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

    result = await transaction(_tx)
    await _sync_session_mirror(int(tournament_id))
    return result


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


RUNNING_TOURNAMENT_STATUSES = ACTIVE_DRAFT_STATUSES + ("created",)


async def _sync_session_mirror(tournament_id: int) -> None:
    try:
        from services.auction_tournament_session import sync_tournament_session
        await sync_tournament_session(int(tournament_id))
    except Exception as exc:
        print(f"[auction-repo] session mirror update failed for {tournament_id}: {exc!r}")


async def get_owned_running_tournaments(creator_id: int):
    return await fetch(
        """
        SELECT *
        FROM auction_tournaments
        WHERE creator_id=$1
          AND status = ANY($2::text[])
        ORDER BY tournament_id DESC;
        """,
        int(creator_id),
        list(RUNNING_TOURNAMENT_STATUSES),
    )


async def find_owned_running_tournament(creator_id: int, keyword: str):
    import re
    token = " ".join(str(keyword or "").strip().split()).casefold()
    token_compact = re.sub(r"[^a-z0-9]+", "", token)
    if not token:
        return None
    rows = await fetch(
        """
        SELECT *
        FROM auction_tournaments
        WHERE creator_id=$1
          AND status = ANY($2::text[])
        ORDER BY tournament_id DESC;
        """,
        int(creator_id),
        list(RUNNING_TOURNAMENT_STATUSES),
    )
    for row in rows:
        code = str(row.get("tournament_code") or "").casefold()
        name = " ".join(str(row.get("tournament_name") or "").split()).casefold()
        engine = str(row.get("engine_key") or "").casefold()
        aliases = {code, name, engine}
        aliases.discard("")
        compact_aliases = {re.sub(r"[^a-z0-9]+", "", alias) for alias in aliases}
        if token in aliases or token_compact in compact_aliases:
            return row
    return None


async def create_tournament_backup(tournament_id: int) -> dict:
    import hashlib
    import secrets
    from datetime import date, datetime

    def safe(value):
        if isinstance(value, dict):
            return {str(k): safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [safe(v) for v in value]
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, (bytes, bytearray)):
            return value.hex()
        try:
            import json as _json
            _json.dumps(value)
            return value
        except Exception:
            return str(value)

    async def _tx(conn):
        row = await conn.fetchrow(
            "SELECT * FROM auction_tournaments WHERE tournament_id=$1 FOR UPDATE;",
            int(tournament_id),
        )
        if not row:
            raise ValueError("Tournament not found.")

        teams = await conn.fetch(
            "SELECT * FROM auction_tournament_teams WHERE tournament_id=$1 ORDER BY tournament_team_id;",
            int(tournament_id),
        )
        pools = await conn.fetch(
            "SELECT * FROM auction_tournament_pools WHERE tournament_id=$1 ORDER BY pool_no;",
            int(tournament_id),
        )
        pool_players = await conn.fetch(
            "SELECT * FROM auction_tournament_pool_players WHERE tournament_id=$1 ORDER BY pool_id,pool_player_id;",
            int(tournament_id),
        )

        data = {
            "schema_version": 1,
            "backup_type": "crickium_auction_tournament",
            "tournament": safe(dict(row)),
            "teams": [safe(dict(r)) for r in teams],
            "pools": [safe(dict(r)) for r in pools],
            "pool_players": [safe(dict(r)) for r in pool_players],
        }
        import json as _json
        canonical = _json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        token = secrets.token_urlsafe(24)
        payload = {
            "backup_token": token,
            "backup_sha256": digest,
            "data": data,
        }
        raw = _json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        name_raw = str(row["tournament_name"] or "tournament").strip().lower()
        safe_name = "".join(ch if ch.isalnum() else "_" for ch in name_raw).strip("_")[:40] or "tournament"
        filename = f"crickium_tournament_{int(tournament_id)}_{safe_name}.json"

        backup_row = await conn.fetchrow(
            """
            INSERT INTO auction_tournament_backups(
                backup_token, backup_sha256, creator_id, original_tournament_id,
                tournament_name, filename, payload
            )
            VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)
            RETURNING backup_id, backup_token, backup_sha256, filename, creator_id, original_tournament_id;
            """,
            token, digest, int(row["creator_id"]), int(row["tournament_id"]),
            str(row["tournament_name"] or "Indian Premier League"), filename, canonical,
        )

        await conn.execute(
            "DELETE FROM auction_tournaments WHERE tournament_id=$1;",
            int(tournament_id),
        )

        return {
            **dict(backup_row),
            "payload": data,
            "raw": raw.encode("utf-8"),
            "backup_token": token,
            "backup_sha256": digest,
            "tournament": data["tournament"],
            "teams": data["teams"],
            "pools": data["pools"],
            "pool_players": data["pool_players"],
        }

    result = await transaction(_tx)
    try:
        from services.auction_tournament_session import sync_tournament_session
        await sync_tournament_session(int(tournament_id))
    except Exception:
        pass
    return result


async def lookup_tournament_backup(payload: dict, requester_id: int) -> dict | None:
    import hashlib
    import json as _json

    token = str(payload.get("backup_token") or "")
    data = payload.get("data") or {}
    if not token or not isinstance(data, dict):
        return None
    canonical = _json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if str(payload.get("backup_sha256") or "") != digest:
        return None
    row = await fetchrow(
        """
        SELECT *
        FROM auction_tournament_backups
        WHERE backup_token=$1
          AND backup_sha256=$2
          AND creator_id=$3
        LIMIT 1;
        """,
        token, digest, int(requester_id),
    )
    return dict(row) if row else None


async def restore_tournament_backup(backup_row: dict) -> dict:
    payload = backup_row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    tournament = dict(payload["tournament"])
    teams = list(payload.get("teams") or [])
    pools = list(payload.get("pools") or [])
    pool_players = list(payload.get("pool_players") or [])

    json_fields = {
        "prize_player", "prize_breakdown", "pool_preview", "pool_errors",
    }

    async def _tx(conn):
        restored = await conn.fetchrow(
            """
            INSERT INTO auction_tournaments(
                creator_id,creator_username,creator_name,engine_key,tournament_code,
                tournament_name,auction_mode,status,creation_chat_id,prize_coins,
                prize_rubies,prize_player,prize_breakdown,pool_preview,pool_errors,
                pool_count,player_count,host_group_id,host_group_name,host_group_username,
                group_registration_message_id,self_registration_enabled,created_at,updated_at,completed_at
            )
            VALUES(
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb,$14::jsonb,$15::jsonb,
                $16,$17,$18,$19,$20,NULL,$21,NOW(),NOW(),NULL
            )
            RETURNING *;
            """,
            int(tournament["creator_id"]), tournament.get("creator_username"), tournament.get("creator_name"),
            tournament.get("engine_key") or "PLAYIPL", tournament.get("tournament_code") or "IPL",
            tournament.get("tournament_name") or "Indian Premier League", bool(tournament.get("auction_mode", True)),
            tournament.get("status") or "created", int(tournament.get("creation_chat_id") or 0),
            int(tournament.get("prize_coins") or 0), int(tournament.get("prize_rubies") or 0),
            json.dumps(tournament.get("prize_player")) if tournament.get("prize_player") is not None else "null",
            json.dumps(tournament.get("prize_breakdown") or []),
            json.dumps(tournament.get("pool_preview")) if tournament.get("pool_preview") is not None else "null",
            json.dumps(tournament.get("pool_errors")) if tournament.get("pool_errors") is not None else "null",
            int(tournament.get("pool_count") or 0), int(tournament.get("player_count") or 0),
            int(tournament["host_group_id"]) if tournament.get("host_group_id") else None,
            tournament.get("host_group_name"), tournament.get("host_group_username"),
            bool(tournament.get("self_registration_enabled")),
        )
        tid = int(restored["tournament_id"])

        for row in teams:
            await conn.execute(
                """
                INSERT INTO auction_tournament_teams(
                    tournament_id,team_code,team_name,owner_user_id,owner_username,owner_name,ownership_source
                ) VALUES($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (tournament_id,team_code) DO UPDATE SET
                    team_name=EXCLUDED.team_name,
                    owner_user_id=EXCLUDED.owner_user_id,
                    owner_username=EXCLUDED.owner_username,
                    owner_name=EXCLUDED.owner_name,
                    ownership_source=EXCLUDED.ownership_source;
                """,
                tid, row.get("team_code"), row.get("team_name"),
                row.get("owner_user_id"), row.get("owner_username"), row.get("owner_name"), row.get("ownership_source"),
            )

        pool_id_map = {}
        for row in pools:
            inserted = await conn.fetchrow(
                """
                INSERT INTO auction_tournament_pools(tournament_id,pool_no,pool_name,base_price)
                VALUES($1,$2,$3,$4)
                RETURNING pool_id;
                """,
                tid, int(row.get("pool_no") or 0), row.get("pool_name") or "Pool", int(row.get("base_price") or 0),
            )
            pool_id_map[str(row.get("pool_id"))] = int(inserted["pool_id"])

        for row in pool_players:
            old_pool_id = str(row.get("pool_id"))
            new_pool_id = pool_id_map.get(old_pool_id)
            if not new_pool_id:
                continue
            await conn.execute(
                """
                INSERT INTO auction_tournament_pool_players(
                    tournament_id,pool_id,identity_key,player_id,special_player_id,
                    player_name,edition,is_special,ovr,country,role,bat_level,bowl_level,
                    batting_hand,bowling_hand,base_price,status,sold_to_user_id,sold_price
                ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19);
                """,
                tid, new_pool_id, row.get("identity_key"), row.get("player_id"), row.get("special_player_id"),
                row.get("player_name"), row.get("edition"), bool(row.get("is_special")), int(row.get("ovr") or 0),
                row.get("country"), row.get("role"), int(row.get("bat_level") or 0), int(row.get("bowl_level") or 0),
                row.get("batting_hand"), row.get("bowling_hand"), row.get("base_price"),
                row.get("status") or "available", row.get("sold_to_user_id"), row.get("sold_price"),
            )

        await conn.execute(
            "UPDATE auction_tournament_backups SET restored_tournament_id=$2, restored_at=NOW() WHERE backup_id=$1;",
            int(backup_row["backup_id"]), tid,
        )
        return dict(restored)

    return await transaction(_tx)
