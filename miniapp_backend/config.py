from __future__ import annotations

from pathlib import Path
import os

from config import DATABASE_URL as ROOT_DATABASE_URL, BOT_TOKEN as ROOT_BOT_TOKEN, DEBUG as ROOT_DEBUG, MINIAPP_URL as ROOT_MINIAPP_URL

APP_NAME = "Crickium Mini App"
APP_ROOT = Path(__file__).resolve().parent
STATIC_DIR = APP_ROOT / "static"

DATABASE_URL = ROOT_DATABASE_URL
BOT_TOKEN = ROOT_BOT_TOKEN
DEBUG = ROOT_DEBUG
MINIAPP_URL = ROOT_MINIAPP_URL

TELEGRAM_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
TELEGRAM_FILE_BASE = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

API_ORIGIN = os.getenv("API_ORIGIN", "")
