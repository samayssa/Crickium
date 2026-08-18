from __future__ import annotations

print("menu.py loaded")

from handlers.registry import register
from app import app


MENU_TEXT = """<b>Crickium Bot Command Manual.</b>

<blockquote><b>🏏 Getting Started</b>

- "/start" — Open Bot
- "/debut" — Create Your First Playing XI
- "/app" — Open Mini App</blockquote>

<blockquote><b>👤 Player &amp; Profile</b>

- "/player" — Search a Player
- "/profile" — View Your Profile
- "/plstats" — View Player Statistics
- "/squad" — View Your Squad
- "/pxl" — View Your Playing XI</blockquote>

<blockquote><b>🎴 Cards &amp; Collection</b>

- "/claim" — Claim Hourly Player
- "/buy" — Buy Player
- "/sell" — Sell Player
- "/give" — Send Coins to Friends</blockquote>

<blockquote><b>🏦 Economy</b>

- "/mybank" — View Your Bank</blockquote>

<blockquote><b>🏏 Match &amp; Gameplay</b>

- "/play" — Play Game with Friends
- "/match" — Under Development
- "/changexl" — Change Your XI
- "/endgame" — End Current Game
- "/exitgame" — Exit Current Play Game</blockquote>

<blockquote><b>⚡ Quick Start</b>

"/start" → "/debut" → "/squad" → "/pxl" → "/play"</blockquote>"""


@register("menu")
async def menu_command(message):
    chat_id = message["chat"]["id"]
    await app.send_message(chat_id, MENU_TEXT, parse_mode="HTML")
