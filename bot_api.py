"""
Minimal, dependency-free Telegram Bot API client.
"""

from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.request

from config import BOT_TOKEN

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
SHORT_HTTP_TIMEOUT_SECONDS = 30
LONG_POLL_HTTP_TIMEOUT_SECONDS = 60


def _build_request(method: str, params: dict | None = None) -> urllib.request.Request:
    url = f"{BASE_URL}/{method}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if params is None:
        return urllib.request.Request(url, headers=headers)
    data = json.dumps(params).encode("utf-8")
    return urllib.request.Request(url, data=data, headers=headers)


def _call_sync(method: str, params: dict | None = None, timeout_seconds: int = SHORT_HTTP_TIMEOUT_SECONDS) -> dict:
    req = _build_request(method, params)
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    if not body.get("ok"):
        raise RuntimeError(f"[bot_api] Telegram API error on {method}: {body}")
    return body["result"]


# getUpdates is already retried by the outer long-poll loop in main.py, so we
# don't want to retry it here too (that would stack two retry loops on top of
# each other). Everything else (sendMessage, editMessageText, etc.) is a quick
# call that's worth retrying a couple of times on flaky mobile connections
# before we give up and let the caller see an error.
RETRYABLE_NETWORK_ERRORS = (TimeoutError, socket.timeout, urllib.error.URLError, ConnectionResetError)
MAX_CALL_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 0.75


async def call(method: str, params: dict | None = None) -> dict:
    """Async wrapper - runs the blocking HTTP call in a thread, retrying
    transient network errors with exponential backoff."""
    timeout_seconds = SHORT_HTTP_TIMEOUT_SECONDS
    if method == "getUpdates":
        poll_timeout = 25
        if params and isinstance(params, dict):
            poll_timeout = int(params.get("timeout", 25))
        timeout_seconds = max(LONG_POLL_HTTP_TIMEOUT_SECONDS, poll_timeout + 10)

    max_attempts = 1 if method == "getUpdates" else MAX_CALL_ATTEMPTS
    delay = RETRY_BASE_DELAY_SECONDS

    for attempt in range(1, max_attempts + 1):
        try:
            return await asyncio.to_thread(_call_sync, method, params, timeout_seconds)
        except urllib.error.HTTPError as e:
            # Telegram sometimes returns 400 for stale callback queries or already-answered callbacks.
            # For callback answers we do not want the whole match flow to die.
            if method == "answerCallbackQuery":
                print(f"[bot_api] Ignoring non-fatal HTTPError from answerCallbackQuery: {e!r}")
                return {}
            raise RuntimeError(f"[bot_api] HTTP error while calling {method}: {e!r}") from e
        except RETRYABLE_NETWORK_ERRORS as e:
            if attempt < max_attempts:
                print(f"[bot_api] Transient network error on {method} (attempt {attempt}/{max_attempts}): {e!r}. Retrying in {delay:.2f}s...")
                await asyncio.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"[bot_api] Network timeout while calling {method}: {e!r}") from e


async def get_me() -> dict:
    return await call("getMe")


async def get_updates(offset: int | None = None, timeout: int = 25, allowed_updates: list[str] | None = None) -> list:
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    if allowed_updates is not None:
        params["allowed_updates"] = allowed_updates
    return await call("getUpdates", params)


async def send_message(chat_id: int, text: str, parse_mode: str | None = None, reply_markup: dict | None = None) -> dict:
    params = {"chat_id": chat_id, "text": text}
    if parse_mode:
        params["parse_mode"] = parse_mode
    if reply_markup:
        params["reply_markup"] = reply_markup
    return await call("sendMessage", params)


async def edit_message_text(chat_id: int, message_id: int, text: str, parse_mode: str | None = None, reply_markup: dict | None = None) -> dict:
    params = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        params["parse_mode"] = parse_mode
    if reply_markup:
        params["reply_markup"] = reply_markup
    return await call("editMessageText", params)


async def delete_message(chat_id: int, message_id: int) -> dict:
    return await call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})


async def answer_callback_query(callback_query_id: str, text: str | None = None, show_alert: bool = False) -> dict:
    params = {"callback_query_id": callback_query_id}
    if text:
        params["text"] = text
        params["show_alert"] = show_alert
    try:
        return await call("answerCallbackQuery", params)
    except Exception as exc:
        print(f"[bot_api] Non-fatal answer_callback_query error ignored: {exc!r}")
        return {}


async def delete_webhook() -> dict:
    return await call("deleteWebhook")


async def get_webhook_info() -> dict:
    return await call("getWebhookInfo")
