"""Safety policy for recurring full database backups.

A full backup reads every public application table. Running it too often
creates substantial database egress and competes with live game queries.
Manual /sync remains available for an immediate full backup.
"""

SECONDS_PER_DAY = 24 * 60 * 60
DEFAULT_AUTO_BACKUP_INTERVAL_SECONDS = SECONDS_PER_DAY
MIN_AUTO_BACKUP_INTERVAL_SECONDS = SECONDS_PER_DAY


def effective_auto_backup_interval(requested_seconds: int | str | None) -> int:
    """Return a safe cadence, never allowing full exports more often than daily."""
    try:
        requested = int(requested_seconds) if requested_seconds not in (None, "") else DEFAULT_AUTO_BACKUP_INTERVAL_SECONDS
    except (TypeError, ValueError):
        requested = DEFAULT_AUTO_BACKUP_INTERVAL_SECONDS
    return max(MIN_AUTO_BACKUP_INTERVAL_SECONDS, requested)
