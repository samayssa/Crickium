"""Lightweight database traffic counters (diagnostics only).

Shows *which queries* move the most data out of the database, so egress and
compute problems can be found with numbers instead of guesses.  Counters live
in memory per process, are bounded, and every function here swallows its own
errors: instrumentation must never change a query result or raise.
"""
from __future__ import annotations

import os
import time
from typing import Any

_ENABLED = os.getenv("DB_STATS", "1").strip().lower() not in {"0", "false", "no", "off"}
_MAX_SIGNATURES = 300
_SAMPLE_ROWS = 200

_started = time.time()
_totals = {"calls": 0, "writes": 0, "rows": 0, "bytes": 0, "ms": 0.0}
_by_sig: dict[str, list[float]] = {}          # sig -> [calls, rows, bytes, ms]


def _signature(sql: str) -> str:
    text = " ".join(str(sql).split())
    return text[:96]


def _value_size(value: Any) -> int:
    if value is None:
        return 1
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (bool, int, float)):
        return 8
    return 24


def _row_size(row: Any) -> int:
    try:
        if hasattr(row, "values"):
            return sum(_value_size(v) for v in row.values())
        if isinstance(row, (list, tuple)):
            return sum(_value_size(v) for v in row)
    except Exception:
        return 0
    return _value_size(row)


def _approx_bytes(result: Any) -> tuple[int, int]:
    """Return (rows, approx_bytes) for a query result."""
    if result is None:
        return 0, 0
    if isinstance(result, list):
        rows = len(result)
        if rows == 0:
            return 0, 0
        sample = result[:_SAMPLE_ROWS]
        size = sum(_row_size(r) for r in sample)
        if rows > len(sample):
            size = int(size * rows / len(sample))
        return rows, size
    if hasattr(result, "values"):
        return 1, _row_size(result)
    if isinstance(result, str):
        return 0, 0          # command tag of execute()
    return 1, _value_size(result)


def record(sql: str, result: Any, started: float, *, write: bool = False) -> None:
    """Count one finished statement. ``started`` is a ``time.monotonic()`` stamp."""
    if not _ENABLED:
        return
    try:
        elapsed_ms = (time.monotonic() - started) * 1000.0
        rows, size = (0, 0) if write else _approx_bytes(result)
        _totals["calls"] += 1
        if write:
            _totals["writes"] += 1
        _totals["rows"] += rows
        _totals["bytes"] += size
        _totals["ms"] += elapsed_ms
        sig = _signature(sql)
        row = _by_sig.get(sig)
        if row is None:
            if len(_by_sig) >= _MAX_SIGNATURES:
                sig = "(other)"
                row = _by_sig.setdefault(sig, [0, 0, 0, 0.0])
            else:
                row = _by_sig.setdefault(sig, [0, 0, 0, 0.0])
        row[0] += 1
        row[1] += rows
        row[2] += size
        row[3] += elapsed_ms
    except Exception:
        pass


def snapshot(top: int = 8) -> dict[str, Any]:
    try:
        ranked = sorted(_by_sig.items(), key=lambda kv: (kv[1][2], kv[1][0]), reverse=True)[: max(1, top)]
        by_calls = sorted(_by_sig.items(), key=lambda kv: kv[1][0], reverse=True)[: max(1, top)]
        return {
            "since_s": int(time.time() - _started),
            "calls": _totals["calls"],
            "writes": _totals["writes"],
            "rows": _totals["rows"],
            "bytes": _totals["bytes"],
            "avg_ms": round(_totals["ms"] / max(1, _totals["calls"]), 1),
            "top_bytes": [[sig, int(v[0]), int(v[2]), round(v[3] / max(1, v[0]), 1)] for sig, v in ranked],
            "top_calls": [[sig, int(v[0]), int(v[2]), round(v[3] / max(1, v[0]), 1)] for sig, v in by_calls],
        }
    except Exception:
        return {}


def reset() -> None:
    _by_sig.clear()
    for key in _totals:
        _totals[key] = 0 if key != "ms" else 0.0
