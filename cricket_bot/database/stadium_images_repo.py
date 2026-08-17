from database.query import execute, fetchrow


async def get_stadium_image(stadium_name: str) -> str | None:
    row = await fetchrow(
        "SELECT file_id FROM stadium_images WHERE stadium_name = $1;", stadium_name,
    )
    return row["file_id"] if row else None


async def save_stadium_image(stadium_name: str, file_id: str) -> None:
    await execute(
        """
        INSERT INTO stadium_images (stadium_name, file_id)
        VALUES ($1, $2)
        ON CONFLICT (stadium_name) DO UPDATE SET file_id = EXCLUDED.file_id;
        """,
        stadium_name, file_id,
    )
