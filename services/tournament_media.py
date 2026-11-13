"""Lot card images for the tournament auction: render once, send by Telegram file_id afterwards.

When a player comes up for bidding the group gets his card (photo). Telegram answers with a ``file_id``; we keep it
(memory first, the database behind it, and on the lot row itself) so that

* every ``BID ACCEPTED`` message re-uses the SAME picture through that file_id - no render, no upload;
* the same player in a later lot / round / auction needs no render at all (default-template cards are keyed by
  exactly what is drawn: player data + template, so a changed template or stats never serves a stale picture);
* a custom image (``/upload_img <Player>``) is sent through its own file_id directly - custom stays custom.
"""
from __future__ import annotations

import collections
import json
from typing import Any

from database import tournament_auction_repo as ar

_FILE_IDS: "collections.OrderedDict[str, str]" = collections.OrderedDict()
_MAX_KEYS = 4000
_FALLBACK_NO_PHOTO = object()


class LotMedia:
    """What the presenter needs to send a lot picture: a file_id when we have one, else PNG bytes to upload."""
    __slots__ = ("key", "file_id", "data", "custom")

    def __init__(self, key: str, file_id: str | None, data: bytes | None, custom: bool):
        self.key, self.file_id, self.data, self.custom = key, file_id, data, bool(custom)


def _remember_mem(key: str, file_id: str) -> None:
    _FILE_IDS[key] = file_id
    _FILE_IDS.move_to_end(key)
    while len(_FILE_IDS) > _MAX_KEYS:
        _FILE_IDS.popitem(last=False)


async def lookup(key: str) -> str | None:
    fid = _FILE_IDS.get(key)
    if fid:
        _FILE_IDS.move_to_end(key)
        return fid
    try:
        fid = await ar.get_card_file(key)
    except Exception as exc:                                                                                
        print(f"[tournament/media] file_id lookup failed: {exc!r}")
        return None
    if fid:
        _remember_mem(key, fid)
    return fid


async def remember(key: str, file_id: str, custom: bool = False) -> None:
    if not key or not file_id:
        return
    if _FILE_IDS.get(key) == file_id:
        return
    _remember_mem(key, file_id)
    try:
        await ar.save_card_file(key, file_id, custom)
    except Exception as exc:
        print(f"[tournament/media] file_id save failed: {exc!r}")


async def forget(key: str) -> None:
    _FILE_IDS.pop(key, None)
    try:
        await ar.delete_card_file(key)
    except Exception as exc:
        print(f"[tournament/media] file_id delete failed: {exc!r}")


async def _player_for(lot: dict) -> dict:
    """The player row the card is drawn from (special / showcase cards through their own resolver)."""
    raw = lot.get("card")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if isinstance(raw, dict) and str(raw.get("player_kind") or "global") in ("special", "showcase"):
        from services.giveaway import resolve_live_card
        player = await resolve_live_card(raw)
        if player is not None:
            return dict(player)
    from database.query import fetchrow
    row = await fetchrow("SELECT * FROM players WHERE LOWER(name)=LOWER($1) LIMIT 1;", str(lot.get("name") or ""))
    if row:
        return dict(row)
    return {"name": lot.get("name"), "role": lot.get("role"), "country": lot.get("country"),
            "bat_level": lot.get("bat_level"), "bowl_level": lot.get("bowl_level"),
            "batting_hand": lot.get("batting_hand"), "bowling_hand": lot.get("bowling_hand")}


async def prepare(lot: dict) -> LotMedia | None:
    """Resolve the picture of a lot. None when it cannot be produced (the card then goes out as text)."""
    try:
        from services import card_provider as cp
        player = await _player_for(lot)
        source = await cp.get_player_card_source(player)
        key = source["key"]
        if source["custom"]:
                                                                            
            return LotMedia(key, (await lookup(key)) or source["file_id"], None, True)
        fid = await lookup(key)
        if fid:
            return LotMedia(key, fid, None, False)
        data = await cp.render_default_card(player, source)
        return LotMedia(key, None, data, False)
    except Exception as exc:
        print(f"[tournament/media] card image unavailable for lot {lot.get('ord')}: {exc!r}")
        return None


async def bytes_for(lot: dict, media: LotMedia) -> bytes | None:
    """Last-resort bytes when Telegram refuses a stored file_id (expired / not a photo): download or draw again."""
    try:
        from services import card_provider as cp
        player = await _player_for(lot)
        source = await cp.get_player_card_source(player)
        if source["custom"]:
            return await cp.download_cached(source["file_id"])
        return await cp.render_default_card(player, source)
    except Exception as exc:
        print(f"[tournament/media] re-render failed: {exc!r}")
        return None
