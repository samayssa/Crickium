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
)

_pool = None
_DB_BLOCKED_UNTIL = 0.0
_DB_BLOCK_REASON = None
_DB_COOLDOWN_SECONDS = 30.0


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


def _ca_pem_for_cockroach() -> str | None:
    """Return a CA only for CockroachDB connections, never for other providers."""
    if COCKROACH_CA_CERT_FILE:
        path = Path(COCKROACH_CA_CERT_FILE).expanduser()
        if not path.is_file():
            raise RuntimeError(
                f"COCKROACH_CA_CERT_FILE points to '{path}', but that file does not exist."
            )
        return path.read_text(encoding="utf-8")

    if COCKROACH_CA_CERT_B64:
        try:
            return base64.b64decode(COCKROACH_CA_CERT_B64).decode("utf-8")
        except Exception as exc:
            raise RuntimeError("COCKROACH_CA_CERT_B64 is not valid base64-encoded PEM data.") from exc

    if COCKROACH_CA_CERT:
        return COCKROACH_CA_CERT.replace("\\n", "\n")

    return None


async def get_asyncpg_connect_kwargs() -> dict:
    """Build provider-neutral asyncpg connection arguments.

    CockroachDB Cloud gets its custom cluster CA only when the current URL is
    actually a CockroachDB Cloud URL. Other PostgreSQL providers use their own
    explicit sslrootcert, or the normal system CA trust store.
    """
    dsn = DATABASE_URL
    if not dsn:
        raise RuntimeError("DATABASE_URL is not configured.")

    parts = urlsplit(dsn)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    sslmode = (query.get("sslmode") or "verify-full").lower()
    embedded_root = query.get("sslrootcert")
    is_cockroach = _is_cockroach_url(dsn)

    # Only honor Cockroach-specific CA settings after the URL has been classified
    # as CockroachDB. This makes swapping DATABASE_URL to another PostgreSQL
    # provider safe and avoids an unnecessary certificate download.
    if is_cockroach:
        ca_pem = _ca_pem_for_cockroach()

        if ca_pem is None:
            ca_pem = await _download_cockroach_ca_cert()

        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cadata=ca_pem)
        ctx.check_hostname = sslmode == "verify-full"
        dsn_without_cert = _remove_query_keys(dsn, {"sslrootcert"})
        return {"dsn": dsn_without_cert, "ssl": ctx}

    if embedded_root and embedded_root not in {"~/.postgresql/root.crt", "/root/.postgresql/root.crt"}:
        path = Path(embedded_root).expanduser()
        if path.is_file():
            ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(path))
            ctx.check_hostname = sslmode == "verify-full"
            return {"dsn": _remove_query_keys(dsn, {"sslrootcert"}), "ssl": ctx}

    if sslmode in {"verify-full", "verify-ca"}:
        # Standard managed PostgreSQL providers generally use public CA chains.
        # Use the runtime trust store rather than any Cockroach-specific CA.
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        ctx.check_hostname = sslmode == "verify-full"
        return {"dsn": _remove_query_keys(dsn, {"sslrootcert"}), "ssl": ctx}

    # Explicit ssl=require remains encrypted but skips server-certificate verification.
    return {"dsn": dsn}


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
            # CockroachDB exposes this session setting to allow pgwire portal
            # execution that asyncpg can otherwise trigger when statements are
            # prepared/executed back-to-back inside one transaction. Keep it
            # scoped to CockroachDB so the same code remains compatible with
            # ordinary PostgreSQL if the DATABASE_URL is ever switched back.
            if _is_cockroach_url(DATABASE_URL):
                connect_kwargs["server_settings"] = {
                    "multiple_active_portals_enabled": "true",
                }
            _pool = await asyncpg.create_pool(
                **connect_kwargs,
                min_size=0,
                max_size=5,
                max_inactive_connection_lifetime=300.0,
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
