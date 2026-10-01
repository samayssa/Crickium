"""
Generic export/import engine for complete and partial PostgreSQL backups.

/ sync creates a complete logical backup of every public application table
except the internal ``schema_version`` bookkeeping table.  The table list is
resolved from PostgreSQL itself, so newly-added application tables are not
silently omitted from future backups.

/cleardata keeps its existing targeted backup behaviour.
/recover restores either backup format transactionally.

Backup files are gzip-compressed JSON. Binary BYTEA values and Decimal values
are explicitly encoded so the backup is genuinely round-trippable instead of
being stringified and silently corrupted.
"""
from __future__ import annotations

import base64
import gzip
import json
import uuid
import re
from collections import defaultdict, deque
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Iterable

from database.connection import get_pool
from database.admin_repo import CLEAR_TABLES

BACKUP_FORMAT_VERSION = 2
SCHEMA_BOOKKEEPING_TABLES = {"schema_version"}

# Tables that are cascade-deleted by /cleardata's TRUNCATE ... CASCADE even
# though they are not directly listed in CLEAR_TABLES.
_CLEARDATA_CASCADE_EXTRAS = [
    "player_card_images",
    "special_player_card_images",
    "daily_rewards",
]

CLEARDATA_BACKUP_TABLES = [*CLEAR_TABLES, *_CLEARDATA_CASCADE_EXTRAS]

# Tables that /cleardata must never include.
NEVER_CLEARED_TABLES = [
    "tier_card_images",
    "template_card_image",
    "authorized_uploaders",
    "play_matches",
    "stadium_images",
]

# /recover treats these identity registries as merge-only data. Current rows
# always win; rows that exist only in the backup are inserted. All other
# application data is restored from the backup as the authoritative state.
# Users are keyed by Telegram user_id; known groups/channels are keyed by chat_id.
PROTECTED_MERGE_KEYS = {
    "users": "user_id",
    "broadcast_targets": "chat_id",
}

# Compatibility list for callers/tools that imported this constant from an
# older version. /sync itself no longer depends on this fixed list.
FULL_BACKUP_TABLES = [
    "bot_runtime_state",
    "users",
    "players",
    "playint_players",
    "playint_matches",
    "recent_playing_xis",
    "matches",
    "player_stats",
    "team_squads",
    "referrals",
    "match_challenges",
    "player_claims",
    "player_user_match_stats",
    "broadcast_targets",
    "team_lineups",
    "probability_profiles",
    "special_edition_players",
    "special_player_card_images",
    "authorized_uploaders",
    "player_card_images",
    "template_card_image",
    "play_matches",
    "playso_matches",
    "playipl_matches",
    "stadium_images",
    "auction_tournaments",
    "auction_tournament_teams",
    "auction_tournament_pools",
    "auction_tournament_backups",
    "auction_tournament_pool_players",
    "daily_rewards",
    "coin_exchange_requests",
    "team_logo_requests",
    "tier_card_images",
    "upgrade_catalog",
    "upgrade_catalog_tiers",
    "user_player_upgrades",
    "user_player_loadouts",
    "match_player_upgrade_snapshots",
    "h2h_matches",
    "trade_requests",
    "game_session_snapshots",
]

assert set(CLEARDATA_BACKUP_TABLES).isdisjoint(NEVER_CLEARED_TABLES), (
    "A table meant to survive /cleardata ended up in its backup/clear set."
)

# Parent-before-child order used as a fast-path preference. Dynamic foreign
# key ordering is still calculated at restore time so newly-added tables are
# handled safely.
_RESTORE_ORDER = FULL_BACKUP_TABLES[:]


def _json_default(value):
    # asyncpg may expose PostgreSQL UUID values using its own UUID class
    # (`asyncpg.pgproto.pgproto.UUID`) rather than the stdlib uuid.UUID.
    # Normalize both forms to a stable JSON marker so /sync can always
    # serialize the complete database without leaking a driver-specific
    # Python object into json.dumps().
    if isinstance(value, uuid.UUID) or (
        type(value).__name__ == "UUID"
        and type(value).__module__.startswith("asyncpg.pgproto")
    ):
        return {"__backup_type__": "uuid", "value": str(value)}

    if isinstance(value, datetime):
        return {"__backup_type__": "datetime", "value": value.isoformat()}

    if isinstance(value, date):
        return {"__backup_type__": "date", "value": value.isoformat()}

    if isinstance(value, time):
        return {"__backup_type__": "time", "value": value.isoformat()}

    if isinstance(value, Decimal):
        return {"__backup_type__": "decimal", "value": str(value)}

    if isinstance(value, memoryview):
        value = value.tobytes()

    if isinstance(value, (bytes, bytearray)):
        return {
            "__backup_type__": "bytes",
            "base64": base64.b64encode(bytes(value)).decode("ascii"),
        }

    raise TypeError(f"Unsupported backup value type: {type(value)!r}")


def _json_object_hook(value):
    # Keep markers as dictionaries until we have the PostgreSQL column type.
    return value


def _decode_backup(raw_bytes: bytes) -> dict:
    try:
        raw = gzip.decompress(raw_bytes)
    except OSError:
        raw = raw_bytes
    return json.loads(raw.decode("utf-8"), object_hook=_json_object_hook)


async def _public_base_tables(
    conn,
    *,
    include_schema_bookkeeping: bool = False,
) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name;
        """
    )

    excluded = (
        set()
        if include_schema_bookkeeping
        else SCHEMA_BOOKKEEPING_TABLES
    )

    return [
        str(row["table_name"])
        for row in rows
        if str(row["table_name"]) not in excluded
    ]


async def get_full_backup_tables() -> list[str]:
    """Return every public application table currently present in PostgreSQL."""
    pool = get_pool()

    async with pool.acquire() as conn:
        return await _public_base_tables(conn)


async def export_tables(
    table_names: Iterable[str] | None = None,
    *,
    backup_type: str,
) -> bytes:
    """Export requested tables, or every public application table for /sync."""
    pool = get_pool()

    async with pool.acquire() as conn:
        if backup_type == "sync" and table_names is None:
            tables_to_export = await _public_base_tables(conn)

        elif table_names is None:
            raise ValueError(
                "table_names is required for non-sync backups"
            )

        else:
            tables_to_export = list(
                dict.fromkeys(str(t) for t in table_names)
            )

        # Validate names against information_schema instead of trusting a
        # caller-provided identifier inside a SQL string.
        current_tables = set(
            await _public_base_tables(
                conn,
                include_schema_bookkeeping=False,
            )
        )

        invalid = sorted(
            set(tables_to_export) - current_tables
        )

        if invalid:
            raise ValueError(
                "Backup requested unknown public table(s): "
                + ", ".join(invalid)
            )

        tables: dict[str, list[dict]] = {}
        row_counts: dict[str, int] = {}

        for table in tables_to_export:
            rows = await conn.fetch(
                f'SELECT * FROM "{table}";'
            )

            tables[table] = [dict(row) for row in rows]
            row_counts[table] = len(rows)

    payload = {
        "backup_format_version": BACKUP_FORMAT_VERSION,
        "backup_type": backup_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "schema_tables": (
            tables_to_export
            if backup_type == "sync"
            else None
        ),
        "table_count": len(tables_to_export),
        "row_counts": row_counts,
        "tables": tables,
    }

    raw = json.dumps(
        payload,
        default=_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    compressed = gzip.compress(
        raw,
        compresslevel=6,
    )

    print(
        f"[backup_repo] export_tables({backup_type}) -> "
        f"{len(tables_to_export)} tables, "
        f"{sum(row_counts.values())} rows, "
        f"{len(compressed)} bytes gzip"
    )

    return compressed


def _parse_datetime(
    value,
    *,
    with_timezone: bool,
):
    if isinstance(value, datetime):
        parsed = value

    elif isinstance(value, date) and not isinstance(
        value,
        datetime,
    ):
        parsed = datetime.combine(
            value,
            time.min,
        )

    else:
        text = str(value).strip()

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        parsed = datetime.fromisoformat(text)

    if with_timezone:
        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

    elif parsed.tzinfo is not None:
        parsed = (
            parsed.astimezone(timezone.utc)
            .replace(tzinfo=None)
        )

    return parsed


def _decode_marker(
    value,
    pg_type: str,
):
    if not isinstance(value, dict):
        return value

    marker = value.get(
        "__backup_type__"
    )

    if marker == "bytes":
        return base64.b64decode(
            value["base64"].encode("ascii")
        )

    if marker == "decimal":
        return Decimal(
            str(value["value"])
        )

    if marker == "date":
        return date.fromisoformat(
            str(value["value"]).split(
                "T",
                1,
            )[0]
        )

    if marker == "time":
        text = str(value["value"])

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        return time.fromisoformat(text)

    if marker == "datetime":
        text = str(value["value"])

        return _parse_datetime(
            text,
            with_timezone=(
                pg_type == "timestamp with time zone"
            ),
        )

    if marker == "uuid":
        return uuid.UUID(
            str(value["value"])
        )

    return value


def _coerce_value(
    value,
    pg_type: str,
):
    if value is None:
        return None

    value = _decode_marker(
        value,
        pg_type,
    )

    if pg_type in {
        "timestamp without time zone",
        "timestamp with time zone",
    }:
        return _parse_datetime(
            value,
            with_timezone=(
                pg_type
                == "timestamp with time zone"
            ),
        )

    if pg_type == "date":
        if isinstance(value, date) and not isinstance(
            value,
            datetime,
        ):
            return value

        return date.fromisoformat(
            str(value).split(
                "T",
                1,
            )[0]
        )

    if pg_type.startswith("time"):
        if isinstance(value, time):
            return value

        text = str(value).strip()

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        return time.fromisoformat(text)

    if pg_type in {
        "json",
        "jsonb",
    }:
        return (
            value
            if isinstance(value, str)
            else json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    if pg_type in {
        "numeric",
        "decimal",
    }:
        return (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value))
        )

    # asyncpg accepts ordinary Python lists for PostgreSQL arrays.
    # The current Crickium schema does not use arrays, but leaving
    # lists untouched makes the backup engine future-friendly.
    return value


async def _table_column_metadata(
    conn,
    table: str,
) -> dict[str, dict]:
    rows = await conn.fetch(
        """
        SELECT
            column_name,
            data_type,
            udt_name,
            column_default,
            is_identity
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = $1
        ORDER BY ordinal_position;
        """,
        table,
    )

    if not rows:
        raise ValueError(
            f"Backup contains unknown table: {table!r}"
        )

    return {
        row["column_name"]: dict(row)
        for row in rows
    }


async def _foreign_key_order(
    conn,
    tables: list[str],
) -> list[str]:
    """Topologically order tables so parents are inserted before children."""
    if not tables:
        return []

    rows = await conn.fetch(
        """
        SELECT
            tc.table_name AS child_table,
            ccu.table_name AS parent_table
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_schema = tc.constraint_schema
         AND ccu.constraint_name = tc.constraint_name
         AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = 'public'
          AND ccu.table_schema = 'public';
        """
    )

    table_set = set(tables)

    parents: dict[str, set[str]] = {
        table: set()
        for table in tables
    }

    children: dict[str, set[str]] = {
        table: set()
        for table in tables
    }

    indegree: dict[str, int] = {
        table: 0
        for table in tables
    }

    for row in rows:
        child = str(
            row["child_table"]
        )

        parent = str(
            row["parent_table"]
        )

        if (
            child == parent
            or child not in table_set
            or parent not in table_set
        ):
            continue

        if parent in parents[child]:
            continue

        parents[child].add(parent)
        children[parent].add(child)
        indegree[child] += 1

    preferred = {
        name: i
        for i, name in enumerate(
            _RESTORE_ORDER
        )
    }

    queue = deque(
        sorted(
            (
                t
                for t in tables
                if indegree[t] == 0
            ),
            key=lambda x: (
                preferred.get(x, 10_000),
                x,
            ),
        )
    )

    ordered: list[str] = []

    while queue:
        node = queue.popleft()
        ordered.append(node)

        for child in sorted(
            children[node],
            key=lambda x: (
                preferred.get(x, 10_000),
                x,
            ),
        ):
            indegree[child] -= 1

            if indegree[child] == 0:
                queue.append(child)

    # The current schema has no FK cycles. If a future schema introduces one,
    # append the remaining tables deterministically and let PostgreSQL surface
    # the actual constraint error rather than silently dropping data.
    if len(ordered) != len(tables):
        remaining = sorted(
            set(tables) - set(ordered),
            key=lambda x: (
                preferred.get(x, 10_000),
                x,
            ),
        )

        ordered.extend(
            remaining
        )

    return ordered


async def _reset_sequences(
    conn,
    tables: list[str],
    metadata: dict[str, dict[str, dict]],
):
    """Reset SERIAL/identity-backed sequences after a logical restore.

    PostgreSQL exposes pg_get_serial_sequence(); CockroachDB does not require
    the application to depend on that PostgreSQL helper. For CockroachDB we
    resolve sequence names from the column default and use ALTER SEQUENCE.
    """

    version_text = str(
        await conn.fetchval(
            "SELECT version();"
        )
        or ""
    )

    is_cockroach = (
        "cockroachdb"
        in version_text.lower()
    )

    for table in tables:
        columns = metadata.get(
            table,
            {},
        )

        for column, info in columns.items():
            default = str(
                info.get(
                    "column_default"
                )
                or ""
            )

            is_identity = (
                info.get("is_identity")
                == "YES"
            )

            if (
                "nextval(" not in default
                and not is_identity
            ):
                continue

            max_value = await conn.fetchval(
                f'SELECT MAX("{column}") FROM "{table}";'
            )

            next_value = (
                1
                if max_value is None
                else int(max_value) + 1
            )

            if is_cockroach:
                # Typical PostgreSQL-compatible SERIAL defaults are of the form
                # nextval('public.table_id_seq'::REGCLASS). CockroachDB exposes
                # the same default text for sequence-backed SERIAL columns.
                match = re.search(
                    r"nextval\('([^']+)'",
                    default,
                )

                if not match:
                    # Identity/managed columns do not always expose a nextval()
                    # default. Leave those alone; their identity mechanism owns
                    # its own counter and the restored explicit IDs remain valid.
                    continue

                raw_sequence = match.group(1)

                sequence_parts = [
                    part.strip().strip('"')
                    for part in raw_sequence.split(".")
                ]

                if len(sequence_parts) == 1:
                    sequence_schema = "public"
                    sequence_name = sequence_parts[0]
                else:
                    sequence_schema = sequence_parts[-2]
                    sequence_name = sequence_parts[-1]

                def _ident(value: str) -> str:
                    return (
                        '"'
                        + value.replace(
                            '"',
                            '""',
                        )
                        + '"'
                    )

                await conn.execute(
                    f'ALTER SEQUENCE '
                    f'{_ident(sequence_schema)}.'
                    f'{_ident(sequence_name)} '
                    f'RESTART WITH {next_value};'
                )

                continue

            sequence_name = await conn.fetchval(
                "SELECT pg_get_serial_sequence($1, $2);",
                f"public.{table}",
                column,
            )

            if not sequence_name:
                continue

            if max_value is None:
                await conn.execute(
                    "SELECT setval($1, 1, false);",
                    sequence_name,
                )

            else:
                await conn.execute(
                    "SELECT setval($1, $2, false);",
                    sequence_name,
                    next_value,
                )


async def _restore_rows(
    conn,
    table: str,
    rows: list[dict],
    metadata: dict[str, dict],
):
    if not rows:
        return 0

    normalized_rows = []

    current_columns = metadata

    for row_index, row in enumerate(
        rows,
        start=1,
    ):
        if not isinstance(row, dict):
            raise ValueError(
                f"Backup row {row_index} in "
                f"{table!r} is not an object."
            )

        unsupported_cols = sorted(
            set(row) - set(current_columns)
        )

        if unsupported_cols:
            raise ValueError(
                f"Backup table {table!r} "
                f"contains unknown column(s): "
                + ", ".join(unsupported_cols)
            )

        columns = list(
            row.keys()
        )

        record = tuple(
            _coerce_value(
                row.get(column),
                current_columns[column][
                    "data_type"
                ],
            )
            for column in columns
        )

        normalized_rows.append(
            (
                columns,
                record,
            )
        )

    first_columns = normalized_rows[0][0]

    if any(
        columns != first_columns
        for columns, _ in normalized_rows
    ):
        raise ValueError(
            f"Backup table {table!r} "
            f"has inconsistent row columns."
        )

    col_list = ", ".join(
        f'"{column}"'
        for column in first_columns
    )

    placeholders = ", ".join(
        f"${i + 1}"
        for i in range(
            len(first_columns)
        )
    )

    insert_sql = (
        f'INSERT INTO "{table}" '
        f'({col_list}) '
        f'VALUES ({placeholders});'
    )

    await conn.executemany(
        insert_sql,
        [
            record
            for _, record in normalized_rows
        ],
    )

    return len(normalized_rows)


async def _restore_or_merge_rows(
    conn,
    table: str,
    rows: list[dict],
    metadata: dict[str, dict],
    *,
    conflict_column: str | None = None,
) -> int:
    """Restore rows, or merge identity rows without overwriting live data."""

    if not rows:
        return 0

    normalized_rows = []

    current_columns = metadata

    for row_index, row in enumerate(
        rows,
        start=1,
    ):
        if not isinstance(row, dict):
            raise ValueError(
                f"Backup row {row_index} in "
                f"{table!r} is not an object."
            )

        unsupported_cols = sorted(
            set(row) - set(current_columns)
        )

        if unsupported_cols:
            raise ValueError(
                f"Backup table {table!r} "
                f"contains unknown column(s): "
                + ", ".join(unsupported_cols)
            )

        columns = list(
            row.keys()
        )

        if (
            conflict_column
            and conflict_column not in columns
        ):
            raise ValueError(
                f"Backup table {table!r} "
                f"is missing its merge key "
                f"column {conflict_column!r}."
            )

        record = tuple(
            _coerce_value(
                row.get(column),
                current_columns[column][
                    "data_type"
                ],
            )
            for column in columns
        )

        normalized_rows.append(
            (
                columns,
                record,
            )
        )

    first_columns = normalized_rows[0][0]

    if any(
        columns != first_columns
        for columns, _ in normalized_rows
    ):
        raise ValueError(
            f"Backup table {table!r} "
            f"has inconsistent row columns."
        )

    col_list = ", ".join(
        f'"{column}"'
        for column in first_columns
    )

    placeholders = ", ".join(
        f"${i + 1}"
        for i in range(
            len(first_columns)
        )
    )

    if conflict_column:
        insert_sql = (
            f'INSERT INTO "{table}" '
            f'({col_list}) '
            f'VALUES ({placeholders}) '
            f'ON CONFLICT ("{conflict_column}") '
            f'DO NOTHING;'
        )

    else:
        insert_sql = (
            f'INSERT INTO "{table}" '
            f'({col_list}) '
            f'VALUES ({placeholders});'
        )

    await conn.executemany(
        insert_sql,
        [
            record
            for _, record in normalized_rows
        ],
    )

    return len(normalized_rows)


async def import_tables(
    raw_bytes: bytes,
) -> dict[str, int]:
    """Restore a backup, with live users/known chats merged instead of replaced.

    Full /sync backups are authoritative for all non-identity application data:
    the current rows are removed and the backup rows are restored. The `users`
    and `broadcast_targets` registries are the exception: existing live rows are
    preserved, and only backup rows missing from the live database are added.

    Partial /cleardata backups retain their historical scope: only tables in the
    partial backup are replaced/restored, with the same identity-registry merge.
    """

    payload = _decode_backup(
        raw_bytes
    )

    if not isinstance(payload, dict):
        raise ValueError(
            "Invalid backup file: "
            "top-level JSON must be an object."
        )

    backup_type = payload.get(
        "backup_type"
    )

    if backup_type not in {
        "sync",
        "cleardata",
    }:
        raise ValueError(
            "Invalid backup file: "
            "missing or unsupported backup_type "
            "(expected 'sync' or 'cleardata')."
        )

    tables = payload.get(
        "tables"
    ) or {}

    if (
        not isinstance(tables, dict)
        or not tables
    ):
        raise ValueError(
            "This backup file has no table data in it."
        )

    pool = get_pool()
    results: dict[str, int] = {}

    async with pool.acquire() as conn:
        current_tables = await _public_base_tables(
            conn
        )

        current_set = set(
            current_tables
        )

        backup_set = set(
            tables
        )

        unknown = sorted(
            backup_set - current_set
        )

        if unknown:
            raise ValueError(
                "Backup contains table(s) "
                "that do not exist in the current "
                "database: "
                + ", ".join(unknown)
            )

        if (
            backup_type == "sync"
            and int(
                payload.get(
                    "backup_format_version"
                )
                or 1
            ) >= BACKUP_FORMAT_VERSION
        ):
            declared = set(
                payload.get(
                    "schema_tables"
                )
                or []
            )

            if declared != backup_set:
                raise ValueError(
                    "Full backup manifest "
                    "does not match its table payload."
                )

        protected = set(
            PROTECTED_MERGE_KEYS
        )

        if backup_type == "sync":
            # A full /sync backup is authoritative for every normal application
            # table, including any table that was added after the backup was made.
            # Such current-only tables therefore become empty when absent from the
            # backup, while protected identity tables remain intact.
            clear_set = (
                current_set - protected
            )

        else:
            # /cleardata backups are intentionally partial. Keep all tables that
            # the partial backup never represented, preserving the old scope.
            clear_set = (
                backup_set - protected
            )

        # Restore order must cover everything we intend to clear and everything
        # we intend to restore. Metadata is read before writes so the transaction
        # never has to discover schema midway through the replacement.
        ordered_clear = await _foreign_key_order(
            conn,
            list(clear_set),
        )

        ordered_restore = await _foreign_key_order(
            conn,
            list(
                backup_set - protected
            ),
        )

        metadata: dict[
            str,
            dict[str, dict],
        ] = {}

        for table in sorted(
            set(clear_set) | backup_set
        ):
            metadata[table] = (
                await _table_column_metadata(
                    conn,
                    table,
                )
            )

        async with conn.transaction():
            # Delete every authoritative non-protected table. We intentionally
            # keep users and known chat targets untouched so their live identity
            # records survive a restore. Individual TRUNCATE statements avoid the
            # PostgreSQL-specific multi-table RESTART IDENTITY syntax that
            # CockroachDB rejects.
            for table in reversed(
                ordered_clear
            ):
                await conn.execute(
                    f'TRUNCATE TABLE "{table}" CASCADE;'
                )

            # Merge live identity registries first. Existing current rows win;
            # backup-only users/groups are appended without duplication.
            for (
                table,
                key_column,
            ) in PROTECTED_MERGE_KEYS.items():
                if table in tables:
                    results[table] = (
                        await _restore_or_merge_rows(
                            conn,
                            table,
                            tables.get(table)
                            or [],
                            metadata[table],
                            conflict_column=key_column,
                        )
                    )

            # Restore every non-protected table from the backup exactly. A full
            # sync can intentionally leave current-only tables empty when they did
            # not exist in the older backup, because the backup is authoritative.
            for table in ordered_restore:
                results[table] = await _restore_rows(
                    conn,
                    table,
                    tables.get(table)
                    or [],
                    metadata[table],
                )

            # Reset sequence-backed IDs for every table whose live rows were
            # replaced, plus every non-protected table restored from the backup.
            sequence_tables = list(
                dict.fromkeys(
                    ordered_clear
                    + ordered_restore
                )
            )

            sequence_metadata = {
                table: metadata[table]
                for table in sequence_tables
            }

            if sequence_tables:
                await _reset_sequences(
                    conn,
                    sequence_tables,
                    sequence_metadata,
                )

    print(
        f"[backup_repo] import_tables() -> "
        f"restored/merged {results}"
    )

    return results