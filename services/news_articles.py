"""Pure helpers for parsing admin-published Crickium news captions."""
from __future__ import annotations

import re


def parse_news_caption(caption: str) -> tuple[str, str] | None:
    """Split a photo caption into a single-line title and story at the first blank line."""
    value = str(caption or "").strip()
    if not value:
        return None
    parts = re.split(r"\r?\n[ \t]*\r?\n", value, maxsplit=1)
    if len(parts) != 2:
        return None
    title, description = parts[0].strip(), parts[1].strip()
    if not title or not description:
        return None
    if "\n" in title or "\r" in title or len(title) > 180:
        return None
    return title, description
