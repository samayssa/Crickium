"""
Bridges three things to produce the final card image for a player:
1. database/card_images_repo.py  - which file_id (if any) to use
2. app.py                        - downloading that file from Telegram
3. services/player_card.py       - rendering stats onto a template

This is what a future player-lookup/search command should call - it
does not register any Telegram command itself.
"""

from __future__ import annotations

from app import app
from database.card_images_repo import get_player_card_image, get_card_template_image
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


async def get_player_card_bytes(player: dict) -> tuple[bytes, bool]:
    """
    Returns (image_bytes, is_custom):
    - is_custom=True  -> a pre-made image uploaded via `/upload_img <Player Name>`,
      already complete, send it as-is.
    - is_custom=False -> generated on the fly from a template (either the
      admin-uploaded one via `/upload_img bat-card` / `/upload_img ball-card`,
      or the matching bundled default), picked by the player's role.
    """
    player_id = player.get("player_id")

    custom = await get_player_card_image(player_id) if player_id else None
    if custom and custom.get("file_id"):
        image_bytes = await app.download_media(custom["file_id"])
        return image_bytes, True

    card_type = resolve_card_type(player)

    template_bytes = None
    template = await get_card_template_image(card_type)
    if template and template.get("file_id"):
        template_bytes = await app.download_media(template["file_id"])

    return render_player_card(player, template_bytes=template_bytes, card_type=card_type), False
