"""
Layout coordinates for the Platinum tier player card template
(card_coordinates/platinum.py).

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

TIER_KEY = "platinum"
TIER_TITLE = "Platinum"

                                                            
CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720

                              
                                                                          
                                                               
                                                                       
                                                  
PROFILE_CIRCLE = {
    "center": (934, 403),
    "radius": 194,
}

                                                                      
PLAYER_NAME_BOX = {
    "box": (345, 205, 630, 276),
    "center": (490, 270),
}

                                                                      
                                                                     
                                                   
TIER_PILL = {
    "icon_center": (215, 345),
    "value_box": (495, 315, 575, 362),                                     
    "value_center": (537, 341),
}

                     
CAPTAIN_ROW = {
    "icon_center": (200, 431),
    "row_box": (60, 395, 700, 466),
    "value_box": (331, 410, 605, 476),                                  
    "value_center_y": 486,
}

                       
FRANCHISE_ROW = {
    "icon_center": (202, 501),
    "row_box": (60, 466, 700, 537),
    "value_box": (371, 486, 630, 557),                                  
    "value_center_y": 501,
}
410
                   
SQUAD_ROW = {
    "icon_center": (201, 573),
    "row_box": (60, 537, 700, 609),
    "value_box": (422, 547, 662, 619),                               
    "value_center_y": 573,
}

                                             
CARD_SAFE_ZONE = {
    "box": (12, 18, 1268, 700),
}
