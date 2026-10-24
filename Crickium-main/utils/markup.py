"""Tiny formatting helpers for Telegram text."""
from __future__ import annotations

import html


def escape_html(text: str | None) -> str:
    return html.escape(text or "")


def safe_username(username: str | None) -> str:
    if not username:
        return ""
    username = username.strip().lstrip("@")
    return f"@{username}"


def bracket_username(username: str | None) -> str:
    return f"({safe_username(username)})" if username else ""
