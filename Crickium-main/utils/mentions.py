"""
Helpers for mentioning/tagging Telegram users in messages.
Not every user has a public @username, so we fall back to a
tg://user?id=... deep-link mention using their first name.
"""

from __future__ import annotations

import re

_MD_SPECIALS = r"_*[]()~`>#+-=|{}.!\\"
_MD_ESCAPE_RE = re.compile(rf"([{re.escape(_MD_SPECIALS)}])")


def _escape_markdown(text: str | None) -> str:
    return _MD_ESCAPE_RE.sub(r"\\\1", str(text or ""))


def mention(user_id: int, username: str | None, first_name: str | None) -> str:
    """Returns a Markdown-formatted mention. Requires parse_mode='Markdown'."""
    name = first_name or "Player"
    if username:
        return f"@{_escape_markdown(username)}"
    safe_name = _escape_markdown(name)
    if user_id is None:
        return safe_name
    return f"[{safe_name}](tg://user?id={user_id})"


def display_name(username: str | None, first_name: str | None) -> str:
    """Plain display name (no markdown), for use in plain-text contexts."""
    if username:
        return f"@{username}"
    return first_name or "Player"


def mention_html(user_id: int, username: str | None, first_name: str | None) -> str:
    """Same as mention(), but HTML-escaped for use with parse_mode='HTML'."""
    import html as _html

    name = first_name or "Player"
    if username:
        return f"@{_html.escape(username)}"
    safe_name = _html.escape(name)
    if user_id is None:
        return safe_name
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>'


def mention_name_only_html(user_id: int, first_name: str | None) -> str:
    """Like mention_html(), but ALWAYS renders as a name deep-link -
    never falls back to '@username', even if the user has one. Used by
    /profile so the profile card shows a clean display name instead of
    a raw handle."""
    import html as _html

    safe_name = _html.escape((first_name or "Player").strip() or "Player")
    if user_id is None:
        return safe_name
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>'
