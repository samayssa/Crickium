import base64
import os
import ssl
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

from config import (
    COCKROACH_CA_CERT,
    COCKROACH_CA_CERT_B64,
    COCKROACH_CA_CERT_FILE,
    COCKROACH_CA_CERT_URL,
    DATABASE_URL,
    DB_CA_CERT,
    DB_CA_CERT_B64,
    DB_CA_CERT_FILE,
    DB_SSL_MODE,
)

_pool = None
_DB_BLOCKED_UNTIL = 0.0
_DB_BLOCK_REASON = None
_DB_COOLDOWN_SECONDS = 30.0

_VALID_SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
_DEFAULT_SSLROOTCERT_VALUES = {"~/.postgresql/root.crt", "/root/.postgresql/root.crt"}


def _quota_error(exc: BaseException) -> bool:
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    return (
        "insufficientresourceserror" in name
        or "exceeded the quota" in text
        or "quota" in text and ("exceeded" in text or "limit" in text)
    )


def is_database_quota_blocked() -> bool:
    return time.monotonic() < _DB_BLOCKED_UNTIL


def database_block_reason() -> str | None:
    return _DB_BLOCK_REASON


def mark_database_quota_exhausted(exc: BaseException) -> None:
    global _DB_BLOCKED_UNTIL, _DB_BLOCK_REASON
    _DB_BLOCKED_UNTIL = max(_DB_BLOCKED_UNTIL, time.monotonic() + _DB_COOLDOWN_SECONDS)
    _DB_BLOCK_REASON = str(exc) or "Database provider quota is temporarily exhausted."
    print(
        f"[db/connection] Database quota circuit-breaker active for "
        f"{_DB_COOLDOWN_SECONDS:.0f}s: {_DB_BLOCK_REASON}"
    )


def clear_database_quota_block() -> None:
    global _DB_BLOCKED_UNTIL, _DB_BLOCK_REASON
    _DB_BLOCKED_UNTIL = 0.0
    _DB_BLOCK_REASON = None


def is_database_quota_error(exc: BaseException) -> bool:
    return _quota_error(exc)


def _is_cockroach_url(dsn: str) -> bool:
    try:
        host = urlsplit(dsn).hostname or ""
    except ValueError:
        return False
    return host.endswith(".cockroachlabs.cloud") or "cockroachlabs.cloud" in host


def _remove_query_keys(dsn: str, keys: set[str]) -> str:
    parts = urlsplit(dsn)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in keys]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _resolve_cert_file(raw_path: str, setting_name: str) -> Path:
    """Resolve absolute or repository-relative CA certificate paths."""
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        project_root = Path(__file__).resolve().parents[1]
        path = project_root / path
    path = path.resolve()
    if not path.is_file():
        raise RuntimeError(
            f"{setting_name} points to '{path}', but that certificate file does not exist. "
            "Check the Railway variable and make sure the file is included in the deployment."
        )
    return path


def _configured_provider_ca() -> tuple[Path | None, str | None]:
    """Return the provider-neutral CA as (file path, PEM data), if configured."""
    if DB_CA_CERT_FILE:
        return _resolve_cert_file(DB_CA_CERT_FILE, "DB_CA_CERT_FILE"), None

    if DB_CA_CERT_B64:
        try:
            pem = base64.b64decode(DB_CA_CERT_B64, validate=True).decode("utf-8")
        except Exception as exc:
            raise RuntimeError("DB_CA_CERT_B64 is not valid base64-encoded PEM data.") from exc
        if "-----BEGIN CERTIFICATE-----" not in pem or "-----END CERTIFICATE-----" not in pem:
            raise RuntimeError("DB_CA_CERT_B64 does not contain a PEM certificate.")
        return None, pem

    if DB_CA_CERT:
        pem = DB_CA_CERT.replace("\\n", "\n")
        if "-----BEGIN CERTIFICATE-----" not in pem or "-----END CERTIFICATE-----" not in pem:
            raise RuntimeError("DB_CA_CERT does not contain a PEM certificate.")
        return None, pem

    return None, None


def _ca_pem_for_cockroach() -> str | None:
    """Legacy CockroachDB-specific fallback, retained for existing deployments."""
    if COCKROACH_CA_CERT_FILE:
        path = _resolve_cert_file(COCKROACH_CA_CERT_FILE, "COCKROACH_CA_CERT_FILE")
        return path.read_text(encoding="utf-8")

    if COCKROACH_CA_CERT_B64:
        try:
            return base64.b64decode(COCKROACH_CA_CERT_B64, validate=True).decode("utf-8")
        except Exception as exc:
            raise RuntimeError("COCKROACH_CA_CERT_B64 is not valid base64-encoded PEM data.") from exc

    if COCKROACH_CA_CERT:
        return COCKROACH_CA_CERT.replace("\\n", "\n")

    return None


async def _download_cockroach_ca_cert() -> str:
    import urllib.request

    request = urllib.request.Request(
        COCKROACH_CA_CERT_URL,
        headers={"User-Agent": "Crickium-CockroachDB-TLS/1.0"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        pem = response.read().decode("utf-8").strip()

    if "BEGIN CERTIFICATE" not in pem or "END CERTIFICATE" not in pem:
        raise RuntimeError(
            "CockroachDB cluster CA endpoint did not return a valid PEM certificate."
        )
    return pem


def _make_verified_ssl_context(
    sslmode: str,
    ca_file: Path | None = None,
    ca_pem: str | None = None,
) -> ssl.SSLContext:
    """Build a TLS context while retaining system roots plus any custom provider CA."""
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    if ca_file is not None:
        context.load_verify_locations(cafile=str(ca_file))
    if ca_pem is not None:
        context.load_verify_locations(cadata=ca_pem)

    context.check_hostname = sslmode == "verify-full"
    context.verify_mode = ssl.CERT_REQUIRED

                                                                              
                                                                                
    if hasattr(context, "set_alpn_protocols"):
        context.set_alpn_protocols(["postgresql"])
    return context


def _ssl_connect_kwargs(
    dsn: str,
    sslmode: str,
    ca_file: Path | None = None,
    ca_pem: str | None = None,
) -> dict:
    """Return asyncpg TLS arguments for a libpq-compatible sslmode."""
    clean_dsn = _remove_query_keys(dsn, {"sslmode", "sslrootcert"})

    if sslmode == "disable":
        print("[db/connection] WARNING: DB SSL is disabled by configuration.")
        return {"dsn": clean_dsn, "ssl": False}

    if sslmode in {"allow", "prefer"}:
        return {"dsn": clean_dsn, "ssl": sslmode}

    if sslmode == "require":
                                                                                   
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        if hasattr(context, "set_alpn_protocols"):
            context.set_alpn_protocols(["postgresql"])
        return {"dsn": clean_dsn, "ssl": context}

    context = _make_verified_ssl_context(sslmode, ca_file=ca_file, ca_pem=ca_pem)
    return {"dsn": clean_dsn, "ssl": context}


async def get_asyncpg_connect_kwargs() -> dict:
    """Build provider-neutral asyncpg arguments using DATABASE_URL + optional CA.

    Provider-specific Cockroach support remains an automatic fallback. All other
    PostgreSQL providers can supply a CA through DB_CA_CERT_FILE, DB_CA_CERT_B64,
    DB_CA_CERT, or sslrootcert in DATABASE_URL. No provider-specific code change
    is needed when switching DATABASE_URL in Railway.
    """
    dsn = DATABASE_URL
    if not dsn:
        raise RuntimeError("DATABASE_URL is not configured.")

    parts = urlsplit(dsn)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
                                                                          
    sslmode = (DB_SSL_MODE or query.get("sslmode") or "verify-full").lower()
    if sslmode not in _VALID_SSL_MODES:
        allowed = ", ".join(sorted(_VALID_SSL_MODES))
        raise RuntimeError(f"Unsupported DB_SSL_MODE/sslmode '{sslmode}'. Use one of: {allowed}.")

    embedded_root = query.get("sslrootcert")
    ca_file: Path | None = None
    ca_pem: str | None = None

                                                                                 
    needs_verification = sslmode in {"verify-ca", "verify-full"}
    if needs_verification:
                                                                           
                                                                       
        ca_file, ca_pem = _configured_provider_ca()

        if (
            ca_file is None
            and ca_pem is None
            and embedded_root
            and embedded_root not in _DEFAULT_SSLROOTCERT_VALUES
        ):
            ca_file = _resolve_cert_file(embedded_root, "DATABASE_URL sslrootcert")

                                                                                   
        default_root_values = _DEFAULT_SSLROOTCERT_VALUES | {None, ""}
        if (
            ca_file is None
            and ca_pem is None
            and embedded_root in default_root_values
            and _is_cockroach_url(dsn)
        ):
            ca_pem = _ca_pem_for_cockroach()
            if ca_pem is None:
                ca_pem = await _download_cockroach_ca_cert()

    return _ssl_connect_kwargs(dsn, sslmode, ca_file=ca_file, ca_pem=ca_pem)


def is_retryable_transaction_error(exc: BaseException) -> bool:
    return getattr(exc, "sqlstate", None) == "40001"


async def connect():
    global _pool

    print("[db/connection] connect() called")

    if is_database_quota_blocked():
        raise RuntimeError("Database provider quota is temporarily exhausted; retry shortly.")

    if _pool is None:
        print("[db/connection] No existing pool, creating new asyncpg pool...")
        try:
            connect_kwargs = await get_asyncpg_connect_kwargs()
                                                                             
                                                                              
                                                                            
                                                                            
                                                                            
            if _is_cockroach_url(DATABASE_URL):
                connect_kwargs["server_settings"] = {
                    "multiple_active_portals_enabled": "true",
                }
            _pool = await asyncpg.create_pool(
                **connect_kwargs,
                                                                                                            
                                                                                                                   
                min_size=max(0, int(os.getenv("DB_POOL_MIN", "1"))),
                max_size=max(2, int(os.getenv("DB_POOL_MAX", "10"))),
                                                                                    
                                                                             
                max_inactive_connection_lifetime=300.0,
                command_timeout=60.0,
                timeout=30.0,
            )
            clear_database_quota_block()
            print("[db/connection] Pool created successfully.")
        except Exception as e:
            if _quota_error(e):
                mark_database_quota_exhausted(e)
            print(f"[db/connection] !! Failed to create pool: {e!r}")
            raise
    else:
        print("[db/connection] Pool already exists, reusing it.")

    return _pool


async def disconnect():
    global _pool

    print("[db/connection] disconnect() called")

    if _pool:
        await _pool.close()
        _pool = None
        print("[db/connection] Pool closed.")
    else:
        print("[db/connection] No pool to close.")


def get_pool():
    if is_database_quota_blocked():
        raise RuntimeError("Database provider quota is temporarily exhausted; retry shortly.")

    if _pool is None:
        print("[db/connection] !! get_pool() called but pool is None !!")
        raise RuntimeError("Database is not connected.")

    return _pool
