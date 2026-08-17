"""
Generic export/import engine for full or partial database backups, used
by three commands:
- /cleardata sends a backup of exactly what's about to be deleted,
  right before deleting it.
- /sync sends a full backup of the whole database, on demand, any time.
- /recover restores a database from either of the above backup files.

Backup file format: gzip-compressed JSON, shape:
    {
        "backup_type": "cleardata" | "sync",
        "created_at": "<ISO 8601 timestamp>",
        "tables": {
            "<table_name>": [ {<column>: <value>, ...}, ... ],
            ...
        }
    }
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, date, time, timezone
from decimal import Decimal

from database.connection import get_pool
from database.admin_repo import CLEAR_TABLES

# Tables that are cascade-deleted by /cleardata's TRUNCATE ... CASCADE
# even though they aren't directly in CLEAR_TABLES themselves, because
# they have a foreign key pointing at a table that IS in CLEAR_TABLES
# (player_card_images -> players, daily_rewards -> users). Backing these
# up too means the pre-clear backup file reflects everything that is
# genuinely about to be destroyed, not just the tables named in
# CLEAR_TABLES.
_CLEARDATA_CASCADE_EXTRAS = ["player_card_images", "daily_rewards"]

CLEARDATA_BACKUP_TABLES = [*CLEAR_TABLES, *_CLEARDATA_CASCADE_EXTRAS]

# Tables that /cleardata (and therefore CLEARDATA_BACKUP_TABLES) must
# NEVER include - these have no foreign key back to any CLEAR_TABLES
# table, so TRUNCATE ... CASCADE never reaches them, and they must
# survive a /cleardata run untouched. Level-tier card images
# (/upload_img <tier>) live here.
NEVER_CLEARED_TABLES = [
    "tier_card_images",
    "template_card_image",
    "authorized_uploaders",
    "play_matches",
    "stadium_images",
]

# Every table /sync should back up - i.e. the whole database, minus the
# internal migration bookkeeping table.
FULL_BACKUP_TABLES = [
    "users",
    "players",
    "team_squads",
    "team_lineups",
    "daily_rewards",
    "player_claims",
    "player_card_images",
    "match_challenges",
    "matches",
    "player_stats",
    "probability_profiles",
    "authorized_uploaders",
    "template_card_image",
    "play_matches",
    "stadium_images",
    "tier_card_images",
]

assert set(CLEARDATA_BACKUP_TABLES).isdisjoint(NEVER_CLEARED_TABLES), (
    "A table meant to survive /cleardata ended up in its backup/clear set - fix the lists above."
)

# Parent-before-child order, so a restore's INSERTs never hit a foreign
# key that doesn't exist yet. Any table not listed here (shouldn't
# happen for a backup made by this file) is restored last, in whatever
# order the backup JSON has it.
_RESTORE_ORDER = [
    "users",
    "players",
    "team_squads",
    "team_lineups",
    "daily_rewards",
    "player_claims",
    "player_card_images",
    "match_challenges",
    "matches",
    "player_stats",
    "probability_profiles",
    "authorized_uploaders",
    "template_card_image",
    "play_matches",
    "stadium_images",
    "tier_card_images",
]


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


async def export_tables(table_names: list[str], *, backup_type: str) -> bytes:
    """Dumps the given tables to gzip-compressed JSON bytes."""
    pool = get_pool()
    tables: dict[str, list[dict]] = {}

    async with pool.acquire() as conn:
        for table in table_names:
            rows = await conn.fetch(f"SELECT * FROM {table};")
            tables[table] = [dict(row) for row in rows]

    payload = {
        "backup_type": backup_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tables": tables,
    }
    raw = json.dumps(payload, default=_json_default, ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(raw)
    print(f"[backup_repo] export_tables({backup_type}) -> {len(table_names)} tables, "
          f"{sum(len(v) for v in tables.values())} rows, {len(compressed)} bytes gzip")
    return compressed


def _decode_backup(raw_bytes: bytes) -> dict:
    try:
        raw = gzip.decompress(raw_bytes)
    except OSError:
        # Not gzip - maybe a plain .json backup someone edited by hand.
        raw = raw_bytes
    return json.loads(raw.decode("utf-8"))


def _parse_datetime(value, *, with_timezone: bool):
    """Convert ISO-8601 backup text to a datetime compatible with PostgreSQL."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date) and not isinstance(value, datetime):
        parsed = datetime.combine(value, time.min)
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)

    if with_timezone:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    elif parsed.tzinfo is not None:
        # PostgreSQL "timestamp without time zone" cannot accept an aware
        # datetime. The backup timestamps were created in UTC, so keep the
        # wall-clock UTC value and remove the tzinfo.
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)

    return parsed


def _coerce_value(value, pg_type: str):
    """Turn JSON-decoded backup values into asyncpg/PostgreSQL-native values."""
    if value is None:
        return None

    if pg_type in {"timestamp without time zone", "timestamp with time zone"}:
        return _parse_datetime(
            value,
            with_timezone=(pg_type == "timestamp with time zone"),
        )

    if pg_type == "date":
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        return date.fromisoformat(str(value).split("T", 1)[0])

    if pg_type.startswith("time"):
        if isinstance(value, time):
            return value
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return time.fromisoformat(text)

    if pg_type in {"json", "jsonb"}:
        # asyncpg expects JSON/JSONB parameters as JSON text unless a custom
        # codec has been installed on the pool.
        return value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, separators=(",", ":")
        )

    if pg_type in {
        "numeric", "decimal",
    }:
        return value if isinstance(value, Decimal) else Decimal(str(value))

    # The remaining current schema types (BIGINT, INTEGER, TEXT, BOOLEAN,
    # etc.) are represented natively by JSON decoding and asyncpg.
    return value


async def _table_column_metadata(conn, table: str) -> dict[str, dict]:
    """Read PostgreSQL column types/defaults for safe value conversion."""
    rows = await conn.fetch(
        """
        SELECT
            column_name,
            data_type,
            udt_name,
            column_default,
            is_identity
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        ORDER BY ordinal_position
        """,
        table,
    )
    if not rows:
        raise ValueError(f"Backup contains unknown table: {table!r}")
    return {row["column_name"]: dict(row) for row in rows}


async def _reset_sequences(conn, tables: list[str], metadata: dict[str, dict[str, dict]]):
    """Advance serial/identity sequences so future INSERTs do not collide."""
    for table in tables:
        columns = metadata.get(table, {})
        for column, info in columns.items():
            default = info.get("column_default") or ""
            is_identity = info.get("is_identity") == "YES"
            if "nextval(" not in default and not is_identity:
                continue

            sequence_name = await conn.fetchval(
                "SELECT pg_get_serial_sequence($1, $2)",
                f"public.{table}",
                column,
            )
            if not sequence_name:
                continue

            # These identifiers originate from information_schema and the
            # table list is validated against the known backup table set.
            max_value = await conn.fetchval(
                f'SELECT MAX("{column}") FROM "{table}";'
            )
            if max_value is None:
                await conn.execute("SELECT setval($1, 1, false);", sequence_name)
            else:
                await conn.execute(
                    "SELECT setval($1, $2, true);",
                    sequence_name,
                    max_value,
                )


async def import_tables(raw_bytes: bytes) -> dict:
    """Restore a backup produced by export_tables().

    The restore is transactional: if any table or row fails, PostgreSQL rolls
    the whole restore back. Date/time values serialized as ISO strings are
    converted back to PostgreSQL-native values, JSON/JSONB fields are encoded
    correctly for asyncpg, and serial sequences are advanced after explicit
    primary-key restoration.
    """
    payload = _decode_backup(raw_bytes)
    if not isinstance(payload, dict):
        raise ValueError("Invalid backup file: top-level JSON must be an object.")

    backup_type = payload.get("backup_type")
    if backup_type not in {"sync", "cleardata"}:
        raise ValueError(
            "Invalid backup file: missing or unsupported backup_type "
            "(expected 'sync' or 'cleardata')."
        )

    tables = payload.get("tables") or {}
    if not isinstance(tables, dict) or not tables:
        raise ValueError("This backup file has no table data in it.")

    known_tables = set(FULL_BACKUP_TABLES) | set(CLEARDATA_BACKUP_TABLES)
    unknown_tables = sorted(set(tables) - known_tables)
    if unknown_tables:
        raise ValueError(
            "Backup contains unsupported table(s): " + ", ".join(unknown_tables)
        )

    present = [t for t in _RESTORE_ORDER if t in tables]
    present += [t for t in tables if t not in present]

    # /sync is intended to represent the complete application database.
    # Reject an incomplete sync instead of silently replacing only a subset.
    if backup_type == "sync":
        missing = [t for t in FULL_BACKUP_TABLES if t not in tables]
        if missing:
            raise ValueError(
                "This full backup is incomplete. Missing table(s): "
                + ", ".join(missing)
            )

    pool = get_pool()
    results: dict[str, int] = {}

    async with pool.acquire() as conn:
        async with conn.transaction():
            metadata = {
                table: await _table_column_metadata(conn, table)
                for table in present
            }

            # The application's backup table sets already include the child
            # tables that /cleardata cascades into. For a full /sync all
            # application tables are present, so CASCADE cannot discard data
            # outside the backup.
            truncate_list = ", ".join(f'"{table}"' for table in present)
            await conn.execute(
                f"TRUNCATE TABLE {truncate_list} RESTART IDENTITY CASCADE;"
            )

            for table in present:
                rows = tables.get(table) or []
                if not rows:
                    results[table] = 0
                    continue
                if not isinstance(rows, list):
                    raise ValueError(
                        f"Backup table {table!r} must contain an array of rows."
                    )

                current_columns = metadata[table]
                normalized_rows = []

                # Keep only columns that still exist in the current schema.
                # This lets an older backup survive additive migrations.
                for row_index, row in enumerate(rows, start=1):
                    if not isinstance(row, dict):
                        raise ValueError(
                            f"Backup row {row_index} in {table!r} is not an object."
                        )

                    unsupported_cols = sorted(set(row) - set(current_columns))
                    if unsupported_cols:
                        raise ValueError(
                            f"Backup table {table!r} contains unknown column(s): "
                            + ", ".join(unsupported_cols)
                        )

                    columns = list(row.keys())
                    record = tuple(
                        _coerce_value(
                            row.get(column),
                            current_columns[column]["data_type"],
                        )
                        for column in columns
                    )
                    normalized_rows.append((columns, record))

                # One statement per table, using that table's actual backup
                # column set. All rows from a given table should have the same
                # columns because export_tables() comes directly from SELECT *.
                first_columns = normalized_rows[0][0]
                if any(columns != first_columns for columns, _ in normalized_rows):
                    raise ValueError(
                        f"Backup table {table!r} has inconsistent row columns."
                    )

                col_list = ", ".join(f'"{column}"' for column in first_columns)
                placeholders = ", ".join(
                    f"${i + 1}" for i in range(len(first_columns))
                )
                insert_sql = (
                    f'INSERT INTO "{table}" ({col_list}) '
                    f"VALUES ({placeholders});"
                )
                records = [record for _, record in normalized_rows]
                await conn.executemany(insert_sql, records)
                results[table] = len(records)

            await _reset_sequences(conn, present, metadata)

    print(f"[backup_repo] import_tables() -> restored {results}")
    return results
