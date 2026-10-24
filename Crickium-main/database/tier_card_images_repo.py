"""
Database operations for level-tier card images uploaded via
'/upload_img <tier>' (e.g. '/upload_img bronze'), one image per tier -
bronze, silver, gold, platinum, diamond, legend. Whichever tier a
player currently sits on decides which of these images shows up on
their /profile card, and it automatically switches once they level
into the next tier.
"""
from __future__ import annotations

from database.query import execute, fetchrow


async def save_tier_card_image(tier_key: str, file_id: str, channel_message_id: int | None, uploaded_by: int) -> None:
    await execute(
        """
        INSERT INTO tier_card_images (tier_key, file_id, channel_message_id, uploaded_by, updated_at)
        VALUES ($1, $2, $3, $4, NOW())
        ON CONFLICT (tier_key) DO UPDATE SET
            file_id = EXCLUDED.file_id,
            channel_message_id = EXCLUDED.channel_message_id,
            uploaded_by = EXCLUDED.uploaded_by,
            updated_at = NOW();
        """,
        tier_key, file_id, channel_message_id, uploaded_by,
    )


async def get_tier_card_image(tier_key: str) -> dict | None:
    row = await fetchrow("SELECT * FROM tier_card_images WHERE tier_key = $1;", tier_key)
    return dict(row) if row else None
