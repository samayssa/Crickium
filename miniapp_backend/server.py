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


async def _send_json(send, payload, status: int = 200):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_json_default).encode("utf-8")
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


async def _send_bytes(send, data: bytes, content_type: str, status: int = 200):
    await send({
        "type": "http.response.start", "status": status,
        "headers": [
            (b"content-type", content_type.encode("latin-1", "replace")),
            (b"content-length", str(len(data)).encode("ascii")),
            (b"cache-control", b"private, max-age=300"),
            (b"x-content-type-options", b"nosniff"),
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
    await _send_bytes(send, data, mime or "application/octet-stream")


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
                        await send({"type": "lifespan.startup.failed", "message": str(exc)})
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

        limit = _WRITE_LIMIT if method == "POST" else _API_LIMIT
        if _rate_limited(f"{viewer.id}:{method}", limit):
            await _send_json(send, {"ok": False, "detail": "Rate limit exceeded"}, 429)
            return

        if method == "GET":
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
            if path == "/api/quests" or path == "/api/achievements":
                await _send_json(send, await api.build_quests_response(viewer))
                return
            if path == "/api/rewards":
                await _send_json(send, await api.build_rewards_response(viewer))
                return
            if path == "/api/wallet/transactions":
                await _send_json(send, await api.build_transaction_history(viewer))
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
                await _send_bytes(send, image[0], image[1])
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
        if path == "/api/rewards/kitbag":
            result = await api.claim_kitbag(viewer)
            status = 200 if result.get("status") == "success" else 409 if result.get("status") in {"cooldown", "squad_full", "no_player"} else 500
            await _send_json(send, result, status)
            return
        await _send_json(send, {"detail": "Not found"}, 404)


app = CrickiumMiniApp()
