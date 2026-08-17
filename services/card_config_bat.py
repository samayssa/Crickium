"""
Layout configuration for the BAT CARD (batsman / WK / all-rounder-with-
higher-batting-level player card), rendered by services/player_card.py.

This file only holds box *positions* for this card type. Fonts, colors,
and card size are shared from card_config_common.py.

This is a standalone copy of card_config_ball.py's coordinates on
purpose - edit the boxes below to tune the bat card. It will NOT affect
the ball card (services/card_config_ball.py), which is tuned
separately against its own template artwork.

If the bat-card template artwork is ever redesigned, only this file
should need to change - player_card.py itself doesn't hardcode any
positions.
"""

from __future__ import annotations

from services.card_config_common import *  # noqa: F401,F403 - fonts/colors/sizes shared with card_config_ball.py

DEFAULT_TEMPLATE_PATH = ASSETS_DIR / "default_template.png"

# Each box is (x0, y0, x1, y1). Text is auto-shrunk to fit inside its box
# (see player_card._fit_font), so being a little generous here is safer
# than being exact - it guarantees nothing overflows the design's columns.
FIRST_NAME_BOX = (85, 180, 630, 268)     # was "PLAYER"     - navy, left aligned
LAST_NAME_BOX = (85, 275, 690, 396)      # was "FULL NAME"  - teal, left aligned
ROLE_BOX = (85, 428, 540, 498)           # was "PLAYER ROLE" - navy, left aligned

OVR_BOX = (1295, 170, 1485, 258)         # was "??"         - teal, centered

BAT_VALUE_BOX = (205, 598, 345, 692)     # was "??" (batting) - teal, centered
BOWL_VALUE_BOX = (205, 810, 345, 902)    # was "??" (bowling) - teal, centered

BAT_BAR_BOX = (85, 702, 515, 720)        # progress bar track+fill
BOWL_BAR_BOX = (85, 914, 515, 932)

BATTING_STYLE_BOX = (1225, 600, 1520, 646)   # was "RIGHT-HANDED"  - teal, left aligned
BOWLING_STYLE_BOX = (1225, 745, 1520, 793)     # was "BOWLING TYPE"  - teal, left aligned
COUNTRY_BOX = (1225, 904, 1520, 954)         # was "COUNTRY NAME"  - teal, left aligned
