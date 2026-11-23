from database.query import execute, fetch, fetchrow


async def grant_upload_access(user_id: int, granted_by: int) -> bool:
    """Grant upload access once; the INSERT itself is race-safe."""
    row = await fetchrow(
        """
        INSERT INTO authorized_uploaders (user_id, granted_by)
        VALUES ($1, $2)
        ON CONFLICT (user_id) DO NOTHING
        RETURNING user_id;
        """,
        int(user_id), int(granted_by),
    )
    return row is not None


async def revoke_upload_access(user_id: int) -> bool:
    """Remove a user's granted upload access."""
    result = await execute("DELETE FROM authorized_uploaders WHERE user_id = $1;", int(user_id))
    return bool(result) and result.split()[-1] != "0"


async def has_upload_access(user_id: int) -> bool:
    row = await fetchrow("SELECT 1 FROM authorized_uploaders WHERE user_id = $1;", int(user_id))
    return row is not None


async def list_upload_access() -> list[dict]:
    """Return every grant, including profile details when available."""
    rows = await fetch(
        """
        SELECT
            a.user_id,
            a.granted_by,
            a.granted_at,
            u.username,
            u.first_name
        FROM authorized_uploaders AS a
        LEFT JOIN users AS u ON u.user_id = a.user_id
        ORDER BY a.granted_at ASC, a.user_id ASC;
        """
    )
    return [dict(row) for row in rows]
