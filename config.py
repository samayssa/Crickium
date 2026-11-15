import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
                                                                            
                                        
    pass

                            
                        
                            
                                                                            
                                                      
API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

                            
                     
                            
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "1766243373") or 0)

                            
                          
                            
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
                                                                               
                                                                             
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

                            
                              
                            
BOT_NAME = os.getenv("BOT_NAME", "Crickium").strip() or "Crickium"
                                                                             
                                                                           
DEBUG = os.getenv("DEBUG", "1").strip().lower() in {"1", "true", "yes"}
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://crickium-production.up.railway.app").strip()
BACKEND_URL = os.getenv("BACKEND_URL", "").strip()

                            
                    
                            
PLAYER_IMAGE_CHANNEL_ID = int(os.getenv("PLAYER_IMAGE_CHANNEL_ID", "-1003958908828") or 0)
AUTO_BACKUP_ENABLED = os.getenv("AUTO_BACKUP_ENABLED", "1").strip() != "0"
AUTO_BACKUP_CHANNEL_ID = int(os.getenv("AUTO_BACKUP_CHANNEL_ID", "") or PLAYER_IMAGE_CHANNEL_ID)
AUTO_BACKUP_INTERVAL_SECONDS = int(os.getenv("AUTO_BACKUP_INTERVAL_SECONDS", "") or 86400)
NOTIFICATION_GROUP_ID = int(os.getenv("NOTIFICATION_GROUP_ID", "-1003588964307") or 0)

                            
                        
                            
MAIN_GROUP_ID = int(os.getenv("MAIN_GROUP_ID", "-1002374007744") or 0)
MAIN_GROUP_LINK = os.getenv("MAIN_GROUP_LINK", "").strip()


# ---------------------------------------------------------------------------
# Redis (Upstash) cache layer — see docs/REDIS_CACHE.md
# Put the Upstash "Redis URL" (rediss://default:PASSWORD@HOST:6379) in the
# Railway variable REDIS_URL.  Without it the bot still runs (memory cache only).
# ---------------------------------------------------------------------------
def _env_first(*names: str) -> str:
    for _name in names:
        _value = os.getenv(_name, "").strip().strip('"').strip("'")
        if _value:
            return _value
    return ""


def _env_flag(name: str, default: bool) -> bool:
    _raw = os.getenv(name)
    if _raw is None or not _raw.strip():
        return default
    return _raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_number(name: str, default, cast=float):
    try:
        return cast(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


REDIS_URL = _env_first("REDIS_URL", "DATABASE_REDIS_URL", "UPSTASH_REDIS_URL", "UPSTASH_REDIS_TCP_URL")
CACHE_ENABLED = _env_flag("CACHE_ENABLED", True)
CACHE_NAMESPACE = os.getenv("CACHE_NAMESPACE", "").strip()
CACHE_SIGNING_KEY = os.getenv("CACHE_SIGNING_KEY", "").strip()
REDIS_TIMEOUT_SECONDS = _env_number("REDIS_TIMEOUT_SECONDS", 1.5)
REDIS_CONNECT_TIMEOUT_SECONDS = _env_number("REDIS_CONNECT_TIMEOUT_SECONDS", 2.0)
REDIS_POOL_SIZE = _env_number("REDIS_POOL_SIZE", 4, int)
REDIS_TLS_VERIFY = _env_flag("REDIS_TLS_VERIFY", True)
REDIS_SLOW_MS = _env_number("REDIS_SLOW_MS", 450.0)
CACHE_L1_MAX_ENTRIES = _env_number("CACHE_L1_MAX_ENTRIES", 3000, int)
CACHE_L1_MAX_MB = _env_number("CACHE_L1_MAX_MB", 48, int)
CACHE_GEN_REFRESH_SECONDS = _env_number("CACHE_GEN_REFRESH_SECONDS", 90.0)
CACHE_REDIS_DAILY_COMMAND_LIMIT = _env_number("CACHE_REDIS_DAILY_COMMAND_LIMIT", 0, int)
CACHE_SNAPSHOT_REDIS = _env_flag("CACHE_SNAPSHOT_REDIS", True)   # live-match snapshot hand-off bot -> Mini App via Redis
