"""Build shareable Telegram Mini App links for read-only match scoring."""
from __future__ import annotations

import os
import re

_SUPPORTED_ENGINES = {"PLAY", "PLAYSO", "PLAYINT", "PLAYIPL", "PLAYWPL", "MATCH"}


def match_scoring_keyboard(engine: str, match_id: int) -> dict:
    """Return the match-ready inline keyboard for a public read-only score view."""
    normalized = str(engine or "").upper().strip()
    numeric_id = int(match_id)
    if normalized not in _SUPPORTED_ENGINES or numeric_id <= 0:
        raise ValueError("Unsupported match scoring link")
    username = os.getenv("MINIAPP_BOT_USERNAME", "CrickiumBot").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        username = "CrickiumBot"
    start_param = f"score_{normalized}_{numeric_id}"
    # The startapp value uses Telegram's allowed [A-Za-z0-9_-] alphabet.
    url = f"https://t.me/{username}?startapp={start_param}&mode=fullscreen"
    return {"inline_keyboard": [[{
        "text": "Open Mini App for Live Scoring",
        "url": url,
        "style": "primary",
    }]]}
