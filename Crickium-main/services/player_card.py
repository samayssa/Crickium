"""
Renders a player card image (like services/assets/default_template.png)
with a specific player's stats drawn on top, using Pillow only - no
external services, so this works fully offline on the device running
the bot.

Usage:
    from services.player_card import render_player_card
    png_bytes = render_player_card(player_row, template_bytes=optional_bytes)
"""

from __future__ import annotations

from io import BytesIO
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from services import card_config_bat, card_config_ball
from services.card_config_common import MIN_FONT_SIZE
from utils.style import batting_style_code, bowling_style_text

# "bat" -> batsman / WK / all-rounder-with-higher-batting-level card
# "ball" -> bowler / all-rounder-with-higher-bowling-level card
# See services/card_provider.resolve_card_type() for how a player's role
# is mapped to one of these.
_CARD_CONFIGS = {"bat": card_config_bat, "ball": card_config_ball}

# The shared utils/style.py phrasing ("Right-hand batsman") is meant for
# chat messages. The template's own placeholder text is much shorter
# ("RIGHT-HANDED"), so the card uses its own short labels to match it.
_CARD_BATTING_STYLE_TEXT = {
    "RH": "RIGHT-HANDED",
    "LH": "LEFT-HANDED",
}


def _card_batting_style(player: dict) -> str:
    code = batting_style_code(player.get("batting_hand"))
    if code:
        return _CARD_BATTING_STYLE_TEXT.get(code, code)
    return "UNKNOWN"


@lru_cache(maxsize=None)
def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int, tuple]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1], bbox


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path,
    box: tuple[int, int, int, int],
    start_size: int,
    min_size: int = MIN_FONT_SIZE,
) -> ImageFont.FreeTypeFont:
    """Shrinks the font until `text` fits inside `box` on both axes.
    This is what keeps long player names / country names from spilling
    out of the template's boxes."""
    box_w = box[2] - box[0]
    box_h = box[3] - box[1]
    size = start_size
    path_str = str(font_path)

    while size > min_size:
        font = _load_font(path_str, size)
        tw, th, _ = _text_size(draw, text, font)
        if tw <= box_w and th <= box_h:
            return font
        size -= 2

    return _load_font(path_str, min_size)


def _draw_left_middle(draw, text, box, font, color):
    x0, y0, x1, y1 = box
    _, th, bbox = _text_size(draw, text, font)
    y = y0 + ((y1 - y0) - th) // 2 - bbox[1]
    draw.text((x0, y), text, font=font, fill=color)


def _draw_centered(draw, text, box, font, color):
    x0, y0, x1, y1 = box
    tw, th, bbox = _text_size(draw, text, font)
    x = x0 + ((x1 - x0) - tw) // 2 - bbox[0]
    y = y0 + ((y1 - y0) - th) // 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=color)


def _draw_bar(draw, box, percent: float, fill_color, track_color):
    x0, y0, x1, y1 = box
    radius = (y1 - y0) // 2
    percent = max(0.0, min(100.0, percent))

    draw.rounded_rectangle(box, radius=radius, fill=track_color)

    fill_w = int((x1 - x0) * (percent / 100.0))
    if fill_w >= (y1 - y0):  # only draw the fill once it's wide enough to have rounded ends
        draw.rounded_rectangle((x0, y0, x0 + fill_w, y1), radius=radius, fill=fill_color)
    elif fill_w > 0:
        draw.rectangle((x0, y0, x0 + fill_w, y1), fill=fill_color)


def _split_name(full_name: str) -> tuple[str, str]:
    parts = [p for p in str(full_name or "").strip().split() if p]
    if not parts:
        return "PLAYER", ""
    first = parts[0]
    surname = " ".join(parts[1:])
    return first, surname


def _overall_rating(bat_level, bowl_level) -> int:
    try:
        bat = int(bat_level or 0)
        bowl = int(bowl_level or 0)
    except (TypeError, ValueError):
        return 0
    return max(bat, bowl)


def overall_rating(bat_level, bowl_level) -> int:
    """Public alias of the OVR formula used on the card itself, so other
    modules (e.g. handlers/player.py, handlers/claim.py) that need a
    player's overall level for rarity/pricing stay in sync with what's
    drawn on the card. The bot now uses the player's higher level
    (batting or bowling) as the OVR so rarity and pricing follow the
    dominant skill."""
    return _overall_rating(bat_level, bowl_level)


def render_player_card(player: dict, template_bytes: bytes | None = None, card_type: str = "bat") -> bytes:
    """
    Draws `player`'s stats onto the card template and returns PNG bytes.
    `player` is a row from the `players` table (name, country, role,
    bat_level, bowl_level, batting_hand, bowling_hand, ...).
    `card_type` is "bat" or "ball" - picks which layout config (box
    positions) and which bundled fallback template to use. Callers
    normally get this from services.card_provider.resolve_card_type().
    If `template_bytes` isn't given, falls back to that card type's
    bundled default template (services/assets/default_template.png for
    "bat", services/assets/ball_template.png for "ball").
    """
    cfg = _CARD_CONFIGS.get(card_type, card_config_bat)

    if template_bytes:
        image = Image.open(BytesIO(template_bytes)).convert("RGB")
    else:
        image = Image.open(cfg.DEFAULT_TEMPLATE_PATH).convert("RGB")

    if image.size != cfg.CARD_SIZE:
        image = image.resize(cfg.CARD_SIZE)

    draw = ImageDraw.Draw(image)

    first_name, surname = _split_name(player.get("name"))
    role = str(player.get("role") or "").upper()
    bat_level = player.get("bat_level") or 0
    bowl_level = player.get("bowl_level") or 0
    ovr = _overall_rating(bat_level, bowl_level)
    country = str(player.get("country") or "Unknown").upper()
    batting_style = _card_batting_style(player).upper()
    bowling_style = bowling_style_text(player.get("bowling_hand")).upper()

    # First name (navy)
    font = _fit_font(draw, first_name.upper(), cfg.FONT_BOLD, cfg.FIRST_NAME_BOX, cfg.FIRST_NAME_MAX_SIZE)
    _draw_left_middle(draw, first_name.upper(), cfg.FIRST_NAME_BOX, font, cfg.NAVY)

    # Surname (teal) - only drawn if the player has one
    if surname:
        font = _fit_font(draw, surname.upper(), cfg.FONT_BOLD, cfg.LAST_NAME_BOX, cfg.LAST_NAME_MAX_SIZE)
        _draw_left_middle(draw, surname.upper(), cfg.LAST_NAME_BOX, font, cfg.TEAL)

    # Role (navy)
    if role:
        font = _fit_font(draw, role, cfg.FONT_MEDIUM, cfg.ROLE_BOX, cfg.ROLE_MAX_SIZE)
        _draw_left_middle(draw, role, cfg.ROLE_BOX, font, cfg.NAVY)

    # OVR (teal, centered) - now based on the player's higher level
    ovr_text = str(ovr)
    font = _fit_font(draw, ovr_text, cfg.FONT_BOLD, cfg.OVR_BOX, cfg.OVR_MAX_SIZE)
    _draw_centered(draw, ovr_text, cfg.OVR_BOX, font, cfg.TEAL)

    # Batting level (teal, centered) + progress bar
    bat_text = str(bat_level)
    font = _fit_font(draw, bat_text, cfg.FONT_BOLD, cfg.BAT_VALUE_BOX, cfg.LEVEL_VALUE_MAX_SIZE)
    _draw_centered(draw, bat_text, cfg.BAT_VALUE_BOX, font, cfg.TEAL)
    _draw_bar(draw, cfg.BAT_BAR_BOX, float(bat_level), cfg.TEAL, cfg.BAR_TRACK)

    # Bowling level (teal, centered) + progress bar
    bowl_text = str(bowl_level)
    font = _fit_font(draw, bowl_text, cfg.FONT_BOLD, cfg.BOWL_VALUE_BOX, cfg.LEVEL_VALUE_MAX_SIZE)
    _draw_centered(draw, bowl_text, cfg.BOWL_VALUE_BOX, font, cfg.TEAL)
    _draw_bar(draw, cfg.BOWL_BAR_BOX, float(bowl_level), cfg.TEAL, cfg.BAR_TRACK)

    # Batting style (teal)
    font = _fit_font(draw, batting_style, cfg.FONT_BOLD, cfg.BATTING_STYLE_BOX, cfg.STYLE_VALUE_MAX_SIZE)
    _draw_left_middle(draw, batting_style, cfg.BATTING_STYLE_BOX, font, cfg.TEAL)

    # Bowling style (teal)
    font = _fit_font(draw, bowling_style, cfg.FONT_BOLD, cfg.BOWLING_STYLE_BOX, cfg.STYLE_VALUE_MAX_SIZE)
    _draw_left_middle(draw, bowling_style, cfg.BOWLING_STYLE_BOX, font, cfg.TEAL)

    # Country (teal)
    font = _fit_font(draw, country, cfg.FONT_BOLD, cfg.COUNTRY_BOX, cfg.STYLE_VALUE_MAX_SIZE)
    _draw_left_middle(draw, country, cfg.COUNTRY_BOX, font, cfg.TEAL)

    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
