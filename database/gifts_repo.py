"""Persistence helpers for owner-created My Gifts campaigns."""
from __future__ import annotations

import json
from typing import Any

from database.query import execute, fetch, fetchrow, fetchval


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


async def create_draft(owner_id: int, chat_id: int) -> dict | None:
    row = await fetchrow(
        """
        INSERT INTO miniapp_gift_campaigns(created_by,admin_chat_id,status,step,title)
        VALUES($1,$2,'draft','choose_type','Happy Diwali')
        RETURNING *;
        """, int(owner_id), int(chat_id),
    )
    return dict(row) if row else None


async def get_campaign(gift_id: int) -> dict | None:
    row = await fetchrow("SELECT * FROM miniapp_gift_campaigns WHERE gift_id=$1;", int(gift_id))
    return dict(row) if row else None


async def save_message(gift_id: int, chat_id: int, message_id: int, *, is_photo: bool = False) -> None:
    await execute(
        """
        UPDATE miniapp_gift_campaigns
        SET admin_chat_id=$2,admin_message_id=$3,admin_message_is_photo=$4
        WHERE gift_id=$1 AND status='draft';
        """, int(gift_id), int(chat_id), int(message_id), bool(is_photo),
    )


async def update_draft(gift_id: int, *, step: str | None = None, reward_type: str | None = None,
                       reward_config: dict | None = None, theme: str | None = None, title: str | None = None,
                       image_file_id: str | None = None, status: str | None = None) -> dict | None:
    row = await fetchrow(
        """
        UPDATE miniapp_gift_campaigns SET
            step=COALESCE($2,step),
            reward_type=COALESCE($3,reward_type),
            reward_config=COALESCE($4::jsonb,reward_config),
            theme=COALESCE($5,theme),
            title=COALESCE($6,title),
            image_file_id=CASE WHEN $7::boolean THEN $8 ELSE image_file_id END,
            status=COALESCE($9,status)
        WHERE gift_id=$1 AND status='draft'
        RETURNING *;
        """,
        int(gift_id), step, reward_type,
        _json(reward_config) if reward_config is not None else None,
        theme, title, image_file_id is not None, image_file_id, status,
    )
    return dict(row) if row else None


async def draft_for_admin(owner_id: int, chat_id: int) -> dict | None:
    row = await fetchrow(
        """
        SELECT * FROM miniapp_gift_campaigns
        WHERE created_by=$1 AND admin_chat_id=$2 AND status='draft'
        ORDER BY gift_id DESC LIMIT 1;
        """, int(owner_id), int(chat_id),
    )
    return dict(row) if row else None


async def set_schedule(gift_id: int, delay_seconds: int) -> dict | None:
    row = await fetchrow(
        """
        UPDATE miniapp_gift_campaigns
        SET status='scheduled',step='scheduled',scheduled_at=NOW()+($2::bigint * INTERVAL '1 second')
        WHERE gift_id=$1 AND status='ready'
        RETURNING *;
        """, int(gift_id), max(60, int(delay_seconds)),
    )
    return dict(row) if row else None


async def cancel_draft(gift_id: int, owner_id: int) -> bool:
    result = await execute(
        "UPDATE miniapp_gift_campaigns SET status='cancelled',step='cancelled' WHERE gift_id=$1 AND created_by=$2 AND status IN ('draft','ready');",
        int(gift_id), int(owner_id),
    )
    try:
        return int(str(result).split()[-1]) > 0
    except Exception:
        return False


async def due_campaigns(limit: int = 5) -> list[dict]:
    rows = await fetch(
        """
        SELECT gift_id FROM miniapp_gift_campaigns
        WHERE status='distributing' OR (status='scheduled' AND scheduled_at<=NOW())
        ORDER BY COALESCE(scheduled_at,created_at),gift_id
        LIMIT $1;
        """, max(1, min(20, int(limit))),
    )
    return [dict(row) for row in rows]


async def campaign_counts(gift_ids: list[int]) -> dict[int, int]:
    if not gift_ids:
        return {}
    rows = await fetch(
        "SELECT gift_id,COUNT(*) AS n FROM miniapp_gift_user_rewards WHERE gift_id=ANY($1::bigint[]) GROUP BY gift_id;",
        [int(x) for x in gift_ids],
    )
    return {int(row["gift_id"]): int(row["n"] or 0) for row in rows}
