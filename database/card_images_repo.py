"""
Database operations for player card images uploaded via /upload_img:
- a custom image per player (player_card_images)
- one shared template per card type ("bat" or "ball"), used by every
  player of that type without a custom image (template_card_image)
"""

from database.query import execute, fetchrow

# Fixed ids for the two template rows (table still has an `id` column
# for backward compatibility with the old single-template schema).
_CARD_TYPE_IDS = {"bat": 1, "ball": 2}


async def save_player_card_image(player_id: int, file_id: str, channel_message_id: int | None, uploaded_by: int) -> None:
    await execute(
        """
        INSERT INTO player_card_images (player_id, file_id, channel_message_id, uploaded_by, updated_at)
        VALUES ($1, $2, $3, $4, NOW())
        ON CONFLICT (player_id) DO UPDATE SET
            file_id = EXCLUDED.file_id,
            channel_message_id = EXCLUDED.channel_message_id,
            uploaded_by = EXCLUDED.uploaded_by,
            updated_at = NOW();
        """,
        player_id, file_id, channel_message_id, uploaded_by,
    )


async def get_player_card_image(player_id: int) -> dict | None:
    row = await fetchrow("SELECT * FROM player_card_images WHERE player_id = $1;", player_id)
    return dict(row) if row else None


async def save_card_template_image(card_type: str, file_id: str, channel_message_id: int | None, uploaded_by: int) -> None:
    """`card_type` is "bat" or "ball"."""
    await execute(
        """
        INSERT INTO template_card_image (id, card_type, file_id, channel_message_id, uploaded_by, updated_at)
        VALUES ($1, $2, $3, $4, $5, NOW())
        ON CONFLICT (card_type) DO UPDATE SET
            file_id = EXCLUDED.file_id,
            channel_message_id = EXCLUDED.channel_message_id,
            uploaded_by = EXCLUDED.uploaded_by,
            updated_at = NOW();
        """,
        _CARD_TYPE_IDS[card_type], card_type, file_id, channel_message_id, uploaded_by,
    )


async def get_card_template_image(card_type: str) -> dict | None:
    """`card_type` is "bat" or "ball"."""
    row = await fetchrow("SELECT * FROM template_card_image WHERE card_type = $1;", card_type)
    return dict(row) if row else None


async def save_special_player_card_image(special_player_id: int, file_id: str, channel_message_id: int | None, uploaded_by: int) -> None:
    await execute(
        """
        INSERT INTO special_player_card_images (special_player_id, file_id, channel_message_id, uploaded_by, updated_at)
        VALUES ($1, $2, $3, $4, NOW())
        ON CONFLICT (special_player_id) DO UPDATE SET
            file_id = EXCLUDED.file_id,
            channel_message_id = EXCLUDED.channel_message_id,
            uploaded_by = EXCLUDED.uploaded_by,
            updated_at = NOW();
        """,
        special_player_id, file_id, channel_message_id, uploaded_by,
    )


async def get_special_player_card_image(special_player_id: int) -> dict | None:
    row = await fetchrow("SELECT * FROM special_player_card_images WHERE special_player_id = $1;", special_player_id)
    return dict(row) if row else None
