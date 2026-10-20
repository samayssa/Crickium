"""Hardened ASGI router for the Telegram Mini App."""
from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from urllib.parse import parse_qsl

from . import api
from .auth import MiniAppAuthError, TelegramViewer, resolve_viewer
from .config import APP_NAME, STATIC_DIR
from .db import connect, disconnect
from .migrate import migrate
from utils.miniapp_url import resolve_miniapp_url, sync_miniapp_url

_REQUESTS: dict[str, list[float]] = {}
_WINDOW_SECONDS = 60
_API_LIMIT = 90
_WRITE_LIMIT = 8
_GAME_LIMIT = 240
_MAX_BODY_BYTES = 16_384


def _headers(scope) -> dict[str, str]:
    result = {}
    for name, value in scope.get("headers", []):
        try:
            result[name.decode("latin-1").lower()] = value.decode("latin-1")
        except Exception:
            continue
    return result


def _query(scope) -> dict[str, str]:
    raw = scope.get("query_string", b"")
    try:
        return dict(parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True))
    except Exception:
        return {}


async def _body(receive) -> bytes:
    chunks = bytearray()
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        chunks.extend(message.get("body", b""))
        if len(chunks) > _MAX_BODY_BYTES:
            raise ValueError("Request body too large")
        if not message.get("more_body", False):
            break
    return bytes(chunks)


def _json_default(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return None
    return str(value)


import html as _html
import re as _re

_TAG_RE = _re.compile(r"</?[A-Za-z][^<>]*>")
_TEAM_TEXT_KEY = _re.compile(r"(team|display|result|winner|toss|name|summary|title|label|note|text|status|innings)", _re.I)


def _plain_text(text: str) -> str:
    """Strip Telegram HTML / <tg-emoji> markup so only the plain name remains."""
    cleaned = _re.sub(r"<tg-emoji\b[^>]*>.*?</tg-emoji>", "", text, flags=_re.I | _re.S)
    cleaned = _TAG_RE.sub("", cleaned)
    cleaned = _html.unescape(cleaned)
    return _re.sub(r"\s{2,}", " ", cleaned).strip()


def _json_safe(value, _depth: int = 0, _key: str = ""):
    """Make a payload JSON-serialisable.

    json.dumps(default=...) is NOT consulted for dict *keys*, so a payload
    that contains a tuple/other non-primitive key raised TypeError and the
    whole request became "Internal server error" (the Mini App then showed
    "Couldn't load match").  Live/completed match analytics carry such a dict
    (``analytics.phase_map`` is keyed by (innings, phase)), which is why every
    live match failed to open.  Keys are stringified here; values untouched.
    """
    if _depth > 40:
        return str(value)
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if isinstance(key, (str, int, float, bool)) or key is None:
                safe_key = key
            elif isinstance(key, (tuple, list)):
                safe_key = "|".join(str(part) for part in key)
            else:
                safe_key = str(key)
            out[safe_key] = _json_safe(item, _depth + 1, str(safe_key))
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item, _depth + 1, _key) for item in value]
    if isinstance(value, str) and "<" in value and _TEAM_TEXT_KEY.search(_key or ""):
        if _TAG_RE.search(value) or "<tg-emoji" in value.lower():
            return _plain_text(value) or value
    return value


async def _send_json(send, payload, status: int = 200):
    body = json.dumps(_json_safe(payload), ensure_ascii=False, separators=(",", ":"), default=_json_default).encode("utf-8")
    await send({
        "type": "http.response.start", "status": status,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


async def _send_bytes(send, data: bytes, content_type: str, status: int = 200, extra_headers=None, cache_control: bytes = b"private, max-age=300"):
    await send({
        "type": "http.response.start", "status": status,
        "headers": [
            (b"content-type", content_type.encode("latin-1", "replace")),
            (b"content-length", str(len(data)).encode("ascii")),
            (b"cache-control", cache_control),
            (b"x-content-type-options", b"nosniff"),
            *((extra_headers or [])),
        ],
    })
    await send({"type": "http.response.body", "body": data})


async def _send_static(send, path: str):
    root = STATIC_DIR.resolve()
    if path == "/":
        candidate = (root / "index.html").resolve()
    else:
        rel = path.removeprefix("/static/").lstrip("/")
        candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        await _send_json(send, {"detail": "Not found"}, 404)
        return
    if not candidate.is_file():
        await _send_json(send, {"detail": "Not found"}, 404)
        return
    data = candidate.read_bytes()
    mime, _ = mimetypes.guess_type(str(candidate))
    # Always revalidate the live Mini App shell/code so Telegram WebView does not retain a stale SDK integration after deployment.
    fresh_assets = {"index.html", "app_v2.js", "miniapp_v2.css", "match_centre.css", "kitbag_animation.css", "kitbag_animation.js", "packs_ui.css", "packs_ui.js", "game_ui.css", "game_ui.js", "achievements_ui.css", "achievements_ui.js", "giveaway_ui.css", "giveaway_ui.js"}
    cache_control = b"no-cache, no-store, must-revalidate" if path == "/" or candidate.name in fresh_assets else b"private, max-age=300"
    await _send_bytes(send, data, mime or "application/octet-stream", cache_control=cache_control)


def _rate_limited(key: str, limit: int) -> bool:
    now = time.monotonic()
    recent = [stamp for stamp in _REQUESTS.get(key, []) if now - stamp < _WINDOW_SECONDS]
    if len(recent) >= limit:
        _REQUESTS[key] = recent
        return True
    recent.append(now)
    _REQUESTS[key] = recent
    if len(_REQUESTS) > 5000:
        for old_key in list(_REQUESTS)[:1000]:
            if not any(now - stamp < _WINDOW_SECONDS for stamp in _REQUESTS.get(old_key, [])):
                _REQUESTS.pop(old_key, None)
    return False


class CrickiumMiniApp:
    async def _startup(self):
        await connect()
        await migrate()
        url = resolve_miniapp_url()
        if url:
            sync_miniapp_url(url)
            print(f"[miniapp_backend] synced MINIAPP_URL -> {url}")

    async def _shutdown(self):
        await disconnect()

    async def __call__(self, scope, receive, send):
        response_started = False

        async def guarded_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._dispatch(scope, receive, guarded_send)
        except Exception as exc:
            # Do not expose database/provider exceptions (or connection strings)
            # to Mini App clients. If headers are already sent, only log type.
            print(f"[miniapp_backend] request failed: {type(exc).__name__}")
            if scope.get("type") == "http" and not response_started:
                await _send_json(send, {"ok": False, "detail": "Internal server error"}, 500)

    async def _dispatch(self, scope, receive, send):
        if scope.get("type") == "lifespan":
            while True:
                event = await receive()
                if event.get("type") == "lifespan.startup":
                    try:
                        await self._startup()
                    except Exception as exc:
                        # Avoid echoing database/provider exception text, which may
                        # include hosts, usernames, or connection details in logs.
                        safe_message = f"Mini App startup failed: {type(exc).__name__}"
                        print(f"[miniapp_backend] {safe_message}")
                        await send({"type": "lifespan.startup.failed", "message": safe_message})
                        return
                    await send({"type": "lifespan.startup.complete"})
                elif event.get("type") == "lifespan.shutdown":
                    try:
                        await self._shutdown()
                    finally:
                        await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        if scope.get("type") != "http":
            return
        method = str(scope.get("method") or "GET").upper()
        path = str(scope.get("path") or "/")
        query = _query(scope)

        if method in {"GET", "HEAD"} and (path == "/" or path.startswith("/static/")):
            await _send_static(send, path)
            return
        if path == "/api/health" and method in {"GET", "HEAD"}:
            await _send_json(send, {"ok": True, "app": APP_NAME})
            return
        if path.startswith("/static/") and method in {"GET", "HEAD"}:
            await _send_static(send, path)
            return

        if method not in {"GET", "POST"}:
            await _send_json(send, {"ok": False, "detail": "Method not allowed"}, 405)
            return

        headers = _headers(scope)
        try:
            viewer = await resolve_viewer(headers, query)
        except MiniAppAuthError as exc:
            await _send_json(send, {"ok": False, "detail": str(exc)}, 401)
            return

        is_game = path == "/api/game" or path.startswith("/api/game/")
        if is_game:
            # A match is hundreds of small taps: it gets its own, higher budget.
            if _rate_limited(f"{viewer.id}:game", _GAME_LIMIT):
                await _send_json(send, {"ok": False, "detail": "Slow down a little"}, 429)
                return
            payload = {}
            if method == "POST":
                try:
                    body = await _body(receive)
                    payload = json.loads(body.decode("utf-8") or "{}")
                    if not isinstance(payload, dict):
                        raise ValueError("JSON body must be an object")
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    await _send_json(send, {"detail": str(exc)}, 400)
                    return
            from . import game_api
            status, data = await game_api.handle(viewer, method, path, query, payload)
            await _send_json(send, data, status)
            return

        limit = _WRITE_LIMIT if method == "POST" else _API_LIMIT
        if _rate_limited(f"{viewer.id}:{method}", limit):
            await _send_json(send, {"ok": False, "detail": "Rate limit exceeded"}, 429)
            return

        if method == "GET":
            if path == "/api/packs":
                await _send_json(send, await api.build_pack_shop_response(viewer))
                return
            if path == "/api/packs/openings":
                try:
                    history_limit = max(1, min(int(query.get("limit") or 10), 20))
                except ValueError:
                    history_limit = 10
                await _send_json(send, await api.build_pack_openings_response(viewer, history_limit))
                return
            if path.startswith("/api/packs/openings/"):
                opening_id = path.rsplit("/", 1)[-1]
                result = await api.build_pack_opening_detail(viewer, opening_id)
                if result is None:
                    await _send_json(send, {"ok": False, "detail": "Opening not found"}, 404)
                    return
                await _send_json(send, result)
                return
            if path == "/api/news":
                try:
                    news_limit = max(1, min(int(query.get("limit") or 30), 50))
                except ValueError:
                    news_limit = 30
                await _send_json(send, await api.build_news_list_response(viewer, news_limit))
                return
            if path.startswith("/api/news/"):
                try:
                    article_id = int(path.rsplit("/", 1)[-1])
                except ValueError:
                    article_id = 0
                article = await api.build_news_detail_response(viewer, article_id)
                if article is None:
                    await _send_json(send, {"detail": "News article not found"}, 404)
                else:
                    await _send_json(send, article)
                return
            if path == "/api/home" or path == "/api/profile":
                await _send_json(send, await api.build_home_response(viewer))
                return
            if path == "/api/players/search":
                q = (query.get("q") or "").strip().lstrip("@")[:100]
                try:
                    limit = int(query.get("limit") or 10)
                except ValueError:
                    limit = 10
                await _send_json(send, await api.build_player_search_response(viewer, q, limit))
                return
            if path == "/api/market":
                await _send_json(send, await api.build_market_response(viewer, query))
                return
            if path == "/api/player":
                kind = (query.get("kind") or "").lower()
                try:
                    entity_id = int(query.get("id") or 0)
                except ValueError:
                    entity_id = 0
                if not kind and query.get("name"):
                    found = await api.build_player_search_response(viewer, query["name"][:100], limit=20)
                    matches = [p for p in found["results"] if p["name"].casefold() == query["name"].strip().casefold()]
                    if len(matches) != 1:
                        await _send_json(send, {"detail": "Player not found or name is ambiguous"}, 404)
                        return
                    kind, entity_id = matches[0]["kind"], matches[0]["entity_id"]
                result = await api.build_player_detail_response(viewer, kind=kind, entity_id=entity_id)
                if not result:
                    await _send_json(send, {"detail": "Player not found"}, 404)
                    return
                await _send_json(send, result)
                return
            if path == "/api/squad" or path == "/api/collection":
                await _send_json(send, await api.build_squad_response(viewer))
                return
            if path == "/api/matches":
                await _send_json(send, await api.build_matches_response(viewer))
                return
            if path.startswith("/api/matches/shared/"):
                parts = path.strip("/").split("/")
                if len(parts) == 5 and parts[2] == "shared":
                    try:
                        shared_match_id = int(parts[4])
                    except ValueError:
                        await _send_json(send, {"detail": "Invalid match id"}, 400)
                        return
                    result = await api.build_match_detail(viewer, parts[3], shared_match_id, allow_shared=True)
                    if result is None:
                        await _send_json(send, {"detail": "Shared match score is not available"}, 404)
                        return
                    await _send_json(send, result)
                    return
            if path.startswith("/api/matches/"):
                parts = path.strip("/").split("/")
                if len(parts) == 4:
                    try:
                        match_id = int(parts[3])
                    except ValueError:
                        await _send_json(send, {"detail": "Invalid match id"}, 400)
                        return
                    result = await api.build_match_detail(viewer, parts[2], match_id)
                    if result is None:
                        await _send_json(send, {"detail": "Match details are not available"}, 404)
                        return
                    await _send_json(send, result)
                    return
            if path == "/api/leaderboard":
                try:
                    limit = int(query.get("limit") or 50)
                except ValueError:
                    limit = 50
                await _send_json(send, await api.build_leaderboard_response(viewer, limit))
                return
            if path == "/api/quests":
                await _send_json(send, await api.build_quests_response(viewer))
                return
            if path == "/api/giveaways":
                from . import giveaway_api
                await _send_json(send, await giveaway_api.build_list(viewer))
                return
            if path == "/api/achievements":
                from services import achievements
                await _send_json(send, await achievements.build_response(int(viewer.id), viewer))
                return
            if path.startswith("/api/ads/attempt/"):
                attempt_id = path.removeprefix("/api/ads/attempt/")
                result = await api.get_adsgram_ad_attempt(viewer, attempt_id)
                await _send_json(send, result, 404 if result.get("status") == "not_found" else 200)
                return
            if path == "/api/rewards":
                await _send_json(send, await api.build_rewards_response(viewer))
                return
            if path == "/api/wallet/transactions":
                await _send_json(send, await api.build_transaction_history(viewer))
                return
            if path == "/api/image/news":
                try:
                    article_id = int(query.get("id") or 0)
                except ValueError:
                    article_id = 0
                image = await api.news_image_bytes(article_id) if article_id > 0 else None
                if not image:
                    await _send_json(send, {"detail": "News image not available"}, 404)
                    return
                await _send_bytes(send, image[0], image[1])
                return
            if path == "/api/image/profile":
                image = await api.profile_image_bytes(viewer.id)
                if not image:
                    await _send_json(send, {"detail": "No profile image available"}, 404)
                    return
                await _send_bytes(send, image[0], image[1])
                return
            if path == "/api/image/player":
                kind = (query.get("kind") or "").lower()
                try:
                    entity_id = int(query.get("id") or 0)
                except ValueError:
                    entity_id = 0
                if kind not in {"global", "special", "showcase"} or entity_id <= 0:
                    await _send_json(send, {"detail": "Invalid player image request"}, 400)
                    return
                image = await api.player_image_bytes(kind, entity_id)
                if not image:
                    await _send_json(send, {"detail": "No player image available"}, 404)
                    return
                image_kind = image[2] if len(image) > 2 else "custom"
                await _send_bytes(send, image[0], image[1], extra_headers=[(b"x-crickium-card-type", str(image_kind).encode("ascii", "ignore"))])
                return
            await _send_json(send, {"detail": "Not found"}, 404)
            return

        try:
            body = await _body(receive)
            payload = json.loads(body.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            await _send_json(send, {"detail": str(exc)}, 400)
            return

        if path == "/api/ads/attempt":
            result = await api.create_adsgram_ad_attempt(viewer)
            status = 200 if result.get("status") == "success" else 409 if result.get("status") in {"daily_limit", "cooldown", "ad_pending", "replacement_pending"} else 404 if result.get("status") == "user_missing" else 400
            await _send_json(send, result, status)
            return
        if path == "/api/ads/attempt/complete":
            attempt_id = str(payload.get("attempt_id") or "")
            result = await api.complete_adsgram_ad_attempt(viewer, attempt_id)
            status = 200 if result.get("status") in {"rewarded", "no_player", "expired", "daily_limit", "replacement_pending", "failed"} else 409 if result.get("status") == "cooldown" else 404 if result.get("status") == "not_found" else 400
            await _send_json(send, result, status)
            return
        if path == "/api/ads/attempt/finish":
            attempt_id = str(payload.get("attempt_id") or "")
            result = await api.finish_adsgram_ad_attempt(viewer, attempt_id)
            status = 200 if result.get("status") in {"failed", "rewarded", "expired", "no_player", "daily_limit"} else 404 if result.get("status") == "not_found" else 400
            await _send_json(send, result, status)
            return
        if path == "/api/packs/purchase-request":
            from services.pack_system import create_purchase_request
            pack_key = str(payload.get("pack_key") or "").strip().lower()
            request_key = str(payload.get("client_request_id") or "").strip()[:80]
            if not pack_key:
                await _send_json(send, {"status": "invalid", "detail": "Choose a valid pack."}, 400)
                return
            await api._ensure_user(viewer)
            request = await create_purchase_request(int(viewer.id), pack_key, request_key or None)
            if not request:
                await _send_json(send, {"status": "invalid", "detail": "This pack is unavailable."}, 400)
                return
            await _send_json(send, request, 200)
            return
        if path == "/api/packs/purchase-confirm":
            from services.pack_system import confirm_purchase
            request_id = str(payload.get("request_id") or "")
            if not request_id:
                await _send_json(send, {"status": "invalid", "detail": "Purchase request is required."}, 400)
                return
            result = await confirm_purchase(int(viewer.id), request_id)
            status = 200 if result.get("status") == "success" else 409 if result.get("status") in {"insufficient", "cancelled", "failed", "pending", "invalid"} else 403 if result.get("status") == "owner" else 404 if result.get("status") == "missing" else 500
            if status != 200:
                result["detail"] = {
                    "insufficient": "You do not have enough Sigils for this pack.",
                    "cancelled": "This purchase was cancelled.",
                    "failed": "This purchase could not be completed.",
                    "pending": "This purchase is still pending.",
                    "invalid": "Invalid pack purchase request.",
                    "owner": "This purchase request does not belong to your account.",
                    "missing": "Purchase request not found.",
                }.get(result.get("status"), "Purchase failed.")
            await _send_json(send, result, status)
            return
        if path == "/api/packs/purchase-cancel":
            from services.pack_system import cancel_purchase
            request_id = str(payload.get("request_id") or "")
            result = await cancel_purchase(int(viewer.id), request_id)
            status = 200 if result.get("status") == "cancelled" else 403 if result.get("status") == "owner" else 404 if result.get("status") == "missing" else 409
            if status != 200:
                result["detail"] = "This purchase cannot be cancelled in its current state."
            await _send_json(send, result, status)
            return
        if path == "/api/packs/open":
            from services.pack_system import open_pack
            pack_key = str(payload.get("pack_key") or "").strip().lower()
            request_key = str(payload.get("client_request_id") or "").strip()[:80]
            if not pack_key:
                await _send_json(send, {"status": "invalid", "detail": "Choose a valid pack."}, 400)
                return
            await api._ensure_user(viewer)
            result = await open_pack(int(viewer.id), pack_key, request_key or None)
            if result.get("status") != "success":
                status = 409 if result.get("status") in {"empty", "no_player"} else 400 if result.get("status") == "invalid" else 500
                result["detail"] = {
                    "empty": "You do not have an unopened copy of this pack.",
                    "no_player": "No eligible player pool is available for this pack. No pack was consumed.",
                    "invalid": "This pack is unavailable.",
                }.get(result.get("status"), "The pack could not be opened.")
                await _send_json(send, result, status)
                return
            opening = await api.build_pack_opening_detail(viewer, str(result.get("opening_id") or ""))
            if opening is None:
                await _send_json(send, {"status": "failed", "detail": "The opening was stored but its result could not be loaded."}, 500)
                return
            opening["status"] = "success"
            opening["replayed"] = bool(result.get("replayed"))
            await _send_json(send, opening)
            return
        if path == "/api/squad/lineup/order":
            raw_ids = payload.get("player_ids")
            if not isinstance(raw_ids, list):
                await _send_json(send, {"status": "invalid_order", "detail": "Provide the ordered Playing XI player IDs."}, 400)
                return
            result = await api.update_playing_xi_order(viewer, raw_ids)
            status = 200 if result.get("status") == "success" else 409 if result.get("status") in {"invalid_order", "player_not_owned", "lineup_changed"} else 404 if result.get("status") == "user_missing" else 400
            await _send_json(send, result, status)
            return
        if path == "/api/squad/sell-multiple":
            raw_ids = payload.get("player_ids")
            if not isinstance(raw_ids, list):
                await _send_json(send, {"status": "invalid_selection", "detail": "Select one or more substitute players."}, 400)
                return
            result = await api.sell_squad_players(viewer, raw_ids)
            status = 200 if result.get("status") == "success" else 409 if result.get("status") in {"invalid_selection", "player_not_owned", "playing_xi_player_selected", "minimum_squad_size"} else 404 if result.get("status") == "user_missing" else 400
            await _send_json(send, result, status)
            return
        if path == "/api/squad/lineup/swap":
            try:
                playing_player_id = int(payload.get("playing_player_id") or 0)
                substitute_player_id = int(payload.get("substitute_player_id") or 0)
            except (TypeError, ValueError):
                await _send_json(send, {"status": "invalid_selection", "detail": "Choose two valid players."}, 400)
                return
            result = await api.swap_playing_xi_players(viewer, playing_player_id, substitute_player_id)
            status = 200 if result.get("status") == "success" else 409 if result.get("status") in {"player_not_owned", "squad_too_small", "player_not_in_xi", "player_already_in_xi"} else 404 if result.get("status") == "user_missing" else 400
            await _send_json(send, result, status)
            return
        if path == "/api/squad/sell":
            try:
                player_id = int(payload.get("player_id") or 0)
            except (TypeError, ValueError):
                player_id = 0
            result = await api.sell_squad_player(viewer, player_id)
            status = 200 if result.get("status") == "success" else 409 if result.get("status") in {"player_not_owned", "minimum_squad_size"} else 404 if result.get("status") == "user_missing" else 400
            await _send_json(send, result, status)
            return
        if path == "/api/collection/sell-pack-card":
            try:
                card_id = int(payload.get("card_id") or 0)
            except (TypeError, ValueError):
                card_id = 0
            result = await api.sell_pack_collection_card(viewer, card_id)
            status = 200 if result.get("status") == "success" else 409 if result.get("status") in {"card_not_owned", "card_data_unavailable"} else 404 if result.get("status") in {"user_missing"} else 400
            await _send_json(send, result, status)
            return
        if path == "/api/rewards/kitbag/replace":
            claim_id = str(payload.get("claim_id") or "")
            try:
                replaced_player_id = int(payload.get("replaced_player_id") or 0)
            except (TypeError, ValueError):
                replaced_player_id = 0
            result = await api.replace_kitbag_player(viewer, claim_id, replaced_player_id)
            status = 200 if result.get("status") == "success" else 409 if result.get("status") in {"claim_already_resolved", "replacement_not_owned", "reward_already_owned", "invalid_replacement"} else 404 if result.get("status") in {"claim_not_found", "player_not_found", "user_missing"} else 400
            await _send_json(send, result, status)
            return
        if path == "/api/market/purchase":
            kind = str(payload.get("kind") or "").lower()
            try:
                entity_id = int(payload.get("id") or 0)
            except (ValueError, TypeError):
                entity_id = 0
            if kind not in {"global", "special", "showcase"} or entity_id <= 0:
                await _send_json(send, {"detail": "Invalid player selection"}, 400)
                return
            result = await api.purchase_market_player(viewer, kind, entity_id)
            status = 200 if result.get("status") == "success" else 409 if result.get("status") in {"already_owned", "squad_full", "insufficient_balance"} else 404
            await _send_json(send, result, status)
            return
        if path == "/api/rewards/daily":
            result = await api.claim_daily_reward(viewer)
            status = 200 if result.get("status") == "success" else 409 if result.get("status") in {"cooldown", "squad_full", "no_player"} else 403 if result.get("status") == "debut_required" else 500
            await _send_json(send, result, status)
            return
        if path == "/api/giveaways/join":
            from . import giveaway_api
            try:
                gid = int(payload.get("id") or 0)
            except (TypeError, ValueError):
                gid = 0
            if gid <= 0:
                await _send_json(send, {"ok": False, "detail": "Invalid giveaway"}, 400)
                return
            status, data = await giveaway_api.join(viewer, gid)
            await _send_json(send, data, status)
            return
        if path == "/api/rewards/kitbag":
            result = await api.claim_kitbag(viewer)
            status = 200 if result.get("status") == "success" else 409 if result.get("status") in {"cooldown", "squad_full", "no_player", "replacement_pending"} else 404 if result.get("status") == "user_missing" else 500
            await _send_json(send, result, status)
            return
        await _send_json(send, {"detail": "Not found"}, 404)


app = CrickiumMiniApp()
