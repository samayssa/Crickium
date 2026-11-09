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
import io
import json
import tempfile
import zlib
import shutil
import uuid
import re
from pathlib import Path
from collections import defaultdict, deque
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Iterable

from database.connection import get_pool
from database.admin_repo import CLEAR_TABLES

BACKUP_FORMAT_VERSION = 2
SCHEMA_BOOKKEEPING_TABLES = {"schema_version"}

_CLEARDATA_CASCADE_EXTRAS = [
    "player_card_images",
    "special_player_card_images",
    "daily_rewards",
]

CLEARDATA_BACKUP_TABLES = [*CLEAR_TABLES, *_CLEARDATA_CASCADE_EXTRAS]

NEVER_CLEARED_TABLES = [
    "tier_card_images",
    "template_card_image",
    "authorized_uploaders",
    "play_matches",
    "stadium_images",
]

PROTECTED_MERGE_KEYS = {
    "users": "user_id",
    "broadcast_targets": "chat_id",
}

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
    "showcase_sets",
    "showcase_cards",
    "showcase_player_card_images",
    "authorized_uploaders",
    "player_card_images",
    "template_card_image",
    "play_matches",
    "playso_matches",
    "playipl_matches",
    "wpl_matches",
    "stadium_images",
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
    "tour_tournaments",
    "tour_teams",
    "tour_members",
    "tour_auction",
    "tour_lots",
    "tour_bids",
    "tour_squad",
    "tour_fixtures",
    "tour_days",
    "tour_player_match",
    "giveaways",
    "giveaway_entries",
    "giveaway_deliveries",
]

assert set(CLEARDATA_BACKUP_TABLES).isdisjoint(NEVER_CLEARED_TABLES), (
    "A table meant to survive /cleardata ended up in its backup/clear set."
)

_RESTORE_ORDER = FULL_BACKUP_TABLES[:]


def _json_default(value):
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
    return value


MAX_BACKUP_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_BACKUP_DECOMPRESSED_BYTES = 512 * 1024 * 1024


def _decode_backup_file(file_path: str | Path) -> dict:
    """Decode a backup from disk with bounded decompression and no RAM download copy."""
    source_path = Path(file_path)
    compressed_size = source_path.stat().st_size
    if compressed_size > MAX_BACKUP_COMPRESSED_BYTES:
        raise ValueError("Backup file is larger than the 64 MB restore limit.")
    if compressed_size <= 0:
        raise ValueError("Backup file is empty.")

    with source_path.open("rb") as probe:
        is_gzip = probe.read(2) == b"\x1f\x8b"

    # Stage decompressed JSON on the container's temporary filesystem. This
    # avoids keeping compressed bytes, decompressed bytes, and a decoded string
    # in RAM at the same time during a restore on a small Railway instance.
    with tempfile.TemporaryFile(mode="w+b") as decoded_file:
        opener = gzip.open if is_gzip else open
        try:
            with opener(source_path, "rb") as source:
                decoded_size = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    decoded_size += len(chunk)
                    if decoded_size > MAX_BACKUP_DECOMPRESSED_BYTES:
                        raise ValueError("Backup file expands beyond the allowed 512 MB restore limit.")
                    decoded_file.write(chunk)
        except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
            raise ValueError(f"Backup gzip data is damaged or incomplete: {exc}") from exc

        decoded_file.seek(0)
        with io.TextIOWrapper(decoded_file, encoding="utf-8") as text_file:
            try:
                return json.load(text_file, object_hook=_json_object_hook)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Backup is not valid UTF-8 JSON: {exc}") from exc


def _decode_backup(raw_bytes: bytes | str | Path) -> dict:
    if isinstance(raw_bytes, (str, Path)):
        return _decode_backup_file(raw_bytes)

    if len(raw_bytes) > MAX_BACKUP_COMPRESSED_BYTES:
        raise ValueError("Backup file is too large to restore safely.")
    try:
        # Bounded decompression: a tiny gzip "bomb" must not exhaust memory.
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = decompressor.decompress(raw_bytes, MAX_BACKUP_DECOMPRESSED_BYTES + 1)
        if len(raw) > MAX_BACKUP_DECOMPRESSED_BYTES or decompressor.unconsumed_tail:
            raise ValueError("Backup file expands beyond the allowed size limit.")
    except zlib.error:
        raw = raw_bytes

    return json.loads(
        raw.decode("utf-8"),
        object_hook=_json_object_hook,
    )


class _LimitedRawReader(io.RawIOBase):
    """Count uncompressed bytes while a gzip/plain backup is streamed."""

    def __init__(self, source, limit: int):
        super().__init__()
        self._source = source
        self._limit = int(limit)
        self.total_bytes = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        chunk = self._source.read(len(buffer))
        if not chunk:
            return 0
        self.total_bytes += len(chunk)
        if self.total_bytes > self._limit:
            raise ValueError(
                "Backup expands beyond the allowed 512 MB restore limit."
            )
        buffer[:len(chunk)] = chunk
        return len(chunk)

    def close(self):
        if not self.closed:
            try:
                self._source.close()
            finally:
                super().close()


class _JSONStreamReader:
    """Small-buffer JSON reader based on JSONDecoder.raw_decode()."""

    def __init__(self, stream, chunk_size: int = 64 * 1024):
        self.stream = stream
        self.chunk_size = int(chunk_size)
        self.buffer = ""
        self.position = 0
        self.eof = False
        self.decoder = json.JSONDecoder()

    def _fill(self) -> None:
        if self.position:
            self.buffer = self.buffer[self.position:]
            self.position = 0
        chunk = self.stream.read(self.chunk_size)
        if chunk:
            self.buffer += chunk
        else:
            self.eof = True

    def _skip_whitespace(self) -> None:
        while True:
            while (
                self.position < len(self.buffer)
                and self.buffer[self.position].isspace()
            ):
                self.position += 1
            if self.position < len(self.buffer) or self.eof:
                return
            self._fill()

    def peek(self) -> str:
        self._skip_whitespace()
        if self.position >= len(self.buffer):
            return ""
        return self.buffer[self.position]

    def consume(self, expected: str) -> None:
        actual = self.peek()
        if actual != expected:
            raise ValueError(
                f"Invalid backup JSON: expected {expected!r}, found {actual!r}."
            )
        self.position += 1

    def read_value(self):
        self._skip_whitespace()
        while True:
            try:
                value, end = self.decoder.raw_decode(
                    self.buffer,
                    self.position,
                )
                self.position = end
                return value
            except json.JSONDecodeError as exc:
                # A token may be incomplete even when the decoder reports an
                # error at its opening quote/container. Refill until the token
                # completes or the underlying file reaches EOF.
                if self.eof:
                    raise ValueError(
                        f"Invalid backup JSON near character {self.position}: {exc.msg}."
                    ) from exc
                self._fill()


def _restore_resource_snapshot(label: str, disk_path: str | Path) -> None:
    """Log process RSS/peak RSS and free temporary disk space on Railway."""
    rss = "unknown"
    peak = "unknown"
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    rss = line.split(":", 1)[1].strip()
                elif line.startswith("VmHWM:"):
                    peak = line.split(":", 1)[1].strip()
    except OSError:
        pass

    try:
        usage = shutil.disk_usage(str(disk_path))
        disk_text = (
            f"free={usage.free / (1024 * 1024):.1f} MiB, "
            f"total={usage.total / (1024 * 1024):.1f} MiB"
        )
    except OSError:
        disk_text = "unavailable"

    print(
        f"[backup_repo] resource {label}: "
        f"rss={rss}, peak_rss={peak}, temp_disk({disk_path})={disk_text}"
    )


def _update_streamed_table_stats(stats: dict, row: dict) -> None:
    columns = list(row.keys())
    if stats["first_columns"] is None:
        stats["first_columns"] = columns
        stats["columns"] = columns[:]
    elif columns != stats["first_columns"]:
        stats["consistent_columns"] = False

    for column, value in row.items():
        column = str(column)
        if value is None:
            stats["column_types"][column].add("NULL")
        else:
            stats["column_types"][column].add(
                _infer_pg_type_from_backup_value(value)
            )
    stats["row_count"] += 1


def _stream_backup_to_table_files(
    source_path: str | Path,
    spool_dir: str | Path,
) -> tuple[dict, dict[str, dict], int]:
    """Parse backup JSON incrementally and spool one JSON row per line/table."""
    source_path = Path(source_path)
    spool_dir = Path(spool_dir)
    compressed_size = source_path.stat().st_size
    if compressed_size <= 0:
        raise ValueError("Backup file is empty.")
    if compressed_size > MAX_BACKUP_COMPRESSED_BYTES:
        raise ValueError("Backup file is larger than the 64 MB restore limit.")

    with source_path.open("rb") as probe:
        is_gzip = probe.read(2) == b"\x1f\x8b"

    opener = gzip.open if is_gzip else open
    metadata: dict = {}
    table_stats: dict[str, dict] = {}
    table_index = 0

    try:
        with opener(source_path, "rb") as binary_source:
            limited = _LimitedRawReader(
                binary_source,
                MAX_BACKUP_DECOMPRESSED_BYTES,
            )
            with io.BufferedReader(limited) as buffered:
                with io.TextIOWrapper(
                    buffered,
                    encoding="utf-8",
                ) as text_stream:
                    reader = _JSONStreamReader(text_stream)
                    reader.consume("{")

                    while reader.peek() != "}":
                        key = reader.read_value()
                        if not isinstance(key, str):
                            raise ValueError(
                                "Invalid backup JSON: root object key is not a string."
                            )
                        reader.consume(":")

                        if key != "tables":
                            metadata[key] = reader.read_value()
                        else:
                            reader.consume("{")
                            while reader.peek() != "}":
                                table_name = reader.read_value()
                                if not isinstance(table_name, str):
                                    raise ValueError(
                                        "Invalid backup JSON: table name is not a string."
                                    )
                                if table_name in table_stats:
                                    raise ValueError(
                                        f"Backup contains duplicate table key {table_name!r}."
                                    )
                                reader.consume(":")
                                reader.consume("[")

                                row_file = spool_dir / f"table_{table_index:04d}.jsonl.gz"
                                table_index += 1
                                stats = {
                                    "row_count": 0,
                                    "columns": [],
                                    "first_columns": None,
                                    "consistent_columns": True,
                                    "column_types": defaultdict(set),
                                    "row_file": row_file,
                                }
                                table_stats[table_name] = stats

                                with gzip.open(
                                    row_file,
                                    "wt",
                                    encoding="utf-8",
                                    compresslevel=1,
                                ) as output:
                                    while reader.peek() != "]":
                                        row = reader.read_value()
                                        if not isinstance(row, dict):
                                            raise ValueError(
                                                f"Backup row {stats['row_count'] + 1} "
                                                f"in {table_name!r} is not an object."
                                            )
                                        _update_streamed_table_stats(stats, row)
                                        output.write(
                                            json.dumps(
                                                row,
                                                ensure_ascii=False,
                                                separators=(",", ":"),
                                            )
                                        )
                                        output.write("\n")

                                        delimiter = reader.peek()
                                        if delimiter == ",":
                                            reader.consume(",")
                                        elif delimiter != "]":
                                            raise ValueError(
                                                f"Invalid backup JSON after a row in {table_name!r}."
                                            )

                                reader.consume("]")
                                delimiter = reader.peek()
                                if delimiter == ",":
                                    reader.consume(",")
                                elif delimiter != "}":
                                    raise ValueError(
                                        "Invalid backup JSON in tables object."
                                    )

                            reader.consume("}")

                        delimiter = reader.peek()
                        if delimiter == ",":
                            reader.consume(",")
                        elif delimiter != "}":
                            raise ValueError(
                                "Invalid backup JSON after root value."
                            )

                    reader.consume("}")
                    if reader.peek():
                        raise ValueError(
                            "Invalid backup JSON: unexpected content after the root object."
                        )
                    decompressed_size = limited.total_bytes
    except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
        raise ValueError(
            f"Backup gzip data is damaged or incomplete: {exc}"
        ) from exc
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Backup is not valid UTF-8 JSON: {exc}"
        ) from exc

    if "tables" not in metadata and not table_stats:
        raise ValueError("This backup file has no table data.")

    return metadata, table_stats, decompressed_size


async def _ensure_streamed_backup_only_tables(
    conn,
    table_stats: dict[str, dict],
    current_tables: set[str],
) -> list[str]:
    """Recreate non-empty backup-only tables using streamed column samples."""
    created: list[str] = []
    for table, stats in table_stats.items():
        if table in current_tables:
            continue
        if not stats["row_count"]:
            print(
                f"[backup_repo] Backup-only empty table {table!r} cannot be "
                "reconstructed without schema metadata; skipping."
            )
            continue

        definitions = []
        for column in stats["columns"]:
            pg_type = _merge_inferred_pg_types(
                stats["column_types"].get(column, set())
            )
            definitions.append(
                f"{_json_identifier(column)} {pg_type}"
            )

        if not definitions:
            continue

        await conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_json_identifier(table)} ("
            + ", ".join(definitions)
            + ");"
        )
        current_tables.add(table)
        created.append(table)
        print(
            f"[backup_repo] Created backup-only table {table!r} "
            f"with {len(definitions)} inferred column(s)."
        )
    return created


async def _ensure_streamed_backup_columns(
    conn,
    table_stats: dict[str, dict],
    current_tables: set[str],
) -> None:
    """Add columns found in a backup without loading complete table arrays."""
    for table, stats in table_stats.items():
        if table not in current_tables or not stats["row_count"]:
            continue

        column_rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1;
            """,
            table,
        )
        existing_columns = {
            str(row["column_name"])
            for row in column_rows
        }

        for column in stats["columns"]:
            if column in existing_columns:
                continue
            pg_type = _merge_inferred_pg_types(
                stats["column_types"].get(column, set())
            )
            await conn.execute(
                f"ALTER TABLE {_json_identifier(table)} "
                f"ADD COLUMN IF NOT EXISTS {_json_identifier(column)} {pg_type};"
            )
            existing_columns.add(column)
            print(
                f"[backup_repo] Added missing backup column "
                f"{table}.{column} as {pg_type}"
            )


async def _restore_streamed_rows(
    conn,
    table: str,
    row_file: str | Path,
    metadata: dict[str, dict],
    *,
    conflict_column: str | None = None,
    batch_size: int = 200,
) -> int:
    """Restore one table from JSONL using bounded batches of at most 200 rows."""
    row_file = Path(row_file)
    if not row_file.exists() or row_file.stat().st_size == 0:
        return 0

    first_columns = None
    insert_sql = None
    batch = []
    row_count = 0

    with gzip.open(row_file, "rt", encoding="utf-8") as source:
        for row_index, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid spooled row {row_index} for table {table!r}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"Backup row {row_index} in {table!r} is not an object."
                )

            unsupported_cols = sorted(set(row) - set(metadata))
            if unsupported_cols:
                raise ValueError(
                    f"Backup table {table!r} contains unknown column(s): "
                    + ", ".join(unsupported_cols)
                )

            columns = list(row.keys())
            if first_columns is None:
                first_columns = columns
                if conflict_column and conflict_column not in columns:
                    raise ValueError(
                        f"Backup table {table!r} is missing its merge key "
                        f"column {conflict_column!r}."
                    )
                col_list = ", ".join(
                    f'"{column}"' for column in first_columns
                )
                placeholders = ", ".join(
                    _typed_placeholder(
                        i + 1,
                        metadata[column]["data_type"],
                    )
                    for i, column in enumerate(first_columns)
                )
                insert_sql = (
                    f'INSERT INTO "{table}" ({col_list}) '
                    f'VALUES ({placeholders})'
                )
                if conflict_column:
                    insert_sql += (
                        f' ON CONFLICT ("{conflict_column}") DO NOTHING'
                    )
                insert_sql += ";"
            elif columns != first_columns:
                raise ValueError(
                    f"Backup table {table!r} has inconsistent row columns."
                )

            record = tuple(
                _coerce_value(
                    row.get(column),
                    metadata[column]["data_type"],
                )
                for column in first_columns
            )
            batch.append(record)
            row_count += 1

            if len(batch) >= batch_size:
                await conn.executemany(insert_sql, batch)
                batch.clear()

    if batch:
        await conn.executemany(insert_sql, batch)

    return row_count


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
    pool = get_pool()

    async with pool.acquire() as conn:
        return await _public_base_tables(conn)


async def _encode_gzip_json(payload: dict) -> bytes:
    """Same bytes-on-the-wire format as ``gzip.compress(json.dumps(payload))`` but produced cooperatively."""
    import asyncio
    import zlib

    co = zlib.compressobj(6, zlib.DEFLATED, 31)          # 31 = gzip container
    parts: list[bytes] = []

    def feed(text: str) -> None:
        parts.append(co.compress(text.encode("utf-8")))

    def dump(obj) -> str:
        return json.dumps(obj, default=_json_default, ensure_ascii=False, separators=(",", ":"))

    head = dump({k: v for k, v in payload.items() if k != "tables"})
    feed(('{"tables":{' if head == "{}" else head[:-1] + ',"tables":{'))
    first = True
    for name, rows in payload["tables"].items():
        feed(("" if first else ",") + dump(name) + ":[")
        first = False
        for i in range(0, len(rows), 100):
            feed(("," if i else "") + ",".join(dump(r) for r in rows[i:i + 100]))
            await asyncio.sleep(0)
        feed("]")
    feed("}}")
    parts.append(co.flush())
    return b"".join(parts)



async def export_tables(
    table_names: Iterable[str] | None = None,
    *,
    backup_type: str,
) -> bytes:
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
                dict.fromkeys(
                    str(t)
                    for t in table_names
                )
            )

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

            tables[table] = [
                dict(row)
                for row in rows
            ]

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

    # Serialised in small slices with an ``await asyncio.sleep(0)`` between them (and zlib in a streaming object):
    # one big json.dumps + gzip of the whole database used to hold the event loop for seconds - on a small host the
    # entire bot (every /bid, /claim, ...) froze each time the 30-minute auto-backup ran.
    compressed = await _encode_gzip_json(payload)

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

    return value


def _infer_pg_type_from_backup_value(
    value,
) -> str:
    """
    Infer a conservative PostgreSQL/CockroachDB type for
    a backup-only value.
    """
    if value is None:
        return "TEXT"

    if isinstance(value, dict):
        marker = value.get(
            "__backup_type__"
        )

        if marker == "uuid":
            return "UUID"

        if marker == "bytes":
            return "BYTEA"

        if marker == "decimal":
            return "DECIMAL"

        if marker == "date":
            return "DATE"

        if marker == "time":
            return "TIME"

        if marker == "datetime":
            return "TIMESTAMP"

        return "JSONB"

    if isinstance(value, bool):
        return "BOOLEAN"

    if isinstance(value, int):
        return "BIGINT"

    if isinstance(value, float):
        return "DOUBLE PRECISION"

    if isinstance(
        value,
        (bytes, bytearray, memoryview),
    ):
        return "BYTEA"

    if isinstance(value, datetime):
        return "TIMESTAMP"

    if isinstance(value, date):
        return "DATE"

    if isinstance(value, time):
        return "TIME"

    if isinstance(value, Decimal):
        return "DECIMAL"

    if isinstance(value, (list, tuple)):
        return "JSONB"

    return "TEXT"


def _merge_inferred_pg_types(
    types: set[str],
) -> str:
    normalized = {
        str(t).upper()
        for t in types
    }

    if not normalized:
        return "TEXT"

    if normalized == {"NULL"}:
        return "TEXT"

    if "JSONB" in normalized:
        return "JSONB"

    if "TEXT" in normalized:
        return "TEXT"

    if "BYTEA" in normalized:
        return "BYTEA"

    if "UUID" in normalized:
        return "UUID"

    if "DOUBLE PRECISION" in normalized:
        return "DOUBLE PRECISION"

    if "DECIMAL" in normalized:
        return "DECIMAL"

    if "BIGINT" in normalized:
        return "BIGINT"

    if "BOOLEAN" in normalized:
        return "BOOLEAN"

    if "TIMESTAMP" in normalized:
        return "TIMESTAMP"

    if "DATE" in normalized:
        return "DATE"

    if "TIME" in normalized:
        return "TIME"

    return "TEXT"


def _json_identifier(
    value: str,
) -> str:
    return (
        '"'
        + str(value).replace(
            '"',
            '""',
        )
        + '"'
    )


async def _ensure_backup_only_tables(
    conn,
    tables: dict[str, list[dict]],
    current_tables: set[str],
) -> list[str]:
    """
    Create tables that exist in a backup but not in the live schema.

    The sync backup format stores row data, not original CREATE TABLE DDL.
    Therefore a missing table cannot be reconstructed with its exact historical
    indexes, foreign keys, primary keys, or defaults.

    For backup-only tables that contain rows, however, the backup still gives
    us every column name and a usable value sample. Reconstruct a conservative
    PostgreSQL/CockroachDB table from those values so the backup data itself is
    not discarded and the whole recovery can complete.

    Existing tables are never altered here.
    """
    created: list[str] = []

    for table, rows in tables.items():
        if table in current_tables:
            continue

        if not isinstance(rows, list):
            raise ValueError(
                f"Backup table {table!r} "
                "must contain a list of rows."
            )

        if not rows:
            print(
                f"[backup_repo] Backup-only empty table "
                f"{table!r} cannot be reconstructed "
                "without schema metadata; skipping."
            )
            continue

        column_names: list[str] = []
        column_types: dict[
            str,
            set[str],
        ] = defaultdict(set)

        for row_index, row in enumerate(
            rows,
            start=1,
        ):
            if not isinstance(row, dict):
                raise ValueError(
                    f"Backup row {row_index} in "
                    f"{table!r} is not an object."
                )

            for column in row.keys():
                column = str(column)

                if column not in column_types:
                    column_names.append(
                        column
                    )

                value = row.get(
                    column
                )

                if value is None:
                    column_types[column].add(
                        "NULL"
                    )

                else:
                    column_types[column].add(
                        _infer_pg_type_from_backup_value(
                            value
                        )
                    )

        definitions = []

        for column in column_names:
            pg_type = _merge_inferred_pg_types(
                column_types.get(
                    column,
                    set(),
                )
            )

            definitions.append(
                f"{_json_identifier(column)} "
                f"{pg_type}"
            )

        if not definitions:
            print(
                f"[backup_repo] Backup-only table "
                f"{table!r} has no columns; skipping."
            )
            continue

        create_sql = (
            f"CREATE TABLE IF NOT EXISTS "
            f"{_json_identifier(table)} ("
            + ", ".join(definitions)
            + ");"
        )

        await conn.execute(
            create_sql
        )

        current_tables.add(
            table
        )

        created.append(
            table
        )

        print(
            f"[backup_repo] Created backup-only table "
            f"{table!r} with "
            f"{len(definitions)} inferred column(s)."
        )

    return created



def _infer_backup_column_type(
    values,
) -> str:
    """
    Infer a safe SQL type for a backup column that does not exist in the
    currently running database.

    The backup format stores row values but not the original CREATE TABLE DDL.
    We therefore prefer a conservative nullable type. Integer identifiers are
    deliberately BIGINT because Telegram user/chat IDs and database foreign
    keys can exceed INT32.
    """
    observed = [
        value
        for value in values
        if value is not None
    ]

    if not observed:
        return "TEXT"

    kinds = set()

    for value in observed:
        if isinstance(value, dict):
            marker = value.get("__backup_type__")

            if marker == "uuid":
                kinds.add("UUID")
            elif marker == "bytes":
                kinds.add("BYTEA")
            elif marker == "decimal":
                kinds.add("DECIMAL")
            elif marker == "date":
                kinds.add("DATE")
            elif marker == "time":
                kinds.add("TIME")
            elif marker == "datetime":
                kinds.add("TIMESTAMP")
            else:
                kinds.add("JSONB")
            continue

        if isinstance(value, bool):
            kinds.add("BOOLEAN")
            continue

        if isinstance(value, int):
            kinds.add("BIGINT")
            continue

        if isinstance(value, float):
            kinds.add("DOUBLE PRECISION")
            continue

        if isinstance(value, (list, tuple)):
            kinds.add("JSONB")
            continue

        if isinstance(value, (bytes, bytearray, memoryview)):
            kinds.add("BYTEA")
            continue

        kinds.add("TEXT")

    if len(kinds) == 1:
        return next(iter(kinds))

    # If the same column contains mixed JSON-like values, preserve them as JSONB.
    if "JSONB" in kinds:
        return "JSONB"

    # Mixed numeric values can safely live in DECIMAL.
    if kinds.issubset({"BIGINT", "DOUBLE PRECISION", "DECIMAL"}):
        return "DECIMAL"

    # Mixed integer/text data is safest as TEXT rather than risking a failed
    # schema migration. The backup can still be restored without data loss.
    if "TEXT" in kinds:
        return "TEXT"

    return "TEXT"


def _backup_row_values(
    rows: list[dict],
    column: str,
):
    return [
        row.get(column)
        for row in rows
        if isinstance(row, dict)
        and column in row
    ]


async def _ensure_backup_columns(
    conn,
    tables: dict[str, list[dict]],
    current_tables: set[str],
):
    """
    Reconcile columns present in the backup but absent from an existing live
    table.

    This is intentionally additive:
      * Existing columns are never modified or deleted.
      * Missing columns are added as nullable.
      * Existing live user/group values remain untouched.
      * Backup-only tables are handled separately by
        `_ensure_backup_only_tables`.

    This lets `/recover` accept backups produced by a newer schema revision,
    such as a backup containing `users.captain_player_id` when the current
    database predates that column.
    """
    for table, rows in tables.items():
        if table not in current_tables:
            continue

        if not isinstance(rows, list) or not rows:
            continue

        column_rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = $1;
            """,
            table,
        )

        existing_columns = {
            str(row["column_name"])
            for row in column_rows
        }

        backup_columns = []
        seen = set()

        for row in rows:
            if not isinstance(row, dict):
                continue

            for column in row:
                column = str(column)

                if column not in seen:
                    seen.add(column)
                    backup_columns.append(column)

        for column in backup_columns:
            if column in existing_columns:
                continue

            pg_type = _infer_backup_column_type(
                _backup_row_values(
                    rows,
                    column,
                )
            )

            await conn.execute(
                f'ALTER TABLE {_json_identifier(table)} '
                f'ADD COLUMN IF NOT EXISTS '
                f'{_json_identifier(column)} '
                f'{pg_type};'
            )

            existing_columns.add(column)

            print(
                f"[backup_repo] Added missing backup column "
                f"{table}.{column} as {pg_type}"
            )


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

    table_set = set(
        tables
    )

    parents: dict[
        str,
        set[str],
    ] = {
        table: set()
        for table in tables
    }

    children: dict[
        str,
        set[str],
    ] = {
        table: set()
        for table in tables
    }

    indegree: dict[
        str,
        int,
    ] = {
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

        parents[child].add(
            parent
        )

        children[parent].add(
            child
        )

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
                preferred.get(
                    x,
                    10_000,
                ),
                x,
            ),
        )
    )

    ordered: list[str] = []

    while queue:
        node = queue.popleft()

        ordered.append(
            node
        )

        for child in sorted(
            children[node],
            key=lambda x: (
                preferred.get(
                    x,
                    10_000,
                ),
                x,
            ),
        ):
            indegree[child] -= 1

            if indegree[child] == 0:
                queue.append(
                    child
                )

    if len(ordered) != len(tables):
        remaining = sorted(
            set(tables) - set(ordered),
            key=lambda x: (
                preferred.get(
                    x,
                    10_000,
                ),
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
    metadata: dict[
        str,
        dict[str, dict],
    ],
):
    """Reset SERIAL/identity-backed sequences after a logical restore."""
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
                info.get(
                    "is_identity"
                )
                == "YES"
            )

            if (
                "nextval(" not in default
                and not is_identity
            ):
                continue

            max_value = await conn.fetchval(
                f'SELECT MAX("{column}") '
                f'FROM "{table}";'
            )

            next_value = (
                1
                if max_value is None
                else int(max_value) + 1
            )

            if is_cockroach:
                match = re.search(
                    r"nextval\\('([^']+)'",
                    default,
                )

                if not match:
                    continue

                raw_sequence = match.group(
                    1
                )

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

                def _ident(
                    value: str,
                ) -> str:
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
                    f'RESTART WITH '
                    f'{next_value};'
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


def _typed_placeholder(
    index: int,
    pg_type: str,
) -> str:
    """
    Explicitly preserve PostgreSQL integer widths.

    This is particularly important for Telegram user/chat IDs because they
    regularly exceed INT32.
    """
    normalized = str(
        pg_type or ""
    ).strip().lower()

    if normalized in {
        "bigint",
        "int8",
    }:
        return f"${index}::BIGINT"

    if normalized in {
        "integer",
        "int",
        "int4",
    }:
        return f"${index}::INTEGER"

    if normalized in {
        "smallint",
        "int2",
    }:
        return f"${index}::SMALLINT"

    return f"${index}"


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
                + ", ".join(
                    unsupported_cols
                )
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
            "has inconsistent row columns."
        )

    col_list = ", ".join(
        f'"{column}"'
        for column in first_columns
    )

    placeholders = ", ".join(
        _typed_placeholder(
            i + 1,
            current_columns[column][
                "data_type"
            ],
        )
        for i, column in enumerate(
            first_columns
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

    return len(
        normalized_rows
    )


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
                + ", ".join(
                    unsupported_cols
                )
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
            "has inconsistent row columns."
        )

    col_list = ", ".join(
        f'"{column}"'
        for column in first_columns
    )

    placeholders = ", ".join(
        _typed_placeholder(
            i + 1,
            current_columns[column][
                "data_type"
            ],
        )
        for i, column in enumerate(
            first_columns
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

    return len(
        normalized_rows
    )



async def _import_tables_from_streamed_file(file_path: str | Path) -> dict[str, int]:
    """Restore a disk backup without ever materializing the whole JSON document."""
    source_path = Path(file_path)
    with tempfile.TemporaryDirectory(prefix="crickium-restore-rows-") as temp_dir:
        _restore_resource_snapshot("before_stream_parse", temp_dir)
        metadata, table_stats, decompressed_size = _stream_backup_to_table_files(
            source_path,
            temp_dir,
        )
        _restore_resource_snapshot("after_stream_parse", temp_dir)

        backup_type = metadata.get("backup_type")
        if backup_type not in {"sync", "cleardata"}:
            raise ValueError(
                "Invalid backup file: missing or unsupported backup_type "
                "(expected 'sync' or 'cleardata')."
            )

        if not table_stats:
            raise ValueError("This backup file has no table data in it.")

        original_backup_set = set(table_stats)
        if (
            backup_type == "sync"
            and int(metadata.get("backup_format_version") or 1) >= BACKUP_FORMAT_VERSION
        ):
            declared = set(metadata.get("schema_tables") or [])
            if declared != original_backup_set:
                raise ValueError(
                    "Full backup manifest does not match its table payload."
                )

        total_rows = sum(stats["row_count"] for stats in table_stats.values())
        print(
            f"[backup_repo] Streaming parse complete: {len(table_stats)} tables, "
            f"{total_rows} rows, {decompressed_size} uncompressed bytes."
        )

        pool = get_pool()
        results: dict[str, int] = {}

        async with pool.acquire() as conn:
            current_tables = await _public_base_tables(conn)
            current_set = set(current_tables)
            missing_backup_tables = sorted(original_backup_set - current_set)
            protected = set(PROTECTED_MERGE_KEYS)

            async with conn.transaction():
                created_backup_only = await _ensure_streamed_backup_only_tables(
                    conn,
                    {
                        table: table_stats[table]
                        for table in missing_backup_tables
                    },
                    current_set,
                )

                await _ensure_streamed_backup_columns(
                    conn,
                    table_stats,
                    current_set,
                )

                still_missing = sorted(original_backup_set - current_set)
                for table in still_missing:
                    results[table] = 0

                if still_missing:
                    print(
                        "[backup_repo] Skipping schema-less backup-only table(s): "
                        + ", ".join(still_missing)
                    )

                restorable_backup_set = original_backup_set - set(still_missing)
                if backup_type == "sync":
                    clear_set = current_set - protected
                else:
                    clear_set = restorable_backup_set - protected

                ordered_clear = await _foreign_key_order(conn, list(clear_set))
                ordered_restore = await _foreign_key_order(
                    conn,
                    list(restorable_backup_set - protected),
                )

                metadata_by_table: dict[str, dict[str, dict]] = {}
                for table in sorted(set(clear_set) | restorable_backup_set):
                    metadata_by_table[table] = await _table_column_metadata(conn, table)

                # Keep the original restore semantics: delete in reverse FK order,
                # then restore parents before children, all inside one transaction.
                for table in reversed(ordered_clear):
                    await conn.execute(f'DELETE FROM "{table}";')

                for table, key_column in PROTECTED_MERGE_KEYS.items():
                    if table in restorable_backup_set:
                        stats = table_stats[table]
                        print(
                            f"[backup_repo] Restoring protected table {table}: "
                            f"{stats['row_count']} rows (merge mode)."
                        )
                        results[table] = await _restore_streamed_rows(
                            conn,
                            table,
                            stats["row_file"],
                            metadata_by_table[table],
                            conflict_column=key_column,
                        )

                for table in ordered_restore:
                    stats = table_stats[table]
                    print(
                        f"[backup_repo] Restoring table {table}: "
                        f"{stats['row_count']} rows in batches of 200."
                    )
                    results[table] = await _restore_streamed_rows(
                        conn,
                        table,
                        stats["row_file"],
                        metadata_by_table[table],
                    )
                    _restore_resource_snapshot(f"after_table_{table}", temp_dir)

                sequence_tables = list(dict.fromkeys(ordered_clear + ordered_restore))
                sequence_metadata = {
                    table: metadata_by_table[table]
                    for table in sequence_tables
                }
                if sequence_tables:
                    await _reset_sequences(
                        conn,
                        sequence_tables,
                        sequence_metadata,
                    )

                if created_backup_only:
                    print(
                        "[backup_repo] Reconstructed backup-only table(s): "
                        + ", ".join(created_backup_only)
                    )

        # Restore-related caches must be invalidated only after the DB transaction commits.
        try:
            from services.quest_engine import invalidate_period_cache
            invalidate_period_cache()
            from utils.debut_gate import invalidate_debut_cache
            invalidate_debut_cache()
            from database.broadcast_repo import _RECENT_UPSERTS
            _RECENT_UPSERTS.clear()
        except Exception:
            pass

        print(f"[backup_repo] import_tables() -> restored/merged {results}")
        return results


async def _import_tables_from_payload(payload: dict) -> dict[str, int]:
    """
    Restore a backup, with live users/known chats merged instead of replaced.

    Full /sync backups are authoritative for all non-identity application data:
    current rows are removed and backup rows are restored. The `users` and
    `broadcast_targets` registries are exceptions: existing live rows are
    preserved, while backup-only identity rows are added without duplication.

    If a backup contains a table absent from the live database, a non-empty
    backup table is reconstructed from its row data so the recovery does not
    fail merely because the backup came from a different schema revision.

    Empty backup-only tables are skipped because this JSON backup format does
    not contain CREATE TABLE metadata for a table with zero rows.
    """
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

    original_backup_set = set(
        tables
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

        if declared != original_backup_set:
            raise ValueError(
                "Full backup manifest "
                "does not match its table payload."
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

        missing_backup_tables = sorted(
            original_backup_set - current_set
        )

        protected = set(
            PROTECTED_MERGE_KEYS
        )

        async with conn.transaction():
            created_backup_only = (
                await _ensure_backup_only_tables(
                    conn,
                    {
                        table: tables[table]
                        for table in missing_backup_tables
                    },
                    current_set,
                )
            )

            # A backup may also come from a newer schema revision where an
            # existing table has gained columns that do not yet exist in the
            # live database. Add those columns before metadata validation and
            # before any rows are restored.
            await _ensure_backup_columns(
                conn,
                tables,
                current_set,
            )

            # Missing tables with no rows cannot be reconstructed from the
            # current JSON format. Do not abort all other restored data.
            still_missing = sorted(
                original_backup_set - current_set
            )

            for table in still_missing:
                results[table] = 0

            if still_missing:
                print(
                    "[backup_repo] Skipping schema-less "
                    "backup-only table(s): "
                    + ", ".join(
                        still_missing
                    )
                )

            restorable_backup_set = (
                original_backup_set
                - set(still_missing)
            )

            if backup_type == "sync":
                clear_set = (
                    current_set - protected
                )
            else:
                clear_set = (
                    restorable_backup_set
                    - protected
                )

            ordered_clear = await _foreign_key_order(
                conn,
                list(clear_set),
            )

            ordered_restore = await _foreign_key_order(
                conn,
                list(
                    restorable_backup_set
                    - protected
                ),
            )

            metadata: dict[
                str,
                dict[str, dict],
            ] = {}

            for table in sorted(
                set(clear_set)
                | restorable_backup_set
            ):
                metadata[table] = (
                    await _table_column_metadata(
                        conn,
                        table,
                    )
                )

            # Use row-level DELETE rather than TRUNCATE during recovery.
            #
            # CockroachDB can reject TRUNCATE when a table participates in an
            # index-drop/rebuild operation, producing errors such as:
            #   cannot perform TRUNCATE on "special_player_card_images"
            #   which has indexes being dropped
            #
            # DELETE does not perform that schema/index operation. We execute
            # tables in reverse foreign-key order so dependent rows are removed
            # before their parents. This also avoids CASCADE deleting protected
            # identity data such as `users` and `broadcast_targets`.
            #
            # Sequences are reset explicitly after restoration by
            # `_reset_sequences`, so switching from TRUNCATE to DELETE does not
            # leave restored SERIAL/BIGSERIAL IDs in a bad state.
            for table in reversed(
                ordered_clear
            ):
                await conn.execute(
                    f'DELETE FROM "{table}";'
                )

            # Existing live identity records win. Backup-only users/groups
            # are inserted without overwriting live values.
            for (
                table,
                key_column,
            ) in PROTECTED_MERGE_KEYS.items():
                if table in restorable_backup_set:
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

            for table in ordered_restore:
                results[table] = (
                    await _restore_rows(
                        conn,
                        table,
                        tables.get(table)
                        or [],
                        metadata[table],
                    )
                )

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

            if created_backup_only:
                print(
                    "[backup_repo] Reconstructed "
                    "backup-only table(s): "
                    + ", ".join(
                        created_backup_only
                    )
                )


    # Restored quest tables may carry different period rows than the cached ones.
    try:
        from services.quest_engine import invalidate_period_cache
        invalidate_period_cache()
        from utils.debut_gate import invalidate_debut_cache
        invalidate_debut_cache()
        from database.broadcast_repo import _RECENT_UPSERTS
        _RECENT_UPSERTS.clear()
    except Exception:
        pass

    print(
        f"[backup_repo] import_tables() -> "
        f"restored/merged {results}"
    )
    return results


async def import_tables(
    raw_bytes: bytes | str | Path,
) -> dict[str, int]:
    """Restore a backup. Path inputs use bounded streaming; bytes retain compatibility."""
    if isinstance(raw_bytes, (str, Path)):
        return await _import_tables_from_streamed_file(raw_bytes)

    payload = _decode_backup(raw_bytes)
    return await _import_tables_from_payload(payload)




