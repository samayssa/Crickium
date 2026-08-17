"""
Buy/sell price chart keyed by a player's overall level (OVR = the higher of
bat_level and bowl_level, see services.player_card.overall_rating).

LEVEL | BUY PRICE | SELL PRICE (45% of buy)

Used wherever a player's price needs to be shown: /player lookups,
/claim, and any future /buy-style command.
"""

from __future__ import annotations

# level -> (buy_price, sell_price)
PRICE_CHART: dict[int, tuple[int, int]] = {
    55: (100, 45),
    56: (130, 58),
    57: (170, 76),
    58: (220, 99),
    59: (300, 135),
    60: (400, 180),
    61: (550, 248),
    62: (750, 338),
    63: (1_000, 450),
    64: (1_350, 608),
    65: (1_800, 810),
    66: (2_500, 1_125),
    67: (3_500, 1_575),
    68: (5_000, 2_250),
    69: (7_000, 3_150),
    70: (10_000, 4_500),
    71: (14_000, 6_300),
    72: (20_000, 9_000),
    73: (28_000, 12_600),
    74: (40_000, 18_000),
    75: (60_000, 27_000),
    76: (85_000, 38_250),
    77: (120_000, 54_000),
    78: (170_000, 76_500),
    79: (240_000, 108_000),
    80: (340_000, 153_000),
    81: (480_000, 216_000),
    82: (650_000, 292_500),
    83: (900_000, 405_000),
    84: (1_200_000, 540_000),
    85: (1_550_000, 697_500),
    86: (1_950_000, 877_500),
    87: (2_400_000, 1_080_000),
    88: (2_900_000, 1_305_000),
    89: (3_450_000, 1_552_500),
    90: (4_050_000, 1_822_500),
    91: (4_700_000, 2_115_000),
    92: (5_350_000, 2_407_500),
    93: (5_950_000, 2_677_500),
    94: (6_450_000, 2_902_500),
    95: (6_900_000, 3_105_000),
    96: (7_250_000, 3_262_500),
    97: (7_550_000, 3_397_500),
    98: (7_800_000, 3_510_000),
    99: (8_000_000, 3_600_000),
}

_MIN_LEVEL = min(PRICE_CHART)
_MAX_LEVEL = max(PRICE_CHART)


def get_price(level: int) -> tuple[int, int]:
    """
    Returns (buy_price, sell_price) for a given overall level. The chart
    only defines 55-99, so levels outside that range are clamped to the
    nearest end instead of raising or guessing at unlisted numbers.
    """
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = _MIN_LEVEL

    clamped = max(_MIN_LEVEL, min(_MAX_LEVEL, level))
    return PRICE_CHART[clamped]


def format_price(amount: int) -> str:
    """
    Formats a price for display:
    - under 1000    -> plain number, e.g. "450"
    - 1000+         -> abbreviated with K/M, e.g. "1K", "10K", "1M", "2M"
      (one decimal place kept only when it's not a round number, e.g. "4.5K")
    """
    amount = int(amount)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)

    if amount < 1_000:
        return f"{sign}{amount}"

    if amount < 1_000_000:
        value = amount / 1_000
        unit = "K"
    else:
        value = amount / 1_000_000
        unit = "M"

    text = f"{value:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return f"{sign}{text}{unit}"
