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

DATABASE_URL = (
    "postgresql://neondb_owner:npg_2nWk1zLQtjau@ep-bitter-hall-a4ihpydn-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
)

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