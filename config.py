import os

# ==========================
# Telegram Configuration
# ==========================

API_ID = 20394793
API_HASH = "20c8b7c13a300c32b9f3cbee183674d1"
BOT_TOKEN = "8964396486:AAHMaqBD2FfGPrNaLOf4evaimWRDWmVKDNQ"

# ==========================
# Admin Configuration
# ==========================

# Bot admin (NOT group admin) - only this Telegram user_id can use
# admin-only commands like /upload_pl
ADMIN_USER_ID = 1766243373

# ==========================
# PostgreSQL Configuration
# ==========================

# Database configuration
# Intentionally self-contained for Railway redeploys. No Railway database
# environment variable is required.
DATABASE_URL = (
    "postgresql://arpit:3M9a5QM_sowTbNhEiGGDjw@"
    "tide-walker-21592.jxf.gcp-asia-south1.cockroachlabs.cloud:26257/"
    "defaultdb?sslmode=verify-full"
).strip()

# CockroachDB Cloud cluster CA endpoint. The database connection layer checks
# the DATABASE_URL first; this certificate is fetched only when that URL is
# actually a CockroachDB Cloud URL. Switching DATABASE_URL to another
# PostgreSQL provider therefore bypasses Cockroach certificate handling.
COCKROACH_CA_CERT_URL = (
    "https://cockroachlabs.cloud/clusters/"
    "d1cd6a3a-481f-4de7-b833-b5d630463b21/cert"
)

# Optional compatibility hooks. Leave these empty when using the built-in
# Cockroach certificate URL above. They are consulted only for CockroachDB.
COCKROACH_CA_CERT = ""
COCKROACH_CA_CERT_B64 = ""
COCKROACH_CA_CERT_FILE = ""

# Verbose SQL logging is off by default for lower latency and cleaner Railway logs.
DB_DEBUG = False

# ==========================
# Bot / Mini App Configuration
# ==========================

BOT_NAME = "Crickium"
DEBUG = True

# Public HTTPS URL of the Telegram Mini App.
# Set this to your deployed frontend URL before going live.
MINIAPP_URL = os.getenv("MINIAPP_URL", "").strip()

# Optional backend URL if you host the API separately from the static app.
BACKEND_URL = os.getenv("BACKEND_URL", "").strip()

# ==========================
# Player Card Images
# ==========================

# Channel where /upload_img uploads player card photos (and the default
# card template). The bot must be an admin of this channel.
PLAYER_IMAGE_CHANNEL_ID = -1003958908828

# Group where bot-level user/group notifications are sent.
NOTIFICATION_GROUP_ID = -1003588964307