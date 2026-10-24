"""
Render the tier-based /profile card by compositing a user's live
Telegram profile photo and account details onto the uploaded tier
template image.

The tier coordinate files are treated as the source of truth. This module
only scales their boxes to the actual template size when needed, so the
coordinate data itself stays untouched.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from card_coordinates import get_coordinates
from services.card_config_common import FONT_BOLD, FONT_MEDIUM, FONT_REGULAR, NAVY, TEAL, WHITE, BAR_TRACK, MIN_FONT_SIZE

# --- /profile tier card font sizes ---
# These are STARTING sizes only - _fit_font() shrinks each one down until
# the text fits inside its box (see card_coordinates/<tier>.py), so a
# bigger number here just raises the ceiling. These only affect the
# /profile tier card. They are completely separate from the bat-card /
# ball-card font sizes used by /claim, /buy, /sell, and /player, which
# live in services/card_config_common.py instead - changing the numbers
# below has no effect on those, and vice versa.
PLAYER_NAME_FONT_SIZE = 46
LEVEL_NUMBER_FONT_SIZE = 44
CAPTAIN_FONT_SIZE = 32
FRANCHISE_FONT_SIZE = 34
SQUAD_FONT_SIZE = 36


@lru_cache(maxsize=None)
def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


def _text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int, int, int]:
    # textbbox gives us left/top offsets too, which matters for precise centering.
    return draw.textbbox((0, 0), text, font=font)


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int, tuple[int, int, int, int]]:
    bbox = _text_bbox(draw, text, font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1], bbox


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path | str,
    box: tuple[int, int, int, int],
    start_size: int,
    min_size: int = MIN_FONT_SIZE,
) -> ImageFont.FreeTypeFont:
    x0, y0, x1, y1 = box
    max_w = max(1, x1 - x0)
    max_h = max(1, y1 - y0)
    size = start_size
    path_str = str(font_path)

    while size > min_size:
        font = _load_font(path_str, size)
        tw, th, _ = _text_size(draw, text, font)
        if tw <= max_w and th <= max_h:
            return font
        size -= 2

    return _load_font(path_str, min_size)


def _draw_centered(draw: ImageDraw.ImageDraw, text: str, box: tuple[int, int, int, int], font, fill) -> None:
    x0, y0, x1, y1 = box
    tw, th, bbox = _text_size(draw, text, font)
    x = x0 + ((x1 - x0) - tw) // 2 - bbox[0]
    y = y0 + ((y1 - y0) - th) // 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=fill)


def _draw_left_middle(draw: ImageDraw.ImageDraw, text: str, box: tuple[int, int, int, int], font, fill) -> None:
    x0, y0, x1, y1 = box
    _, th, bbox = _text_size(draw, text, font)
    y = y0 + ((y1 - y0) - th) // 2 - bbox[1]
    draw.text((x0, y), text, font=font, fill=fill)


def _safe_name(first_name: str | None, last_name: str | None) -> str:
    parts = [part.strip() for part in [first_name or "", last_name or ""] if part and part.strip()]
    return " ".join(parts).strip() or "PLAYER"


def _scale_num(value: float | int, scale: float) -> int:
    return int(round(float(value) * scale))


def _scale_box(box: tuple[int, int, int, int], sx: float, sy: float) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return (_scale_num(x0, sx), _scale_num(y0, sy), _scale_num(x1, sx), _scale_num(y1, sy))


def _scale_point(point: tuple[int, int], sx: float, sy: float) -> tuple[int, int]:
    x, y = point
    return (_scale_num(x, sx), _scale_num(y, sy))


def _scale_circle(circle: dict[str, Any], sx: float, sy: float) -> dict[str, Any]:
    cx, cy = _scale_point(tuple(circle["center"]), sx, sy)
    radius = max(1, _scale_num(circle["radius"], (sx + sy) / 2.0))
    # The circle's box is always DERIVED from center + radius - center and
    # radius are the single source of truth. Editing them in a
    # card_coordinates/<tier>.py file is enough; there is nothing else to
    # keep in sync.
    box = (cx - radius, cy - radius, cx + radius, cy + radius)
    return {
        "center": (cx, cy),
        "radius": radius,
        "box": box,
    }


def _scale_row(row: dict[str, Any], sx: float, sy: float) -> dict[str, Any]:
    scaled = {}
    for key, value in row.items():
        if key.endswith("_center"):
            scaled[key] = _scale_point(tuple(value), sx, sy)
        elif key.endswith("_center_y"):
            scaled[key] = _scale_num(value, sy)
        elif key.endswith("_box") or key == "box" or key == "row_box" or key == "value_box":
            scaled[key] = _scale_box(tuple(value), sx, sy)
        else:
            scaled[key] = value
    return scaled


def _load_template(template_bytes: bytes) -> Image.Image:
    image = Image.open(BytesIO(template_bytes)).convert("RGBA")
    return image


def _paste_circle_avatar(base: Image.Image, avatar_bytes: bytes | None, circle_box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = circle_box
    size = max(1, x1 - x0), max(1, y1 - y0)

    if avatar_bytes:
        try:
            avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
            avatar = ImageOps.fit(avatar, size, method=Image.Resampling.LANCZOS)
        except Exception:
            avatar = None
    else:
        avatar = None

    mask = Image.new("L", size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, size[0] - 1, size[1] - 1), fill=255)

    if avatar is None:
        avatar = Image.new("RGBA", size, (27, 41, 66, 255))
        initials = "?"
        draw = ImageDraw.Draw(avatar)
        font = _load_font(str(FONT_BOLD), max(24, size[0] // 3))
        tw, th, bbox = _text_size(draw, initials, font)
        draw.text(((size[0] - tw) / 2 - bbox[0], (size[1] - th) / 2 - bbox[1]), initials, font=font, fill=WHITE)

    base.paste(avatar, (x0, y0), mask)

    # Subtle border to make the circle read cleanly over every template.
    border = ImageDraw.Draw(base)
    border.ellipse((x0, y0, x1 - 1, y1 - 1), outline=(255, 255, 255, 215), width=max(2, size[0] // 80))


def render_profile_card(
    template_bytes: bytes,
    *,
    avatar_bytes: bytes | None,
    first_name: str | None,
    last_name: str | None,
    tier_key: str,
    tier_title: str,
    level: int,
    snapshot: dict[str, Any],
    franchise_name: str,
    global_rank: int,
    captain_name: str,
    squad_len: int,
    max_squad_size: int = 25,
) -> bytes:
    coords = get_coordinates(tier_key)
    image = _load_template(template_bytes)

    original_w = int(coords["CANVAS_WIDTH"])
    original_h = int(coords["CANVAS_HEIGHT"])
    actual_w, actual_h = image.size
    sx = actual_w / original_w if original_w else 1.0
    sy = actual_h / original_h if original_h else 1.0

    draw = ImageDraw.Draw(image)

    profile_circle = _scale_circle(coords["PROFILE_CIRCLE"], sx, sy)
    player_name_box = _scale_box(coords["PLAYER_NAME_BOX"]["box"], sx, sy)
    tier_pill = _scale_row(coords["TIER_PILL"], sx, sy)
    captain_row = _scale_row(coords["CAPTAIN_ROW"], sx, sy)
    franchise_row = _scale_row(coords["FRANCHISE_ROW"], sx, sy)
    squad_row = _scale_row(coords["SQUAD_ROW"], sx, sy)
    safe_zone = _scale_box(coords["CARD_SAFE_ZONE"]["box"], sx, sy)

    # A faint backdrop to help text remain readable on bright art.
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle(safe_zone, radius=max(12, int((safe_zone[3] - safe_zone[1]) * 0.025)), fill=(255, 255, 255, 10))
    image = Image.alpha_composite(image, overlay)
    draw = ImageDraw.Draw(image)

    _paste_circle_avatar(image, avatar_bytes, profile_circle["box"])

    display_name = _safe_name(first_name, last_name).upper()
    full_name_font = _fit_font(draw, display_name, FONT_BOLD, player_name_box, PLAYER_NAME_FONT_SIZE)
    _draw_left_middle(draw, display_name, player_name_box, full_name_font, WHITE)

    # The tier template art already bakes in "<TIER> * LEVEL" as part of
    # the image itself - we only ever draw the level NUMBER, in the empty
    # space right after the word "LEVEL".
    level_text = str(int(level or 0))
    level_box = tier_pill["value_box"]
    level_font = _fit_font(draw, level_text, FONT_BOLD, level_box, LEVEL_NUMBER_FONT_SIZE)
    _draw_centered(draw, level_text, level_box, level_font, WHITE)

    # Captain / franchise / squad rows
    captain_text = str(captain_name or "Not assigned").upper()
    franchise_text = str(franchise_name or "Unknown").upper()
    squad_text = f"{int(squad_len or 0)}/{int(max_squad_size)}"

    captain_font = _fit_font(draw, captain_text, FONT_BOLD, captain_row["value_box"], CAPTAIN_FONT_SIZE)
    _draw_left_middle(draw, captain_text, captain_row["value_box"], captain_font, WHITE)

    franchise_font = _fit_font(draw, franchise_text, FONT_BOLD, franchise_row["value_box"], FRANCHISE_FONT_SIZE)
    _draw_left_middle(draw, franchise_text, franchise_row["value_box"], franchise_font, WHITE)

    squad_font = _fit_font(draw, squad_text, FONT_BOLD, squad_row["value_box"], SQUAD_FONT_SIZE)
    _draw_centered(draw, squad_text, squad_row["value_box"], squad_font, WHITE)

    out = BytesIO()
    image.convert("RGB").save(out, format="PNG")
    return out.getvalue()
