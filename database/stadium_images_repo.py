from database import cache
from database.query import execute, fetchrow


async def get_stadium_image(stadium_name: str) -> str | None:
    async def _load():
        row = await fetchrow(
            "SELECT file_id FROM stadium_images WHERE stadium_name = $1;", stadium_name,
        )
        return row["file_id"] if row else None
    # admin-uploaded, rarely changes; the save below invalidates the "catalog" tag
    return await cache.get_or_load(
        f"img:stadium:{str(stadium_name).lower()}", _load,
        ttl=21600, l1_ttl=1800, tag="catalog", neg_ttl=300,
    )


async def save_stadium_image(stadium_name: str, file_id: str) -> None:
    await execute(
        """
        INSERT INTO stadium_images (stadium_name, file_id)
        VALUES ($1, $2)
        ON CONFLICT (stadium_name) DO UPDATE SET file_id = EXCLUDED.file_id;
        """,
        stadium_name, file_id,
    )
