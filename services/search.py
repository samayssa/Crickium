"""
Image lookup helper for the /play "MATCH READY" card, so it can be
sent with a real photo of the randomly-picked stadium as the caption
image instead of plain text.

Back to the "duckduckgo-search-api" library (pip install
duckduckgo-search-api, import `from ddg import Duckduckgo`) - per a
tested working script confirming it does find usable image links for
a stadium query. Two things this version does differently from the
earlier attempt that made it actually work:

1. Fallback to the first result's URL even when none of the result
   URLs obviously end in .jpg/.png. DuckDuckGo's general search often
   returns a page whose URL Telegram can still fetch and render fine
   as a photo (or a CDN URL that doesn't happen to end in a normal
   image extension) - refusing early on a strict extension check was
   throwing away perfectly good results.
2. This module no longer downloads the image itself. It just returns
   the URL, and handlers/play/live.py hands that URL straight to
   Telegram's send_photo - Telegram's own servers fetch it. That
   sidesteps every hotlink-protection / header-matching problem our
   own direct HTTP requests were running into (DuckDuckGo doesn't care
   who Telegram's fetcher is; it wasn't happy with requests coming
   from this process). If Telegram can't fetch that URL either,
   send_photo raises and handlers/play/live.py falls back to a plain
   text message - so a bad URL never breaks the match.
"""

from __future__ import annotations

import asyncio

_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def _get_duckduckgo_client():
    try:
        from ddg import Duckduckgo
        return Duckduckgo()
    except ImportError as exc:
        print(f"[services/search] 'duckduckgo-search-api' isn't installed (import 'ddg' failed): {exc!r}")
        return None


def _looks_like_image_url(url: str) -> bool:
    return bool(url) and url.lower().split("?")[0].endswith(_IMAGE_EXTENSIONS)


def _search_image_url(query: str) -> str | None:
    """Blocking DuckDuckGo search - duckduckgo-search-api is
    synchronous, so this is only ever called via asyncio.to_thread."""
    client = _get_duckduckgo_client()
    if client is None:
        return None

    try:
        response = client.search(query)
    except Exception as exc:
        print(f"[services/search] DuckDuckGo search failed for {query!r}: {exc!r}")
        return None

    if not isinstance(response, dict) or not response.get("success"):
        print(f"[services/search] DuckDuckGo search returned no usable results for {query!r}: {response!r}")
        return None

    results = response.get("data") or []
    if not results:
        print(f"[services/search] DuckDuckGo search returned 0 results for {query!r}")
        return None

    for result in results:
        url = result.get("url") or ""
        if _looks_like_image_url(url):
            return url

    # No result obviously ends in an image extension - fall back to the
    # first result's URL anyway and let Telegram's own fetcher try it.
    fallback_url = results[0].get("url")
    if fallback_url:
        print(f"[services/search] No obvious image URL for {query!r}, trying first result as a fallback: {fallback_url}")
    return fallback_url


async def find_image_url(query: str) -> str | None:
    return await asyncio.to_thread(_search_image_url, query)


async def find_stadium_image_url(stadium_name: str) -> str | None:
    """Returns a URL for a photo of the given cricket stadium, or None
    if DuckDuckGo returned nothing at all. The caller is expected to
    hand this straight to Telegram's send_photo and fall back to text
    if Telegram itself can't fetch it - this function never downloads
    the image itself."""
    return await find_image_url(f"{stadium_name} cricket stadium")
