from __future__ import annotations

# Fixed Ruby -> Coin exchange packages. Only these exact Ruby amounts are valid.
COIN_EXCHANGE_CHART: dict[int, int] = {
    100: 35_000,
    250: 100_000,
    500: 210_000,
    750: 330_000,
    1_000: 450_000,
    1_500: 700_000,
    2_000: 950_000,
    3_000: 1_500_000,
    4_000: 2_250_000,
    5_000: 5_000_000,
}

MIN_RUBIES = min(COIN_EXCHANGE_CHART)
MAX_RUBIES = max(COIN_EXCHANGE_CHART)
MAX_COINS = max(COIN_EXCHANGE_CHART.values())


def coins_for_rubies(rubies: int) -> int | None:
    try:
        return COIN_EXCHANGE_CHART.get(int(rubies))
    except (TypeError, ValueError):
        return None


def format_exchange_chart() -> str:
    rows = [
        "<b>💎 RUBIES        🪙 COINS</b>",
        "<b>━━━━━━━━━━━━━━━━━━━━</b>",
    ]
    for rubies, coins in COIN_EXCHANGE_CHART.items():
        rows.append(f"<b>{rubies:>6,} 💎   ➜   {coins:>10,} 🪙</b>")
    return "\n".join(rows)
