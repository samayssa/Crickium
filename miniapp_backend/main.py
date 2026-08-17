from __future__ import annotations

"""Crickium Mini App backend entrypoint.

This backend runs as a small ASGI app so it works cleanly on Android/Termux
without depending on FastAPI's OpenAPI/Pydantic integration path.
"""

from pathlib import Path
import json
import mimetypes
import sys
from urllib.parse import parse_qsl

if __package__ in (None, ""):
    PACKAGE_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = PACKAGE_DIR.parent
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

from miniapp_backend.auth import MiniAppAuthError, resolve_viewer
from miniapp_backend.config import APP_NAME, STATIC_DIR
from miniapp_backend.data import (
    build_home_response,
    build_player_detail_response,
    build_player_search_response,
)
from miniapp_backend.db import connect, disconnect
from miniapp_backend.migrate import migrate
from utils.miniapp_url import resolve_miniapp_url, sync_miniapp_url


def _jsonable(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if hasattr(value, "dict"):
        return _jsonable(value.dict())
    if hasattr(value, "__dict__"):
        return _jsonable({k: v for k, v in value.__dict__.items() if not k.startswith("_")})
    return str(value)


async def _read_body(receive) -> bytes:
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] == "http.request":
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        else:
            break
    return bytes(body)


def _make_headers(content_type: str = "application/json; charset=utf-8"):
    return [(b"content-type", content_type.encode("utf-8"))]


async def _send_json(send, payload, status: int = 200):
    data = json.dumps(_jsonable(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(data)).encode("utf-8")),
        (b"cache-control", b"no-store"),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": data})


async def _send_text(send, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8"):
    data = text.encode("utf-8")
    headers = [
        (b"content-type", content_type.encode("utf-8")),
        (b"content-length", str(len(data)).encode("utf-8")),
        (b"cache-control", b"no-store"),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": data})


async def _send_file(send, file_path: Path):
    data = file_path.read_bytes()
    content_type, _ = mimetypes.guess_type(str(file_path))
    headers = [
        (b"content-type", (content_type or "application/octet-stream").encode("utf-8")),
        (b"content-length", str(len(data)).encode("utf-8")),
        (b"cache-control", b"no-store"),
    ]
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": data})


def _headers_from_scope(scope) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers", []):
        try:
            name = raw_name.decode("latin-1").lower()
            value = raw_value.decode("latin-1")
        except Exception:
            continue
        headers[name] = value
    return headers


def _query_from_scope(scope) -> dict[str, str]:
    query_string = scope.get("query_string", b"")
    try:
        text = query_string.decode("utf-8", errors="replace")
    except Exception:
        text = ""
    return dict(parse_qsl(text, keep_blank_values=True))


def _resolve_static_file(request_path: str) -> Path | None:
    if not STATIC_DIR.exists():
        return None
    static_root = STATIC_DIR.resolve()
    if request_path == "/":
        file_path = (static_root / "index.html").resolve()
    else:
        rel = request_path.removeprefix("/static/").lstrip("/")
        file_path = (static_root / rel).resolve()

    try:
        file_path.relative_to(static_root)
    except Exception:
        return None

    if file_path.is_file():
        return file_path
    return None


class CrickiumMiniApp:
    async def _startup(self) -> None:
        await connect()
        await migrate()
        resolved = resolve_miniapp_url()
        if resolved:
            sync_miniapp_url(resolved)
            print(f"[miniapp_backend] synced MINIAPP_URL -> {resolved}")

    async def _shutdown(self) -> None:
        await disconnect()

    async def __call__(self, scope, receive, send):
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            while True:
                message = await receive()
                message_type = message.get("type")
                if message_type == "lifespan.startup":
                    try:
                        await self._startup()
                    except Exception as exc:
                        await send({"type": "lifespan.startup.failed", "message": str(exc)})
                        return
                    await send({"type": "lifespan.startup.complete"})
                elif message_type == "lifespan.shutdown":
                    try:
                        await self._shutdown()
                    finally:
                        await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        if scope_type != "http":
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/") or "/"
        headers = _headers_from_scope(scope)
        query = _query_from_scope(scope)

        try:
            if path in {"/", "/api/home", "/api/players/search", "/api/player"}:
                viewer = await resolve_viewer(headers, query)
            else:
                viewer = None
        except MiniAppAuthError as exc:
            await _send_json(send, {"ok": False, "detail": str(exc)}, status=401)
            return

        if method not in {"GET", "HEAD"}:
            await _send_json(send, {"ok": False, "detail": "Method not allowed"}, status=405)
            return

        if path == "/api/health":
            await _send_json(send, {"ok": True, "app": APP_NAME})
            return

        if path == "/":
            index_file = STATIC_DIR / "index.html"
            if not index_file.exists():
                await _send_json(send, {"detail": "Mini app frontend not found"}, status=500)
                return
            await _send_file(send, index_file)
            return

        if path.startswith("/static/"):
            file_path = _resolve_static_file(path)
            if not file_path:
                await _send_json(send, {"detail": "Not found"}, status=404)
                return
            await _send_file(send, file_path)
            return

        if path == "/api/home":
            payload = await build_home_response(viewer)
            await _send_json(send, payload)
            return

        if path == "/api/players/search":
            query_text = (query.get("q") or "").strip().lstrip("@")
            try:
                limit = int(query.get("limit") or 10)
            except ValueError:
                limit = 10
            payload = await build_player_search_response(viewer, query_text, limit=limit)
            await _send_json(send, payload)
            return

        if path == "/api/player":
            player_name = (query.get("name") or "").strip()
            if not player_name:
                await _send_json(send, {"detail": "Missing player name"}, status=400)
                return
            try:
                payload = await build_player_detail_response(viewer, player_name)
            except ValueError as exc:
                await _send_json(send, {"detail": str(exc)}, status=404)
                return
            await _send_json(send, payload)
            return

        if path.startswith("/api/section/"):
            section_name = path.removeprefix("/api/section/").strip()
            section_label = section_name.replace("-", " ").title()
            await _send_json(
                send,
                {
                    "title": section_label,
                    "message": f"{section_label} is coming soon.",
                    "status": "coming_soon",
                },
            )
            return

        await _send_json(send, {"detail": "Not found"}, status=404)


app = CrickiumMiniApp()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
