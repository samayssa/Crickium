import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Railway supplies environment variables directly; python-dotenv is only
    # needed for local .env convenience.
    pass

# ==========================
# Telegram Configuration
# ==========================
# Credentials are supplied by the host environment. Never commit bot tokens,
# API hashes, or database passwords to the repository.
API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# ==========================
# Admin Configuration
# ==========================
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "1766243373") or 0)

# ==========================
# PostgreSQL Configuration
# ==========================
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
# Provider-neutral PostgreSQL TLS settings. These let the database provider and
# its CA certificate be changed through Railway variables without code edits.
DB_SSL_MODE = os.getenv("DB_SSL_MODE", "").strip().lower()
DB_CA_CERT_FILE = os.getenv("DB_CA_CERT_FILE", "").strip()
DB_CA_CERT = os.getenv("DB_CA_CERT", "")
DB_CA_CERT_B64 = os.getenv("DB_CA_CERT_B64", "").strip()
COCKROACH_CA_CERT_URL = os.getenv(
    "COCKROACH_CA_CERT_URL",
    "https://cockroachlabs.cloud/clusters/d1cd6a3a-481f-4de7-b833-b5d630463b21/cert",
).strip()
COCKROACH_CA_CERT = os.getenv("COCKROACH_CA_CERT", "")
COCKROACH_CA_CERT_B64 = os.getenv("COCKROACH_CA_CERT_B64", "")
COCKROACH_CA_CERT_FILE = os.getenv("COCKROACH_CA_CERT_FILE", "")
DB_DEBUG = os.getenv("DB_DEBUG", "0").strip().lower() in {"1", "true", "yes"}

# ==========================
# Bot / Mini App Configuration
# ==========================
BOT_NAME = os.getenv("BOT_NAME", "Crickium").strip() or "Crickium"
# Fail closed by default. Local Mini App auth bypass is separately controlled
# by MINIAPP_DEBUG_AUTH and should never be enabled on a public deployment.
DEBUG = os.getenv("DEBUG", "1").strip().lower() in {"1", "true", "yes"}
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://crickium-production.up.railway.app").strip()
BACKEND_URL = os.getenv("BACKEND_URL", "").strip()

# ==========================
# Player Card Images
# ==========================
PLAYER_IMAGE_CHANNEL_ID = int(os.getenv("PLAYER_IMAGE_CHANNEL_ID", "-1003958908828") or 0)
AUTO_BACKUP_ENABLED = os.getenv("AUTO_BACKUP_ENABLED", "1").strip() != "0"
AUTO_BACKUP_CHANNEL_ID = int(os.getenv("AUTO_BACKUP_CHANNEL_ID", "") or PLAYER_IMAGE_CHANNEL_ID)
AUTO_BACKUP_INTERVAL_SECONDS = int(os.getenv("AUTO_BACKUP_INTERVAL_SECONDS", "") or 86400)
NOTIFICATION_GROUP_ID = int(os.getenv("NOTIFICATION_GROUP_ID", "-1003588964307") or 0)

# ==========================
# Giveaway Configuration
# ==========================
MAIN_GROUP_ID = int(os.getenv("MAIN_GROUP_ID", "-1002374007744") or 0)
MAIN_GROUP_LINK = os.getenv("MAIN_GROUP_LINK", "").strip()
