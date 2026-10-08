from __future__ import annotations

from typing import Any

from database.query import transaction

_COUNTER_ID = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS match_analysis_counter (
    counter_id SMALLINT PRIMARY KEY,
    last_number BIGINT NOT NULL DEFAULT 0
);

INSERT INTO match_analysis_counter (counter_id, last_number)
VALUES (1, 0)
ON CONFLICT (counter_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS match_analysis_reports (
    report_id BIGSERIAL PRIMARY KEY,
    engine TEXT NOT NULL,
    source_match_id BIGINT NOT NULL,
    match_number BIGINT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    termination TEXT NOT NULL,
    sent BOOLEAN NOT NULL DEFAULT FALSE,
    telegram_message_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    UNIQUE (engine, source_match_id)
);
"""


async def ensure_schema() -> None:
    async def _tx(conn):
        for statement in [x.strip() for x in _SCHEMA_SQL.split(";") if x.strip()]:
            await conn.execute(statement)
    await transaction(_tx)


async def reserve_report(
    *,
    engine: str,
    source_match_id: int,
    filename_prefix: str,
    termination: str,
) -> dict[str, Any]:
    """Atomically allocate one global HTML report number.

    The counter row is locked before checking the per-match row. That makes
    numbering deterministic even when two terminal callbacks race each other.
    """
    await ensure_schema()
    clean_engine = str(engine or "UNKNOWN").upper()
    source_match_id = int(source_match_id)
    termination = str(termination or "completed").strip().lower() or "completed"

    async def _tx(conn):
        counter = await conn.fetchrow(
            "SELECT last_number FROM match_analysis_counter WHERE counter_id=$1 FOR UPDATE;",
            _COUNTER_ID,
        )
        if not counter:
            await conn.execute(
                "INSERT INTO match_analysis_counter(counter_id,last_number) VALUES($1,0);",
                _COUNTER_ID,
            )
            counter = {"last_number": 0}

        existing = await conn.fetchrow(
            """
            SELECT report_id, engine, source_match_id, match_number, filename,
                   termination, sent, telegram_message_id, created_at, sent_at
            FROM match_analysis_reports
            WHERE engine=$1 AND source_match_id=$2;
            """,
            clean_engine,
            source_match_id,
        )
        if existing:
            return dict(existing), not bool(existing["sent"])

        last_number = int(counter["last_number"] or 0)
        if last_number <= 0:
            # Start from the bot's existing terminal-match population so the
            # first HTML file continues the project's long-running match
            # numbering instead of resetting to Match1 after deployment.
            try:
                baseline = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(n), 0) FROM (
                        SELECT COUNT(*) AS n FROM matches WHERE status IN ('completed','ended','timed_out','abandoned')
                        UNION ALL SELECT COUNT(*) AS n FROM match_challenges WHERE status IN ('completed','ended','timed_out','abandoned')
                        UNION ALL SELECT COUNT(*) AS n FROM play_matches WHERE status IN ('completed','ended','timed_out','abandoned')
                        UNION ALL SELECT COUNT(*) AS n FROM playint_matches WHERE status IN ('completed','ended','timed_out','abandoned')
                        UNION ALL SELECT COUNT(*) AS n FROM playipl_matches WHERE status IN ('completed','ended','timed_out','abandoned')
                        UNION ALL SELECT COUNT(*) AS n FROM wpl_matches WHERE status IN ('completed','ended','timed_out','abandoned')
                        UNION ALL SELECT COUNT(*) AS n FROM playso_matches WHERE status IN ('completed','ended','timed_out','abandoned')
                    ) counts;
                    """
                )
                last_number = max(last_number, int(baseline or 0))
            except Exception:
                pass
        next_number = last_number + 1
        await conn.execute(
            "UPDATE match_analysis_counter SET last_number=$1 WHERE counter_id=$2;",
            next_number,
            _COUNTER_ID,
        )
        prefix = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(filename_prefix or "Crickium"))
        filename = f"{prefix}-{_engine_filename(clean_engine)}-Match{next_number}.html"
        row = await conn.fetchrow(
            """
            INSERT INTO match_analysis_reports
                (engine, source_match_id, match_number, filename, termination)
            VALUES ($1,$2,$3,$4,$5)
            RETURNING report_id, engine, source_match_id, match_number, filename,
                      termination, sent, telegram_message_id, created_at, sent_at;
            """,
            clean_engine,
            source_match_id,
            next_number,
            filename,
            termination,
        )
        return dict(row), True

    return await transaction(_tx)


async def mark_sent(report_id: int, telegram_message_id: int | None = None) -> None:
    await ensure_schema()
    await _mark_sent_once(int(report_id), telegram_message_id)


async def _mark_sent_once(report_id: int, telegram_message_id: int | None) -> None:
    from database.query import execute
    await execute(
        """
        UPDATE match_analysis_reports
        SET sent=TRUE, telegram_message_id=$1, sent_at=NOW()
        WHERE report_id=$2;
        """,
        int(telegram_message_id) if telegram_message_id is not None else None,
        report_id,
    )


def _engine_filename(engine: str) -> str:
    return {
        "PLAY": "Play",
        "PLAYINT": "PlayInt",
        "PLAYIPL": "PlayIPL",
        "PLAYWPL": "PlayWPL",
        "PLAYSO": "PlaySO",
        "MATCH": "Match",
    }.get(str(engine).upper(), str(engine).title().replace("_", ""))
