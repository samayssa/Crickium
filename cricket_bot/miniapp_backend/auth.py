from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qsl

from .config import BOT_TOKEN, DEBUG


class MiniAppAuthError(Exception):
    """Raised when Telegram init data is missing or invalid."""


@dataclass(frozen=True)
class TelegramViewer:
    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


def _verify_init_data(init_data: str) -> dict[str, Any]:
    params = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received_hash = params.pop("hash", None)
    if not received_hash:
        raise ValueError("Missing hash")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise ValueError("Bad hash")

    return params


def _parse_user_blob(params: dict[str, Any]) -> TelegramViewer:
    user_blob = params.get("user")
    if not user_blob:
        raise ValueError("Missing user blob")

    user = json.loads(user_blob)
    return TelegramViewer(
        id=int(user.get("id")),
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
        username=user.get("username"),
        language_code=user.get("language_code"),
    )


def _get_value(mapping: Mapping[str, str], key: str) -> str:
    if not mapping:
        return ""
    # header maps are usually lower-case; query params may preserve case.
    return mapping.get(key) or mapping.get(key.lower()) or mapping.get(key.upper()) or ""


async def resolve_viewer(headers: Mapping[str, str], query_params: Mapping[str, str]) -> TelegramViewer:
    init_data = _get_value(headers, "X-Telegram-Init-Data") or _get_value(query_params, "initData")
    if init_data:
        try:
            params = _verify_init_data(init_data)
            return _parse_user_blob(params)
        except Exception as exc:
            raise MiniAppAuthError(f"Invalid Telegram init data: {exc}") from exc

    if DEBUG:
        return TelegramViewer(
            id=int(_get_value(query_params, "debug_user_id") or 987654321),
            first_name=_get_value(query_params, "debug_first_name") or "Arpit",
            last_name=_get_value(query_params, "debug_last_name") or "Tyagi",
            username=_get_value(query_params, "debug_username") or None,
            language_code="en",
        )

    raise MiniAppAuthError("Missing Telegram init data")
