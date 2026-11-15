"""Small dependency-free asyncio Redis client (RESP2) built for Upstash.

Why this exists
---------------
The cache layer must work on Railway without adding a new pip dependency, so
this module speaks the Redis wire protocol directly over asyncio streams.  It
supports exactly what the cache needs: TLS (``rediss://``), ``AUTH``, a tiny
connection pool, pipelining and strict timeouts.

Safety rules baked in
---------------------
* Every exchange has a hard timeout.  A connection that timed out, was
  cancelled or raised *anything* is closed and never reused, so a half-read
  reply can never be mistaken for the answer to another command.
* A reused (idle) connection that turns out to be dead is retried once on a
  fresh connection; timeouts are never retried.
* Error replies are classified (auth / quota / other) so the caller can decide
  how long to back off.
* Nothing here ever raises on import and nothing connects until first use.
"""
from __future__ import annotations

import asyncio
import re
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any, Iterable, Sequence
from urllib.parse import unquote, urlsplit


class RedisError(Exception):
    """Base class for every error raised by this module."""


class RedisConnectionError(RedisError):
    """Network/protocol level failure (connect, TLS, EOF, malformed reply)."""


class RedisTimeoutError(RedisConnectionError):
    """The server did not answer in time."""


class RedisResponseError(RedisError):
    """The server answered with an ``-ERR ...`` style reply."""


class RedisAuthError(RedisResponseError):
    """Wrong or missing credentials."""


class RedisQuotaError(RedisResponseError):
    """Plan limit reached (Upstash request/data/bandwidth quota)."""


_QUOTA_HINTS = (
    "max requests limit",
    "max daily request limit",
    "max monthly",
    "limit exceeded",
    "quota",
    "max data size",
    "max concurrent connections",
    "bandwidth limit",
)
_AUTH_PREFIXES = ("WRONGPASS", "NOAUTH", "NOPERM", "ERR INVALID PASSWORD", "ERR AUTH", "ERR WRONG PASSWORD")


def classify_error(message: str) -> RedisResponseError:
    text = str(message or "").strip()
    upper = text.upper()
    if upper.startswith(_AUTH_PREFIXES):
        return RedisAuthError(text)
    lower = text.lower()
    if any(hint in lower for hint in _QUOTA_HINTS):
        return RedisQuotaError(text)
    return RedisResponseError(text)


@dataclass(frozen=True)
class RedisSettings:
    host: str
    port: int = 6379
    username: str | None = None
    password: str | None = None
    db: int = 0
    tls: bool = False

    def describe(self) -> str:
        """Safe-to-log description (never includes the password)."""
        scheme = "rediss" if self.tls else "redis"
        return f"{scheme}://{self.host}:{self.port}/{self.db}"


_URL_RE = re.compile(r"(rediss?://[^\s'\"<>]+)", re.IGNORECASE)


def parse_redis_url(value: str) -> RedisSettings:
    """Parse ``rediss://default:PASSWORD@host:6379`` (also tolerates copy-paste noise).

    Upstash shows the string inside a ``redis-cli --tls -u ...`` command; the
    first ``redis://`` / ``rediss://`` token is extracted so that pasting the
    whole command into a Railway variable still works.
    """
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("REDIS_URL is empty.")
    match = _URL_RE.search(raw)
    if match:
        raw = match.group(1)
    parts = urlsplit(raw)
    scheme = (parts.scheme or "").lower()
    if scheme in {"http", "https"}:
        raise ValueError(
            "REDIS_URL looks like the Upstash REST url (https://...). "
            "Use the Redis connection string that starts with rediss:// instead."
        )
    if scheme not in {"redis", "rediss"}:
        raise ValueError("REDIS_URL must start with rediss:// (or redis://).")
    host = parts.hostname
    if not host:
        raise ValueError("REDIS_URL has no host.")
    try:
        port = parts.port or 6379
    except ValueError as exc:
        raise ValueError("REDIS_URL has an invalid port.") from exc
    username = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password is not None else None
    db = 0
    path = (parts.path or "").strip("/")
    if path:
        try:
            db = int(path)
        except ValueError as exc:
            raise ValueError("REDIS_URL database index must be a number.") from exc
    tls = scheme == "rediss"
    if host.lower().endswith(".upstash.io"):
        tls = True   # Upstash only accepts TLS connections.
    return RedisSettings(host=host, port=int(port), username=username, password=password, db=db, tls=tls)


def _encode_arg(arg: Any) -> bytes:
    if isinstance(arg, (bytes, bytearray, memoryview)):
        return bytes(arg)
    if isinstance(arg, bool):
        return b"1" if arg else b"0"
    if isinstance(arg, int):
        return str(arg).encode("ascii")
    if isinstance(arg, float):
        return repr(arg).encode("ascii")
    return str(arg).encode("utf-8")


def encode_command(args: Sequence[Any]) -> bytes:
    chunks = [b"*%d\r\n" % len(args)]
    for arg in args:
        data = _encode_arg(arg)
        chunks.append(b"$%d\r\n" % len(data))
        chunks.append(data)
        chunks.append(b"\r\n")
    return b"".join(chunks)


_MAX_BULK_BYTES = 64 * 1024 * 1024
_MAX_ARRAY_ITEMS = 1_000_000


async def _read_reply(reader: asyncio.StreamReader, depth: int = 0) -> Any:
    line = await reader.readline()
    if not line:
        raise RedisConnectionError("connection closed by server")
    if not line.endswith(b"\r\n"):
        raise RedisConnectionError("truncated reply")
    kind = line[:1]
    body = line[1:-2]
    if kind == b"+":
        return body.decode("utf-8", "replace")
    if kind == b"-":
        return classify_error(body.decode("utf-8", "replace"))
    if kind == b":":
        return int(body)
    if kind == b"$":
        size = int(body)
        if size < 0:
            return None
        if size > _MAX_BULK_BYTES:
            raise RedisConnectionError("bulk reply too large")
        data = await reader.readexactly(size + 2)
        return data[:-2]
    if kind == b"*":
        count = int(body)
        if count < 0:
            return None
        if count > _MAX_ARRAY_ITEMS or depth > 8:
            raise RedisConnectionError("array reply too large")
        return [await _read_reply(reader, depth + 1) for _ in range(count)]
    raise RedisConnectionError(f"unexpected reply type {kind!r}")


class _Connection:
    __slots__ = ("reader", "writer", "last_used", "closed")

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.last_used = time.monotonic()
        self.closed = False

    async def exchange(self, commands: Sequence[Sequence[Any]]) -> list[Any]:
        self.writer.write(b"".join(encode_command(cmd) for cmd in commands))
        await self.writer.drain()
        return [await _read_reply(self.reader) for _ in commands]

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.writer.close()
        except Exception:
            pass


def _is_dead_connection_error(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, BrokenPipeError, EOFError, asyncio.IncompleteReadError)):
        return True
    if isinstance(exc, RedisConnectionError) and not isinstance(exc, RedisTimeoutError):
        return "closed" in str(exc) or "truncated" in str(exc)
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, OSError):
        return True
    return False


class RedisClient:
    """Minimal pooled Redis client. All methods are coroutines."""

    def __init__(
        self,
        settings: RedisSettings,
        *,
        timeout: float = 1.5,
        connect_timeout: float = 2.0,
        pool_size: int = 4,
        max_idle_seconds: float = 30.0,
        verify_tls: bool = True,
    ) -> None:
        self.settings = settings
        self.timeout = max(0.2, float(timeout))
        self.connect_timeout = max(0.5, float(connect_timeout))
        self.pool_size = max(1, int(pool_size))
        self.max_idle_seconds = max(1.0, float(max_idle_seconds))
        self.verify_tls = bool(verify_tls)
        self._idle: list[_Connection] = []
        self._sem: asyncio.Semaphore | None = None
        self._closed = False
        self._ssl_context: ssl.SSLContext | None = None
        # counters (read by the cache stats page)
        self.commands = 0
        self.calls = 0
        self.errors = 0
        self.timeouts = 0
        self.latency_total = 0.0
        self.latency_max = 0.0
        self.connects = 0

    # ------------------------------------------------------------------ pool
    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.pool_size)
        return self._sem

    def _make_ssl_context(self) -> ssl.SSLContext:
        if self._ssl_context is None:
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            if not self.verify_tls:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            self._ssl_context = context
        return self._ssl_context

    async def _open(self) -> _Connection:
        st = self.settings
        ssl_ctx = self._make_ssl_context() if st.tls else None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    st.host,
                    st.port,
                    ssl=ssl_ctx,
                    server_hostname=st.host if ssl_ctx else None,
                    limit=1 << 20,
                ),
                timeout=self.connect_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise RedisTimeoutError("connect timeout") from exc
        sock = writer.get_extra_info("socket")
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except Exception:
                pass
        conn = _Connection(reader, writer)
        self.connects += 1
        try:
            await self._handshake(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    async def _handshake(self, conn: _Connection) -> None:
        st = self.settings
        if st.password:
            if st.username:
                reply = (await conn.exchange([("AUTH", st.username, st.password)]))[0]
                if isinstance(reply, RedisResponseError) and "wrong number of arguments" in str(reply).lower():
                    reply = (await conn.exchange([("AUTH", st.password)]))[0]
            else:
                reply = (await conn.exchange([("AUTH", st.password)]))[0]
            if isinstance(reply, RedisResponseError):
                raise reply if isinstance(reply, RedisAuthError) else RedisAuthError(str(reply))
        if st.db:
            reply = (await conn.exchange([("SELECT", st.db)]))[0]
            if isinstance(reply, RedisResponseError):
                raise reply

    async def _checkout(self) -> tuple[_Connection, bool]:
        now = time.monotonic()
        while self._idle:
            conn = self._idle.pop()
            if conn.closed or now - conn.last_used > self.max_idle_seconds:
                conn.close()
                continue
            return conn, True
        return await self._open(), False

    def _checkin(self, conn: _Connection) -> None:
        if self._closed or conn.closed:
            conn.close()
            return
        conn.last_used = time.monotonic()
        if len(self._idle) >= self.pool_size:
            conn.close()
            return
        self._idle.append(conn)

    # ------------------------------------------------------------- execution
    async def execute_many(self, commands: Sequence[Sequence[Any]], *, timeout: float | None = None) -> list[Any]:
        """Run several commands in one round trip.

        Error replies are returned *inside* the list as ``RedisResponseError``
        instances so one bad command cannot desynchronise the connection.
        """
        if self._closed:
            raise RedisConnectionError("client is closed")
        if not commands:
            return []
        limit = float(timeout or self.timeout)
        started = time.monotonic()
        self.calls += 1
        self.commands += len(commands)
        sem = self._semaphore()
        try:
            await asyncio.wait_for(sem.acquire(), timeout=limit)
        except asyncio.TimeoutError as exc:
            self.errors += 1
            self.timeouts += 1
            raise RedisTimeoutError("connection pool busy") from exc
        try:
            for attempt in (0, 1):
                conn: _Connection | None = None
                reused = False
                try:
                    remaining = max(0.05, limit - (time.monotonic() - started))
                    conn, reused = await asyncio.wait_for(self._checkout(), timeout=remaining + self.connect_timeout)
                    remaining = max(0.05, limit - (time.monotonic() - started))
                    results = await asyncio.wait_for(conn.exchange(commands), timeout=remaining)
                except asyncio.CancelledError:
                    if conn is not None:
                        conn.close()
                    raise
                except asyncio.TimeoutError as exc:
                    if conn is not None:
                        conn.close()
                    self.errors += 1
                    self.timeouts += 1
                    raise RedisTimeoutError("redis timeout") from exc
                except RedisError as exc:
                    if conn is not None:
                        conn.close()
                    if reused and attempt == 0 and _is_dead_connection_error(exc):
                        continue
                    self.errors += 1
                    if isinstance(exc, RedisTimeoutError):
                        self.timeouts += 1
                    raise
                except Exception as exc:
                    if conn is not None:
                        conn.close()
                    if reused and attempt == 0 and _is_dead_connection_error(exc):
                        continue
                    self.errors += 1
                    raise RedisConnectionError(f"{type(exc).__name__}: {exc}") from exc
                else:
                    self._checkin(conn)
                    elapsed = time.monotonic() - started
                    self.latency_total += elapsed
                    if elapsed > self.latency_max:
                        self.latency_max = elapsed
                    return results
            raise RedisConnectionError("connection kept failing")
        finally:
            sem.release()

    async def execute(self, *args: Any, timeout: float | None = None) -> Any:
        reply = (await self.execute_many([args], timeout=timeout))[0]
        if isinstance(reply, RedisResponseError):
            self.errors += 1
            raise reply
        return reply

    # ----------------------------------------------------------- conveniences
    async def get(self, key: str) -> bytes | None:
        return await self.execute("GET", key)

    async def set(
        self,
        key: str,
        value: bytes | str,
        *,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
        xx: bool = False,
    ) -> bool:
        args: list[Any] = ["SET", key, value]
        if ex is not None:
            args += ["EX", max(1, int(ex))]
        elif px is not None:
            args += ["PX", max(1, int(px))]
        if nx:
            args.append("NX")
        if xx:
            args.append("XX")
        return (await self.execute(*args)) == "OK"

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        return int(await self.execute("DEL", *keys) or 0)

    async def mget(self, keys: Iterable[str]) -> list[bytes | None]:
        keys = list(keys)
        if not keys:
            return []
        return list(await self.execute("MGET", *keys))

    async def incr(self, key: str) -> int:
        return int(await self.execute("INCR", key))

    async def ping(self) -> bool:
        return (await self.execute("PING")) == "PONG"

    async def close(self) -> None:
        self._closed = True
        idle, self._idle = self._idle, []
        for conn in idle:
            conn.close()
        for conn in idle:
            try:
                await asyncio.wait_for(conn.writer.wait_closed(), timeout=0.5)
            except Exception:
                pass

    def snapshot(self) -> dict[str, Any]:
        calls = max(1, self.calls)
        return {
            "target": self.settings.describe(),
            "commands": self.commands,
            "calls": self.calls,
            "errors": self.errors,
            "timeouts": self.timeouts,
            "connects": self.connects,
            "avg_ms": round(self.latency_total * 1000.0 / calls, 1),
            "max_ms": round(self.latency_max * 1000.0, 1),
        }
