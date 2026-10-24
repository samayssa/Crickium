
from __future__ import annotations

import httpx

from .config import TELEGRAM_API_BASE, TELEGRAM_FILE_BASE


async def _call(method: str, payload: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{TELEGRAM_API_BASE}/{method}", json=payload or {})
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data)
        return data["result"]


async def get_file_url(file_id: str) -> str | None:
    try:
        file_info = await _call("getFile", {"file_id": file_id})
    except Exception:
        return None
    file_path = file_info.get("file_path")
    if not file_path:
        return None
    return f"{TELEGRAM_FILE_BASE}/{file_path}"


async def get_profile_photo_url(user_id: int) -> str | None:
    photos = await _call("getUserProfilePhotos", {"user_id": user_id, "limit": 1})
    items = photos.get("photos") or []
    if not items:
        return None

    best = items[0][-1]
    return await get_file_url(best["file_id"])
