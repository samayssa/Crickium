"""Resolve the canonical player-card type and render bot-compatible card art.

The role resolver is intentionally import-safe for the Mini App, which only
needs this pure helper and the Pillow renderer. Bot/database imports are lazy
inside ``get_player_card_bytes`` so importing the resolver does not construct a
Telegram client in the separate Mini App worker.
"""

from __future__ import annotations

import asyncio
import collections
import hashlib

from services.player_card import render_player_card


def resolve_card_type(player: dict) -> str:
    """
    Maps a player's role to "bat" or "ball":
    - Batsman / WK -> "bat"
    - Bowler -> "ball"
    - AllRounder -> whichever level (bat_level vs bowl_level) is higher;
      a tie goes to "bat".
    - Anything else/unknown -> "bat" (safe default).
    """
    role = str(player.get("role") or "").strip().lower()

    if role == "bowler":
        return "ball"
    if role in ("batsman", "wk", "wicketkeeper", "wicket-keeper", "wicket keeper"):
        return "bat"
    if role == "allrounder":
        bat_level = int(player.get("bat_level") or 0)
        bowl_level = int(player.get("bowl_level") or 0)
        return "ball" if bowl_level > bat_level else "bat"

    return "bat"


                                                                             
                                         
                                                                             
                                                                                                               
                                                                                                                 
                                                                                                            
                                                                                                               
                                                 
_BYTES_CACHE: "collections.OrderedDict[str, bytes]" = collections.OrderedDict()
_BYTES_CACHE_MAX_ITEMS = 24
_BYTES_CACHE_MAX_BYTES = 48 * 1024 * 1024
_RENDER_SLOTS: "asyncio.Semaphore | None" = None


def _cache_put(key: str, data: bytes) -> None:
    _BYTES_CACHE[key] = data
    _BYTES_CACHE.move_to_end(key)
    total = sum(len(v) for v in _BYTES_CACHE.values())
    while _BYTES_CACHE and (len(_BYTES_CACHE) > _BYTES_CACHE_MAX_ITEMS or total > _BYTES_CACHE_MAX_BYTES):
        _k, old = _BYTES_CACHE.popitem(last=False)
        total -= len(old)


async def download_cached(file_id: str) -> bytes:
    """``app.download_media`` with an in-memory LRU in front of it."""
    hit = _BYTES_CACHE.get(file_id)
    if hit is not None:
        _BYTES_CACHE.move_to_end(file_id)
        return hit
    from app import app
    data = await app.download_media(file_id)
    _cache_put(file_id, data)
    return data


async def render_off_loop(player: dict, template_bytes: bytes | None, card_type: str) -> bytes:
    global _RENDER_SLOTS
    if _RENDER_SLOTS is None:
        _RENDER_SLOTS = asyncio.Semaphore(2)
    async with _RENDER_SLOTS:
        return await asyncio.to_thread(render_player_card, player, template_bytes, card_type, "JPEG")


async def _custom_image_row(player: dict) -> dict | None:
    from database.card_images_repo import (
        get_player_card_image,
        get_showcase_player_card_image,
        get_special_player_card_image,
    )
    from services.card_identity import card_entity_id, player_kind

    kind = player_kind(player)
    if kind == "showcase":
        return await get_showcase_player_card_image(card_entity_id(player))
    if kind == "special":
        return await get_special_player_card_image(card_entity_id(player))
    return await get_player_card_image(card_entity_id(player)) if card_entity_id(player) else None


async def get_player_card_source(player: dict) -> dict:
    """Describe the card of ``player`` WITHOUT rendering anything yet.

    Returns ``{"custom": bool, "file_id": str | None, "key": str, "card_type": str, "template_file_id": str | None}``:
    - custom image (``/upload_img <Player>``): ``file_id`` is that image's own Telegram file_id - it can be sent
      as it is (no download, no upload); ``key`` changes when the image is replaced.
    - default template: ``file_id`` is None; ``key`` identifies exactly what would be drawn (player data +
      template), so a stored file_id for that key is always the right picture.
    """
    custom = await _custom_image_row(player)
    if custom and custom.get("file_id"):
        return {"custom": True, "file_id": str(custom["file_id"]), "key": "c:" + str(custom["file_id"]),
                "card_type": resolve_card_type(player), "template_file_id": None}
    from database.card_images_repo import get_card_template_image
    card_type = resolve_card_type(player)
    template = await get_card_template_image(card_type)
    template_fid = str(template["file_id"]) if template and template.get("file_id") else None
    ident = "|".join(str(player.get(k) if player.get(k) is not None else "") for k in
                     ("name", "country", "role", "bat_level", "bowl_level", "batting_hand", "bowling_hand"))
    digest = hashlib.sha1(f"{card_type}|{template_fid or 'builtin'}|{ident}".encode("utf-8")).hexdigest()[:24]
    return {"custom": False, "file_id": None, "key": "d:" + digest, "card_type": card_type, "template_file_id": template_fid}


async def render_default_card(player: dict, source: dict) -> bytes:
    template_bytes = await download_cached(source["template_file_id"]) if source.get("template_file_id") else None
    return await render_off_loop(player, template_bytes, source["card_type"])


async def get_player_card_bytes(player: dict) -> tuple[bytes, bool]:
    """
    Returns (image_bytes, is_custom):
    - is_custom=True  -> a pre-made image uploaded via `/upload_img <Player Name>`,
      already complete, send it as-is.
    - is_custom=False -> generated on the fly from a template (either the
      admin-uploaded one via `/upload_img bat-card` / `/upload_img ball-card`,
      or the matching bundled default), picked by the player's role.
    """
    source = await get_player_card_source(player)
    if source["custom"]:
        return await download_cached(source["file_id"]), True
    return await render_default_card(player, source), False
