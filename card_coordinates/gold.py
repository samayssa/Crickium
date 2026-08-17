"""
Layout coordinates for the Gold tier player card template
(card_coordinates/gold.py).

Measured directly against the actual rendered 1280x720 output (the size
Telegram delivers photos at after its own compression), so these line up
exactly with what /profile actually renders - no guesswork scaling from
a larger reference image.

Every tier currently shares the same layout (only the art skin/glow
color differs between templates), so these values are identical across
all six files - but each tier gets its own file so a tier's coordinates
can be tweaked independently later if its uploaded template ever has a
different layout.
"""
from __future__ import annotations

TIER_KEY = "gold"
TIER_TITLE = "Gold"

# --- Canvas the coordinates below were measured against ---
CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720

# --- Profile photo circle ---
# center and radius are the ONLY two values that matter here - the box the
# avatar gets pasted/masked into is always calculated from them
# automatically. Change center=(x, y) to move the circle, change radius
# to resize it - nothing else needs to be touched.
PROFILE_CIRCLE = {
    "center": (934, 403),
    "radius": 194,
}

# --- "PLAYER: <name>" value box (right after the "PLAYER:" label) ---
PLAYER_NAME_BOX = {
    "box": (345, 205, 630, 276),
    "center": (490, 270),
}

# --- Tier pill: the template art already bakes in "BRONZE * LEVEL" as
# part of the image itself, so we only ever draw the level NUMBER, in
# the empty space right after the word "LEVEL". ---
TIER_PILL = {
    "icon_center": (215, 345),
    "value_box": (495, 315, 575, 362),   # where just the level number goes
    "value_center": (537, 341),
}

# --- CAPTAIN row ---
CAPTAIN_ROW = {
    "icon_center": (200, 431),
    "row_box": (60, 395, 700, 466),
    "value_box": (331, 410, 605, 476),   # where the captain's name goes
    "value_center_y": 486,
}

# --- FRANCHISE row ---
FRANCHISE_ROW = {
    "icon_center": (202, 501),
    "row_box": (60, 466, 700, 537),
    "value_box": (371, 486, 630, 557),   # where the franchise name goes
    "value_center_y": 501,
}
410
# --- SQUAD row ---
SQUAD_ROW = {
    "icon_center": (201, 573),
    "row_box": (60, 537, 700, 609),
    "value_box": (422, 547, 662, 619),   # where th "{count}/25" goes
    "value_center_y": 573,
}

# --- Outer card frame / usable safe zone ---
CARD_SAFE_ZONE = {
    "box": (12, 18, 1268, 700),
}
