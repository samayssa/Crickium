from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

MINIAPP_URL_FILES = (
    Path(os.getenv("MINIAPP_URL_FILE", PROJECT_ROOT / "miniapp_url.txt")),
    PROJECT_ROOT / "miniapp_backend" / "runtime_url.txt",
    PROJECT_ROOT / "miniapp_backend" / "public_url.txt",
    PROJECT_ROOT / "runtime_url.txt",
)

PLACEHOLDER_MARKERS = (
    "",
    "https://your-miniapp-domain.example.com/",
    "https://your-miniapp-domain.example.com/api",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://0.0.0.0:8000",
)


def _clean_url(url: str | None) -> str:
    if not url:
        return ""
    value = str(url).strip().strip('"').strip("'")
    while value.endswith("/"):
        value = value[:-1]
    return value


def is_valid_webapp_url(url: str | None) -> bool:
    value = _clean_url(url)
    return value.startswith("https://")


def _read_file(path: Path) -> str:
    try:
        if path.exists():
            return _clean_url(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return ""


def resolve_miniapp_url() -> str:
    """Resolve the latest Mini App URL from the newest available source.

    Priority:
    1) MINIAPP_URL environment variable
    2) runtime URL files written by a tunnel/bootstrap script
    3) fallback MINIAPP_URL value from config.py (if set)
    """
    env_url = _clean_url(os.getenv("MINIAPP_URL"))
    if is_valid_webapp_url(env_url):
        return env_url

    for path in MINIAPP_URL_FILES:
        value = _read_file(path)
        if is_valid_webapp_url(value):
            return value

    try:
        from config import MINIAPP_URL as CONFIG_MINIAPP_URL  # type: ignore
    except Exception:
        CONFIG_MINIAPP_URL = ""
    config_url = _clean_url(CONFIG_MINIAPP_URL)
    if is_valid_webapp_url(config_url):
        return config_url

    return ""


def sync_miniapp_url(url: str | None) -> str:
    """Persist the latest valid Mini App URL into local cache files."""
    value = _clean_url(url)
    if not is_valid_webapp_url(value):
        return ""

    for path in MINIAPP_URL_FILES:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        except Exception:
            continue
    return value


def get_launch_keyboard(url: str) -> dict | None:
    if not is_valid_webapp_url(url):
        return None
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🚀 Launch App",
                    "web_app": {"url": url},
                }
            ]
        ]
    }
