"""
Shared constants for both card layouts (services/card_config_bat.py and
services/card_config_ball.py).

Fonts, colors, and card size are the same for every card type - only the
box *positions* differ per card type, and only because each card type
now has its own template artwork. Keeping these shared means updating a
font or color once here updates both cards; box positions stay fully
independent in their own files.
"""

from __future__ import annotations

from pathlib import Path

SERVICES_DIR = Path(__file__).resolve().parent
FONTS_DIR = SERVICES_DIR / "fonts"
ASSETS_DIR = SERVICES_DIR / "assets"

FONT_BOLD = FONTS_DIR / "Poppins-Bold.ttf"
FONT_MEDIUM = FONTS_DIR / "Poppins-Medium.ttf"
FONT_REGULAR = FONTS_DIR / "Poppins-Regular.ttf"

# The template's own reference colors, sampled directly from the artwork.
NAVY = (2, 23, 54)
TEAL = (1, 122, 124)
WHITE = (241, 241, 243)
BAR_TRACK = (183, 204, 227)

CARD_SIZE = (1536, 1024)

# Starting font sizes for auto-fit (services/player_card.py shrinks from
# here until the text fits inside its box).
FIRST_NAME_MAX_SIZE = 92
LAST_NAME_MAX_SIZE = 84
ROLE_MAX_SIZE = 32
OVR_MAX_SIZE = 78
LEVEL_VALUE_MAX_SIZE = 68
STYLE_VALUE_MAX_SIZE = 30

MIN_FONT_SIZE = 14
