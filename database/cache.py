"""Crickium auto-cache: in-process L1 + optional Upstash Redis L2.

The cache is a *performance layer only*.  The PostgreSQL database stays the
single source of truth, and every code path keeps working (just slower) when
Redis is missing, slow, over quota or down:

* ``get_or_load`` always falls back to the caller's loader.
* Redis errors never reach callers; a circuit breaker stops talking to Redis
  for a while after repeated failures or quota errors.
* Nothing is cached without a TTL, and every TTL is an *automatic delete*
  (L1 entries expire, Redis keys carry ``EX``).

Invalidation
------------
Writers call ``invalidate_tag("catalog")`` *after* their database commit.
Tags are generation counters: bumping one makes every key that was stored
under the old generation unreachable at once (L1 and L2), no key scanning
needed.  ``flush_all()`` bumps the global generation.  The cache namespace is
derived from the database identity, so pointing the bot at a different
database can never read another database's cached rows.

What must never be cached
-------------------------
Anything whose value is read and then written back, or that is money/ownership
state (balances, squads, lineups, claims, trades, packs, auctions, game
sessions, quest progress...).  ``FORBIDDEN_FAMILIES`` is a safety net that
refuses such keys at runtime.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as _dt
import decimal
import hashlib
import hmac
import json
import math
import os
import random
import sys
import time
import uuid
import zlib
from collections import OrderedDict
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from database import redis_lite
from database.redis_lite import (
    RedisAuthError,
    RedisError,
    RedisQuotaError,
)

_ALL = "_all"
_VERSION = "v1"
_MISS = object()

# Key families (first ``:`` segment) that are never allowed in the cache.
FORBIDDEN_FAMILIES = frozenset({
    "user", "users", "balance", "wallet", "coins", "rubies", "sigils", "xp", "level",
    "squad", "squads", "lineup", "lineups", "claim", "claims", "trade", "trades",
    "pack", "packs", "gift", "gifts", "giveaway", "daily", "referral", "referrals",
    "tournament", "auction", "bid", "bids", "session", "sessions", "upgrade_owned",
    "loadout", "quest", "backup", "runtime", "counter", "match_no",
})


def _log(message: str) -> None:
    print(f"[cache] {message}", flush=True)


# --------------------------------------------------------------------------- codec
class CodecError(ValueError):
    pass


def _to_jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 24:
        raise CodecError("value nested too deeply")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"__t": "f", "v": repr(value)}
    if isinstance(value, _dt.datetime):
        return {"__t": "dt", "v": value.isoformat()}
    if isinstance(value, _dt.date):
        return {"__t": "d", "v": value.isoformat()}
    if isinstance(value, _dt.timedelta):
        return {"__t": "td", "v": value.total_seconds()}
    if isinstance(value, decimal.Decimal):
        return {"__t": "dec", "v": str(value)}
    if isinstance(value, uuid.UUID):
        return {"__t": "uuid", "v": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__t": "b", "v": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (set, frozenset)):
        return {"__t": "set", "v": [_to_jsonable(v, depth + 1) for v in value]}
    if isinstance(value, tuple):
        return {"__t": "tup", "v": [_to_jsonable(v, depth + 1) for v in value]}
    if isinstance(value, list):
        return [_to_jsonable(v, depth + 1) for v in value]
    if hasattr(value, "keys") and hasattr(value, "items"):      # dict, asyncpg.Record
        items = list(value.items())
        if all(isinstance(k, str) for k, _ in items) and not any(k == "__t" for k, _ in items):
            return {k: _to_jsonable(v, depth + 1) for k, v in items}
        return {"__t": "map", "v": [[_to_jsonable(k, depth + 1), _to_jsonable(v, depth + 1)] for k, v in items]}
    raise CodecError(f"cannot cache value of type {type(value).__name__}")


def _hashable(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_hashable(v) for v in value)
    return value


def _object_hook(obj: dict) -> Any:
    tag = obj.get("__t")
    if tag is None or "v" not in obj:
        return obj
    raw = obj["v"]
    if tag == "dt":
        return _dt.datetime.fromisoformat(raw)
    if tag == "d":
        return _dt.date.fromisoformat(raw)
    if tag == "td":
        return _dt.timedelta(seconds=raw)
    if tag == "dec":
        return decimal.Decimal(raw)
    if tag == "uuid":
        return uuid.UUID(raw)
    if tag == "b":
        return base64.b64decode(raw)
    if tag == "f":
        return float(raw)
    if tag == "set":
        return {_hashable(v) for v in raw}
    if tag == "tup":
        return tuple(raw)
    if tag == "map":
        return {_hashable(k): v for k, v in raw}
    return obj


def encode_value(value: Any) -> str:
    """Python value -> compact JSON text (raises ``CodecError`` if not cacheable)."""
    try:
        return json.dumps(_to_jsonable(value), ensure_ascii=False, separators=(",", ":"))
    except CodecError:
        raise
    except (TypeError, ValueError, RecursionError) as exc:
        raise CodecError(str(exc)) from exc


def decode_value(text: str) -> Any:
    return json.loads(text, object_hook=_object_hook)


def _pack(text: str, expires_at: float) -> bytes:
    """Envelope: kind byte + 10-digit unix expiry + (optionally zlib-compressed) JSON."""
    raw = text.encode("utf-8")
    stamp = b"%010d" % max(0, int(expires_at))
    if len(raw) > 2048:
        packed = zlib.compress(raw, 3)
        if len(packed) < len(raw):
            return b"Z" + stamp + packed
    return b"J" + stamp + raw


def _unpack(blob: bytes) -> tuple[str, float]:
    kind, stamp, body = blob[:1], blob[1:11], blob[11:]
    expires_at = float(int(stamp))
    if kind == b"Z":
        return zlib.decompress(body).decode("utf-8"), expires_at
    if kind == b"J":
        return body.decode("utf-8"), expires_at
    raise CodecError("unknown cache envelope")


# ----------------------------------------------------------------------------- L1
class _L1:
    """Bounded LRU of encoded values with per-entry expiry and generation token."""

    def __init__(self, max_entries: int, max_bytes: int) -> None:
        self.max_entries = max(16, int(max_entries))
        self.max_bytes = max(1 << 20, int(max_bytes))
        self._data: "OrderedDict[str, tuple[float, tuple, int, str]]" = OrderedDict()
        self._bytes = 0
        self.evictions = 0

    def __len__(self) -> int:
        return len(self._data)

    @property
    def size_bytes(self) -> int:
        return self._bytes

    def get(self, key: str, token: tuple, now: float) -> Any:
        entry = self._data.get(key)
        if entry is None:
            return _MISS
        expires, entry_token, _size, text = entry
        if expires <= now or entry_token != token:
            self._remove(key)
            return _MISS
        self._data.move_to_end(key)
        return text

    def put(self, key: str, token: tuple, expires: float, text: str) -> None:
        size = len(text) + len(key) + 160
        if size > self.max_bytes // 4:
            return
        self._remove(key)
        self._data[key] = (expires, token, size, text)
        self._bytes += size
        while self._data and (len(self._data) > self.max_entries or self._bytes > self.max_bytes):
            old_key, old = self._data.popitem(last=False)
            self._bytes -= old[2]
            self.evictions += 1

    def _remove(self, key: str) -> None:
        old = self._data.pop(key, None)
        if old is not None:
            self._bytes -= old[2]

    def remove(self, key: str) -> None:
        self._remove(key)

    def clear(self) -> None:
        self._data.clear()
        self._bytes = 0

    def sweep(self, now: float) -> int:
        dead = [k for k, v in self._data.items() if v[0] <= now]
        for key in dead:
            self._remove(key)
        return len(dead)


# ------------------------------------------------------------------------ breaker
class _Breaker:
    def __init__(self, threshold: int = 2, base_cooldown: float = 30.0, max_cooldown: float = 300.0) -> None:
        self.threshold = threshold
        self.base_cooldown = base_cooldown
        self.max_cooldown = max_cooldown
        self.failures = 0
        self.open_until = 0.0
        self.cooldown = base_cooldown
        self.opened_before = False
        self.probe_started = 0.0
        self.reason = ""
        self.opens = 0

    def is_open(self, now: float | None = None) -> bool:
        return (now or time.monotonic()) < self.open_until

    def allow(self) -> bool:
        now = time.monotonic()
        if now < self.open_until:
            return False
        if self.opened_before:
            # half-open: let a single probe through at a time
            if self.probe_started and now - self.probe_started < 10.0:
                return False
            self.probe_started = now
        return True

    def success(self) -> None:
        if self.opened_before:
            _log("Redis recovered; cache L2 is active again.")
        self.failures = 0
        self.cooldown = self.base_cooldown
        self.opened_before = False
        self.probe_started = 0.0
        self.reason = ""

    def failure(self, reason: str, hard_cooldown: float | None = None) -> None:
        if hard_cooldown is None and self.is_open():
            return          # late failure of a request that started before the breaker opened
        self.failures += 1
        self.probe_started = 0.0
        if hard_cooldown is None and self.failures < self.threshold and not self.opened_before:
            return
        if hard_cooldown is not None:
            cooldown = hard_cooldown
        elif self.opened_before:
            cooldown = min(self.cooldown * 2, self.max_cooldown)
        else:
            cooldown = self.base_cooldown
        self.cooldown = cooldown
        self.open_until = max(self.open_until, time.monotonic() + cooldown)
        self.opened_before = True
        self.opens += 1
        self.reason = reason
        _log(f"Redis paused for {cooldown:.0f}s (cache falls back to memory + database): {reason}")


class _LeaderAborted(Exception):
    """The request that was loading a value was cancelled; waiters retry."""


# ------------------------------------------------------------------------- engine
class Cache:
    def __init__(self) -> None:
        self.enabled = False
        self.started = False
        self.role = "bot"
        self.prefix = f"crk:none:{_VERSION}"
        self.namespace = "none"
        self._client: redis_lite.RedisClient | None = None
        self._redis_target = ""
        self._breaker = _Breaker()
        self._l1 = _L1(3000, 48 << 20)
        self._gens: dict[str, int | None] = {}
        self._epoch: dict[str, int] = {_ALL: 0}
        self._pending: set[str] = set()
        self._known_tags: set[str] = set()
        self._gens_at = 0.0
        self._refreshing = False
        self._flushing = False
        self._flights: dict[str, asyncio.Future] = {}
        self._bg: set[asyncio.Task] = set()
        self._latest: dict[str, tuple[str, Any]] = {}
        self._writers: set[str] = set()
        self._blob_ok: dict[str, Any] = {}
        self._sweeper: asyncio.Task | None = None
        self._signing_key: bytes | None = None
        self._gen_refresh_seconds = 90.0
        self._max_blob_bytes = 700_000
        self._max_value_bytes = 400_000
        self._l1_max_ttl = 21600.0
        self._daily_limit = 0
        self._day = ""
        self._used_today = 0
        self._slow_ms = 450.0
        self._ewma_ms = 0.0
        self._ewma_n = 0
        self._stats: dict[str, int] = {}
        self._families: dict[str, list[int]] = {}
        self._started_at = time.time()
        self._warned: set[str] = set()
        self._last_publish = 0.0

    # --------------------------------------------------------------- bookkeeping
    def _count(self, name: str, n: int = 1) -> None:
        self._stats[name] = self._stats.get(name, 0) + n

    def _family_count(self, key: str, slot: int) -> None:
        family = key.split(":", 1)[0][:24] or "?"
        row = self._families.get(family)
        if row is None:
            if len(self._families) >= 60:
                family = "other"
                row = self._families.setdefault(family, [0, 0, 0, 0])
            else:
                row = self._families.setdefault(family, [0, 0, 0, 0])
        row[slot] += 1

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            _log(message)

    # ------------------------------------------------------------------ lifecycle
    async def start(
        self,
        *,
        redis_url: str | None = None,
        role: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        if self.started:
            return self.status()
        try:
            import config as _config
            cfg = _config
        except Exception:                                       # pragma: no cover - config is always importable
            cfg = None

        def pick(name: str, default: Any) -> Any:
            return getattr(cfg, name, default) if cfg is not None else default

        self.enabled = bool(pick("CACHE_ENABLED", True)) if enabled is None else bool(enabled)
        self.role = role or os.getenv("CACHE_ROLE", "").strip().lower() or _detect_role()
        self._l1 = _L1(int(pick("CACHE_L1_MAX_ENTRIES", 3000)), int(pick("CACHE_L1_MAX_MB", 48)) << 20)
        self._gen_refresh_seconds = max(5.0, float(pick("CACHE_GEN_REFRESH_SECONDS", 90)))
        self._daily_limit = max(0, int(pick("CACHE_REDIS_DAILY_COMMAND_LIMIT", 0)))
        self._slow_ms = max(50.0, float(pick("REDIS_SLOW_MS", 450)))
        self.namespace = _namespace(str(pick("DATABASE_URL", "")), str(pick("CACHE_NAMESPACE", "")))
        self.prefix = f"crk:{self.namespace}:{_VERSION}"
        self._signing_key = _signing_key(
            str(pick("CACHE_SIGNING_KEY", "")), str(pick("BOT_TOKEN", "")),
        )
        self.started = True
        if not self.enabled:
            _log("disabled (CACHE_ENABLED=0): every read goes straight to the database.")
            return self.status()

        url = (redis_url if redis_url is not None else str(pick("REDIS_URL", ""))).strip()
        if url:
            try:
                settings = redis_lite.parse_redis_url(url)
                self._client = redis_lite.RedisClient(
                    settings,
                    timeout=float(pick("REDIS_TIMEOUT_SECONDS", 1.5)),
                    connect_timeout=float(pick("REDIS_CONNECT_TIMEOUT_SECONDS", 2.0)),
                    pool_size=int(pick("REDIS_POOL_SIZE", 4)),
                    verify_tls=bool(pick("REDIS_TLS_VERIFY", True)),
                )
                self._redis_target = settings.describe()
            except ValueError as exc:
                self._client = None
                _log(f"REDIS_URL ignored: {exc}")
        else:
            _log("REDIS_URL is not set: running with the in-memory cache only (add REDIS_URL on Railway to enable Redis).")

        if self._client is not None:
            started = time.monotonic()
            try:
                await self._r("PING")
                await self._refresh_gens()
                _log(f"Redis connected ({self._redis_target}) role={self.role} ping={(time.monotonic() - started) * 1000:.0f}ms")
            except Exception as exc:
                _log(f"Redis not reachable right now ({type(exc).__name__}: {exc}); continuing with memory cache and database fallback.")
        try:
            self._sweeper = asyncio.get_running_loop().create_task(self._sweep_loop())
        except RuntimeError:
            self._sweeper = None
        return self.status()

    async def stop(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            self._sweeper = None
        for task in list(self._bg):
            task.cancel()
        self._bg.clear()
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
        self._client = None
        self.started = False

    async def _sweep_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(60.0)
                self._l1.sweep(time.monotonic())
                if self._client is not None and self.redis_usable() and time.monotonic() - self._last_publish > 600:
                    await self._publish_stats()
            except asyncio.CancelledError:
                return
            except Exception as exc:                            # never let the sweeper die
                self._warn_once("sweep", f"sweeper error: {exc!r}")

    # --------------------------------------------------------------------- redis
    def redis_configured(self) -> bool:
        return self._client is not None

    def redis_usable(self) -> bool:
        if self._client is None or not self.enabled:
            return False
        if self._breaker.is_open():
            return False
        if self._daily_limit and self._roll_day() >= self._daily_limit:
            return False
        return True

    def _roll_day(self) -> int:
        today = time.strftime("%Y%m%d", time.gmtime())
        if today != self._day:
            self._day = today
            self._used_today = 0
        return self._used_today

    async def _r_many(self, commands: list[tuple]) -> list[Any]:
        """Run commands through the breaker. Raises ``RedisError`` on failure."""
        client = self._client
        if client is None:
            raise redis_lite.RedisConnectionError("redis not configured")
        if not self._breaker.allow():
            raise redis_lite.RedisConnectionError("redis paused")
        if self._daily_limit:
            if self._roll_day() + len(commands) > self._daily_limit:
                raise redis_lite.RedisConnectionError("daily redis command budget reached")
            self._used_today += len(commands)
        started = time.monotonic()
        try:
            replies = await client.execute_many(commands)
        except RedisAuthError as exc:
            self._breaker.failure(f"authentication failed: {exc}", hard_cooldown=900.0)
            self._count("redis_errors")
            raise
        except RedisQuotaError as exc:
            self._breaker.failure(f"plan limit reached: {exc}", hard_cooldown=600.0)
            self._count("redis_errors")
            raise
        except RedisError as exc:
            self._breaker.failure(str(exc) or type(exc).__name__)
            self._count("redis_errors")
            raise
        for reply in replies:
            if isinstance(reply, RedisAuthError):
                self._breaker.failure(f"authentication failed: {reply}", hard_cooldown=900.0)
                self._count("redis_errors")
                raise reply
            if isinstance(reply, RedisQuotaError):
                self._breaker.failure(f"plan limit reached: {reply}", hard_cooldown=600.0)
                self._count("redis_errors")
                raise reply
        elapsed_ms = (time.monotonic() - started) * 1000.0
        self._ewma_ms = elapsed_ms if self._ewma_n == 0 else self._ewma_ms * 0.8 + elapsed_ms * 0.2
        self._ewma_n += 1
        self._breaker.success()
        if self._ewma_n >= 8 and self._ewma_ms > self._slow_ms:
            avg = self._ewma_ms
            self._ewma_ms, self._ewma_n = 0.0, 0
            self._breaker.failure(
                f"Redis is slow (avg {avg:.0f} ms) - create the Upstash database in the same region as Railway",
                hard_cooldown=120.0,
            )
        return replies

    async def _r(self, *args: Any) -> Any:
        reply = (await self._r_many([tuple(args)]))[0]
        if isinstance(reply, RedisError):
            self._count("redis_errors")
            raise reply
        return reply

    # ----------------------------------------------------------------- generations
    def _gen_key(self, tag: str) -> str:
        return f"{self.prefix}:gen:{tag}"

    def _register_tag(self, tag: str | None) -> None:
        if tag and tag not in self._known_tags:
            self._known_tags.add(tag)
            self._epoch.setdefault(tag, 0)

    def _token(self, tag: str | None) -> tuple:
        return (
            self._gens.get(_ALL),
            self._epoch.get(_ALL, 0),
            self._gens.get(tag) if tag else 0,
            self._epoch.get(tag, 0) if tag else 0,
        )

    def _l2_ready(self, tag: str | None) -> bool:
        if not self.redis_usable():
            return False
        if self._gens.get(_ALL) is None or _ALL in self._pending:
            return False
        if tag and (self._gens.get(tag) is None or tag in self._pending):
            return False
        return True

    async def _refresh_gens(self) -> None:
        tags = [_ALL, *sorted(self._known_tags)]
        keys = [self._gen_key(t) for t in tags]
        values = list(await self._r("MGET", *keys))
        missing = [i for i, v in enumerate(values) if v is None]
        if missing:
            now_ms = int(time.time() * 1000)
            commands: list[tuple] = []
            for i in missing:
                commands.append(("SET", keys[i], now_ms, "NX"))
                commands.append(("GET", keys[i]))
            replies = await self._r_many(commands)
            for n, i in enumerate(missing):
                got = replies[2 * n + 1]
                if isinstance(got, RedisError):
                    raise got
                values[i] = got
        for tag, value in zip(tags, values):
            if value is None:
                continue
            try:
                self._gens[tag] = int(value)
            except (TypeError, ValueError):
                self._gens[tag] = None
        self._gens_at = time.monotonic()

    def _spawn(self, coro: Awaitable[Any]) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coro)          # type: ignore[arg-type]
        except RuntimeError:
            if hasattr(coro, "close"):
                coro.close()                                            # type: ignore[union-attr]
            return
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _refresh_safely(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        try:
            await self._flush_pending()
            await self._refresh_gens()
        except Exception as exc:
            self._warn_once("gens", f"generation refresh failed: {type(exc).__name__}: {exc}")
        finally:
            self._refreshing = False

    async def _flush_pending(self) -> None:
        if self._flushing or not self._pending or not self.redis_usable():
            return
        self._flushing = True
        try:
            tags = sorted(self._pending)
            now_ms = int(time.time() * 1000)
            commands: list[tuple] = []
            for t in tags:
                commands.append(("SET", self._gen_key(t), now_ms, "NX"))
                commands.append(("INCR", self._gen_key(t)))
            replies = await self._r_many(commands)
            for n, tag in enumerate(tags):
                reply = replies[2 * n + 1]
                if isinstance(reply, int):
                    self._gens[tag] = int(reply)
                    self._pending.discard(tag)
        except Exception as exc:
            self._warn_once("incr", f"could not publish invalidation yet ({type(exc).__name__}); will retry")
        finally:
            self._flushing = False

    def _maybe_refresh(self) -> None:
        if self._client is None or self._refreshing:
            return
        if self._pending or time.monotonic() - self._gens_at > self._gen_refresh_seconds:
            if self.redis_usable():
                self._spawn(self._refresh_safely())

    def bump(self, tag: str) -> None:
        """Invalidate everything cached under ``tag`` (call *after* the DB commit)."""
        self._register_tag(tag)
        self._epoch[tag] = self._epoch.get(tag, 0) + 1
        self._count("invalidations")
        if self._client is not None:
            self._pending.add(tag)
            self._spawn(self._flush_pending())

    def flush_all(self) -> None:
        """Forget every cached value (memory immediately, Redis via generation bump)."""
        self._epoch[_ALL] = self._epoch.get(_ALL, 0) + 1
        self._l1.clear()
        self._count("flushes")
        if self._client is not None:
            self._pending.add(_ALL)
            self._spawn(self._flush_pending())

    async def flush_all_wait(self, timeout: float = 3.0) -> bool:
        """Like ``flush_all`` but waits until Redis acknowledged the new generation."""
        self.flush_all()
        if self._client is None:
            return True
        try:
            await asyncio.wait_for(self._flush_pending_blocking(), timeout=timeout)
        except Exception:
            pass
        return _ALL not in self._pending

    async def _flush_pending_blocking(self) -> None:
        for _ in range(20):
            if not self._pending:
                return
            if self._flushing:
                await asyncio.sleep(0.05)
                continue
            await self._flush_pending()
            if self._pending:
                await asyncio.sleep(0.05)

    # ------------------------------------------------------------------- key rules
    def _check_family(self, key: str) -> bool:
        family = key.split(":", 1)[0].lower()
        if family in FORBIDDEN_FAMILIES:
            self._warn_once(f"forbid:{family}", f"refused to cache '{key}': family '{family}' is on the never-cache list.")
            self._count("refused")
            return False
        return True

    def _l2_key(self, key: str, tag: str | None, token: tuple) -> str:
        middle = f"{tag}{token[2]}:" if tag else ""
        return f"{self.prefix}:g{token[0]}:{middle}{key}"

    # --------------------------------------------------------------- main read path
    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[Any]],
        *,
        ttl: float,
        l1_ttl: float | None = None,
        tag: str | None = None,
        l2: bool = True,
        neg_ttl: float | None = None,
    ) -> Any:
        """Return the cached value for ``key`` or load it with ``loader``.

        ``ttl``      seconds a value may live in Redis (automatic delete).
        ``l1_ttl``   seconds it may live in this process (defaults to ``ttl``).
        ``tag``      invalidation tag (``invalidate_tag`` makes the entry unreachable).
        ``l2``       set False for data that should only live in this process.
        ``neg_ttl``  cache ``None`` results for this many seconds (default: do not).
        Loader exceptions propagate and are never cached.
        """
        if not self.enabled or not self.started or not self._check_family(key):
            return await loader()
        try:
            return await self._get_or_load(key, loader, ttl, l1_ttl, tag, l2, neg_ttl)
        except _LeaderAborted:                                           # leader cancelled: just load directly
            return await loader()

    async def _get_or_load(self, key, loader, ttl, l1_ttl, tag, l2, neg_ttl):
        self._register_tag(tag)
        if self._client is not None:
            if tag and tag not in self._gens and self.redis_usable():
                await self._refresh_safely()
            else:
                self._maybe_refresh()
        ttl = max(1.0, float(ttl))
        l1_cap = min(float(l1_ttl) if l1_ttl else ttl, ttl, self._l1_max_ttl)
        l1_key = f"{tag or '-'}|{key}"
        for _attempt in range(3):
            token = self._token(tag)
            hit = self._l1.get(l1_key, token, time.monotonic())
            if hit is not _MISS:
                self._count("l1_hits")
                self._family_count(key, 0)
                return decode_value(hit)
            flight = self._flights.get(l1_key)
            if flight is None:
                break
            self._count("single_flight_waits")
            try:
                encoded, raw = await asyncio.shield(flight)
            except _LeaderAborted:
                continue
            return decode_value(encoded) if encoded is not None else raw
        return await self._lead(l1_key, key, loader, ttl, l1_cap, tag, l2, neg_ttl)

    async def _lead(self, l1_key, key, loader, ttl, l1_cap, tag, l2, neg_ttl):
        loop = asyncio.get_running_loop()
        flight: asyncio.Future = loop.create_future()
        self._flights[l1_key] = flight
        outcome: tuple[str | None, Any] = (None, None)
        try:
            token = self._token(tag)
            use_l2 = bool(l2) and self._l2_ready(tag)
            l2_key = self._l2_key(key, tag, token) if use_l2 else ""
            value: Any = _MISS
            encoded: str | None = None
            l2_remaining = 0.0
            if use_l2:
                try:
                    blob = await self._r("GET", l2_key)
                except Exception:
                    blob = None
                if blob is not None:
                    try:
                        encoded, l2_expires = _unpack(bytes(blob))
                        remaining = l2_expires - time.time()
                        if remaining <= 0.5:
                            raise CodecError("expired envelope")
                        value = decode_value(encoded)
                        l2_remaining = remaining
                        self._count("l2_hits")
                        self._family_count(key, 1)
                    except Exception:
                        encoded, value = None, _MISS
                        self._count("decode_errors")
                        self._spawn(self._safe_delete(l2_key))
            if value is _MISS:
                self._count("misses")
                self._family_count(key, 2)
                started = time.monotonic()
                value = await loader()
                self._count("load_ms", int((time.monotonic() - started) * 1000))
                encoded = None
                store_ttl = ttl
                if value is None:
                    store_ttl = min(ttl, float(neg_ttl)) if neg_ttl else 0.0
                if store_ttl > 0:
                    try:
                        encoded = encode_value(value)
                    except CodecError as exc:
                        self._warn_once(f"codec:{key.split(':', 1)[0]}", f"value for '{key}' is not cacheable: {exc}")
                        encoded = None
                    if encoded is not None:
                        l1_exp = min(l1_cap, store_ttl)
                        self._l1.put(l1_key, token, time.monotonic() + self._jitter(l1_exp), encoded)
                        if use_l2:
                            self._write_behind(l2_key, encoded, store_ttl)
            else:
                # value came from Redis: keep it in memory, but never longer than Redis keeps it
                self._l1.put(l1_key, token, time.monotonic() + min(self._jitter(l1_cap), l2_remaining), encoded or "null")
            outcome = (encoded, value)
            return value
        except asyncio.CancelledError:
            if not flight.done():
                flight.set_exception(_LeaderAborted())
                flight.exception()
            raise
        except BaseException as exc:
            if not flight.done():
                flight.set_exception(exc)
                flight.exception()
            raise
        finally:
            self._flights.pop(l1_key, None)
            if not flight.done():
                flight.set_result(outcome)

    @staticmethod
    def _jitter(seconds: float) -> float:
        return max(0.2, seconds * (0.92 + random.random() * 0.16))

    def _write_behind(self, l2_key: str, encoded: str, ttl: float) -> None:
        ttl = max(1.0, float(ttl))
        blob = _pack(encoded, time.time() + ttl)
        if len(blob) > self._max_value_bytes:
            self._count("too_large")
            return
        self._spawn(self._safe_set(l2_key, blob, ttl))

    async def _safe_set(self, key: str, blob: bytes, ttl: float) -> None:
        try:
            await self._r("SET", key, blob, "EX", max(1, int(ttl)))
            self._count("l2_sets")
        except Exception:
            pass

    async def _safe_delete(self, key: str) -> None:
        try:
            await self._r("DEL", key)
        except Exception:
            pass

    # --------------------------------------------------------- explicit invalidation
    def invalidate_key(self, key: str, tag: str | None = None) -> None:
        """Drop one key from memory and (in the background) from Redis."""
        l1_key = f"{tag or '-'}|{key}"
        self._l1.remove(l1_key)
        self._count("invalidations")
        if self._client is not None and self._l2_ready(tag):
            self._spawn(self._safe_delete(self._l2_key(key, tag, self._token(tag))))

    # ------------------------------------------------------------- signed raw blobs
    def blobs_available(self) -> bool:
        return (
            self.enabled and self.started and self._signing_key is not None
            and self.redis_usable() and self._gens.get(_ALL) is not None and _ALL not in self._pending
        )

    def _blob_key(self, key: str) -> str:
        return f"{self.prefix}:g{self._gens.get(_ALL)}:blob:{key}"

    def _sign(self, payload: bytes) -> bytes:
        mac = hmac.new(self._signing_key or b"", b"S1" + payload, hashlib.sha256).digest()[:16]
        return b"S1" + mac + payload

    def _unsign(self, blob: bytes) -> bytes | None:
        if len(blob) < 18 or blob[:2] != b"S1" or self._signing_key is None:
            return None
        mac, payload = blob[2:18], blob[18:]
        good = hmac.new(self._signing_key, b"S1" + payload, hashlib.sha256).digest()[:16]
        return payload if hmac.compare_digest(mac, good) else None

    async def blob_get(self, key: str) -> bytes | None:
        """Return a previously stored signed blob, or ``None`` (miss/outage/tampering)."""
        if not self.blobs_available():
            return None
        self._maybe_refresh()
        try:
            raw = await self._r("GET", self._blob_key(key))
        except Exception:
            return None
        if raw is None:
            self._count("blob_misses")
            return None
        payload = self._unsign(bytes(raw))
        if payload is None:
            self._count("blob_rejected")
            return None
        try:
            data = zlib.decompress(payload)
        except Exception:
            self._count("blob_rejected")
            return None
        self._count("blob_hits")
        return data

    def blob_put(self, key: str, data: bytes, *, ttl: float, nx: bool = False, version: Any = None) -> None:
        """Store a signed+compressed blob in the background (latest write wins).

        ``nx=True`` only fills the key when it is empty (used by readers that
        populate from the database so they can never overwrite a newer value
        written by the owner process).  ``version`` is remembered after a
        successful write, see ``blob_version``.
        """
        if not self.blobs_available():
            return
        self._enqueue_write(key, ("put", (data, float(ttl), bool(nx), version)))

    def blob_drop(self, key: str) -> None:
        if self._client is None or not self.enabled or not self.started:
            return
        self._blob_ok.pop(key, None)
        self._enqueue_write(key, ("drop", None))

    def blob_version(self, key: str) -> Any:
        """Version label of the last blob this process wrote successfully (or ``None``)."""
        return self._blob_ok.get(key)

    def _enqueue_write(self, key: str, op: tuple[str, Any]) -> None:
        self._latest[key] = op
        if key in self._writers:
            return
        self._writers.add(key)
        self._spawn(self._drain_writes(key))

    async def _drain_writes(self, key: str) -> None:
        try:
            while True:
                op = self._latest.pop(key, None)
                if op is None:
                    break
                kind, arg = op
                try:
                    if kind == "put":
                        data, ttl, nx, version = arg
                        if not self.blobs_available():
                            continue
                        packed = self._sign(zlib.compress(data, 3))
                        if len(packed) > self._max_blob_bytes:
                            self._count("too_large")
                            if not nx:
                                await self._r("DEL", self._blob_key(key))
                            continue
                        command: list[Any] = ["SET", self._blob_key(key), packed, "EX", max(1, int(ttl))]
                        if nx:
                            command.append("NX")
                        await self._r(*command)
                        self._count("blob_sets")
                        self._count("blob_bytes", len(packed))
                        if version is not None and not nx:
                            if len(self._blob_ok) > 4000:
                                self._blob_ok.clear()
                            self._blob_ok[key] = version
                    else:
                        if self._gens.get(_ALL) is not None:
                            await self._r("DEL", self._blob_key(key))
                except Exception:
                    if kind == "put":
                        self._blob_ok.pop(key, None)
        finally:
            self._writers.discard(key)

    # ------------------------------------------------------------------ statistics
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "started": self.started,
            "role": self.role,
            "namespace": self.namespace,
            "redis": self._redis_target or None,
            "redis_state": self.redis_state(),
        }

    def redis_state(self) -> str:
        if self._client is None:
            return "not configured"
        if self._breaker.is_open():
            return f"paused ({self._breaker.reason[:80]})"
        if self._daily_limit and self._roll_day() >= self._daily_limit:
            return "paused (daily command budget reached)"
        if self._gens.get(_ALL) is None:
            return "not connected yet"
        return "ok"

    def stats(self) -> dict[str, Any]:
        s = dict(self._stats)
        l1_hits, l2_hits, misses = s.get("l1_hits", 0), s.get("l2_hits", 0), s.get("misses", 0)
        total = l1_hits + l2_hits + misses
        out: dict[str, Any] = {
            "role": self.role,
            "uptime_s": int(time.time() - self._started_at),
            "enabled": self.enabled,
            "redis_state": self.redis_state(),
            "redis": self._client.snapshot() if self._client is not None else None,
            "ewma_ms": round(self._ewma_ms, 1),
            "l1_entries": len(self._l1),
            "l1_mb": round(self._l1.size_bytes / (1 << 20), 2),
            "l1_evictions": self._l1.evictions,
            "lookups": total,
            "hit_rate": round((l1_hits + l2_hits) * 100.0 / total, 1) if total else None,
            "counters": s,
            "families": {k: list(v) for k, v in sorted(self._families.items())},
            "breaker_opens": self._breaker.opens,
            "daily_commands": self._used_today if self._daily_limit else None,
        }
        return out

    async def _publish_stats(self) -> None:
        self._last_publish = time.monotonic()
        try:
            from database import db_stats
            payload = {"at": int(time.time()), "cache": self.stats(), "db": db_stats.snapshot(top=6)}
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
            await self._r("SET", f"{self.prefix}:stats:{self.role}", text.encode("utf-8"), "EX", 3600)
        except Exception:
            pass

    async def read_published_stats(self, role: str) -> dict[str, Any] | None:
        if not self.redis_usable():
            return None
        try:
            raw = await self._r("GET", f"{self.prefix}:stats:{role}")
            return json.loads(raw) if raw else None
        except Exception:
            return None

    async def publish_stats_now(self) -> None:
        if self.redis_usable():
            await self._publish_stats()


# ------------------------------------------------------------------------ helpers
def _detect_role() -> str:
    argv = " ".join(str(a) for a in sys.argv[:3]).lower()
    if "uvicorn" in argv or "miniapp_backend" in argv:
        return "web"
    return "bot"


def _namespace(database_url: str, extra: str = "") -> str:
    """Short id of the *database identity* (never the password)."""
    ident = "no-database"
    if database_url:
        try:
            parts = urlsplit(database_url)
            ident = f"{parts.username or ''}@{(parts.hostname or '').lower()}:{parts.port or ''}{parts.path or ''}"
        except ValueError:
            ident = hashlib.sha256(database_url.encode("utf-8", "ignore")).hexdigest()[:16]
    digest = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:10]
    extra = "".join(ch for ch in str(extra or "").strip() if ch.isalnum() or ch in "-_")[:24]
    return f"{digest}{('-' + extra) if extra else ''}"


def _signing_key(explicit: str, bot_token: str) -> bytes | None:
    secret = (explicit or "").strip() or (bot_token or "").strip()
    if not secret:
        return None
    return hashlib.sha256(b"crickium-cache-signing-v1|" + secret.encode("utf-8")).digest()


# ------------------------------------------------------------------ module facade
_CACHE = Cache()


def get_cache() -> Cache:
    return _CACHE


async def start(**kwargs: Any) -> dict[str, Any]:
    try:
        return await _CACHE.start(**kwargs)
    except Exception as exc:                                    # the bot must start even if the cache cannot
        _log(f"start failed, cache disabled for this run: {type(exc).__name__}: {exc}")
        _CACHE.enabled = False
        _CACHE.started = True
        return _CACHE.status()


async def stop() -> None:
    try:
        await _CACHE.stop()
    except Exception:
        pass


async def get_or_load(key: str, loader: Callable[[], Awaitable[Any]], **kwargs: Any) -> Any:
    return await _CACHE.get_or_load(key, loader, **kwargs)


def invalidate_tag(tag: str) -> None:
    try:
        _CACHE.bump(tag)
    except Exception as exc:
        _log(f"invalidate_tag({tag!r}) failed: {exc!r}")


def invalidate_key(key: str, tag: str | None = None) -> None:
    try:
        _CACHE.invalidate_key(key, tag)
    except Exception as exc:
        _log(f"invalidate_key({key!r}) failed: {exc!r}")


def flush_all() -> None:
    try:
        _CACHE.flush_all()
    except Exception as exc:
        _log(f"flush_all failed: {exc!r}")


async def flush_all_wait(timeout: float = 3.0) -> bool:
    try:
        return await _CACHE.flush_all_wait(timeout)
    except Exception as exc:
        _log(f"flush_all failed: {exc!r}")
        return False


async def blob_get(key: str) -> bytes | None:
    try:
        return await _CACHE.blob_get(key)
    except Exception:
        return None


def blob_put(key: str, data: bytes, *, ttl: float, nx: bool = False, version: Any = None) -> None:
    try:
        _CACHE.blob_put(key, data, ttl=ttl, nx=nx, version=version)
    except Exception:
        pass


def blob_version(key: str) -> Any:
    try:
        return _CACHE.blob_version(key)
    except Exception:
        return None


def blob_drop(key: str) -> None:
    try:
        _CACHE.blob_drop(key)
    except Exception:
        pass


def stats() -> dict[str, Any]:
    return _CACHE.stats()
