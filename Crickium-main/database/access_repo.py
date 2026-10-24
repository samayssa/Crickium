from database.query import execute, fetch, fetchrow


async def grant_upload_access(user_id: int, granted_by: int) -> bool:
    """Grants a user access to /upload_pl and /upload_prob.
    Returns True if newly granted, False if they already had access."""
    existing = await fetchrow("SELECT user_id FROM authorized_uploaders WHERE user_id = $1;", user_id)
    if existing:
        return False

    await execute(
        """
        INSERT INTO authorized_uploaders (user_id, granted_by)
        VALUES ($1, $2)
        ON CONFLICT (user_id) DO NOTHING;
        """,
        user_id, granted_by,
    )
    return True


async def revoke_upload_access(user_id: int) -> bool:
    """Removes a user's granted access. Returns True if a row was actually deleted."""
    result = await execute("DELETE FROM authorized_uploaders WHERE user_id = $1;", user_id)
    return bool(result) and result.split()[-1] != "0"


async def has_upload_access(user_id: int) -> bool:
    row = await fetchrow("SELECT 1 FROM authorized_uploaders WHERE user_id = $1;", user_id)
    return row is not None


async def list_upload_access() -> list:
    rows = await fetch(
        "SELECT user_id, granted_by, granted_at FROM authorized_uploaders ORDER BY granted_at ASC;"
    )
    return [dict(row) for row in rows]
