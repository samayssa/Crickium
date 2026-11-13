"""Authenticated Mini App data access for My Gifts."""
from __future__ import annotations

import json
from typing import Any

from . import api
from .db import fetch, fetchrow
from services import gifts as gift_service


def _json_value(value: Any, fallback: Any):
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback
    return fallback


def _gift_card(reward: dict) -> dict | None:
    player = reward.get("player")
    if not isinstance(player, dict):
        return None
    return api._player_payload(player, owned=False)


async def build_list(viewer) -> dict[str, Any]:
    rows = await fetch(
        """
        SELECT c.gift_id,c.title,c.theme,c.image_file_id,c.created_at,c.distributed_at,
               r.reward,r.status,r.opened_at,r.claimed_at
        FROM miniapp_gift_user_rewards r
        JOIN miniapp_gift_campaigns c ON c.gift_id=r.gift_id
        WHERE r.user_id=$1 AND c.status='distributed'
        ORDER BY c.distributed_at DESC NULLS LAST,c.gift_id DESC;
        """, int(viewer.id),
    )
    items = []
    unclaimed = 0
    for row in rows:
        reward = _json_value(row.get("reward"), {})
        status = str(row.get("status") or "pending")
        if status != "claimed":
            unclaimed += 1
        items.append({
            "gift_id": int(row["gift_id"]),
            "title": "Happy Diwali" if str(row.get("theme") or "normal") == "diwali" else str(row.get("title") or "A Gift for You"),
            "theme": str(row.get("theme") or "normal"),
            "has_image": bool(row.get("image_file_id")),
            "image_url": f"/api/image/gift?id={int(row['gift_id'])}" if row.get("image_file_id") else None,
            "status": status,
                                                                          
                                                                            
            "has_coins": max(0, int(reward.get("coins") or 0)) > 0,
            "has_rubies": max(0, int(reward.get("rubies") or 0)) > 0,
            "has_player": isinstance(reward.get("player"), dict),
            "has_pack": bool(reward.get("pack_key")),
            "distributed_at": row.get("distributed_at").isoformat() if row.get("distributed_at") else None,
            "claimed_at": row.get("claimed_at").isoformat() if row.get("claimed_at") else None,
        })
    return {"items": items, "unclaimed": unclaimed, "total": len(items)}


async def open_for_viewer(viewer, gift_id: int) -> dict[str, Any]:
    result = await gift_service.open_gift(int(viewer.id), int(gift_id))
    if result.get("status") != "success":
        return result
    reward = result.get("reward") or {}
    result["image_url"] = f"/api/image/gift?id={int(gift_id)}" if result.get("has_image") else None
    result["reward"] = {
        "coins": max(0, int(reward.get("coins") or 0)),
        "rubies": max(0, int(reward.get("rubies") or 0)),
        "pack_key": str(reward.get("pack_key") or "") or None,
        "player": _gift_card(reward),
    }
    return result


async def claim_for_viewer(viewer, gift_id: int) -> dict[str, Any]:
    return await gift_service.claim_gift(int(viewer.id), int(gift_id))


async def gift_image_bytes(gift_id: int, user_id: int) -> tuple[bytes, str] | None:
    row = await fetchrow(
        """SELECT c.image_file_id FROM miniapp_gift_campaigns c
           JOIN miniapp_gift_user_rewards r ON r.gift_id=c.gift_id
           WHERE c.gift_id=$1 AND r.user_id=$2 AND c.status='distributed';""",
        int(gift_id), int(user_id),
    )
    file_id = str((row or {}).get("image_file_id") or "")
    if not file_id:
        return None
    return await api._download_telegram_image(file_id)
