from __future__ import annotations

from typing import Any


def snapshot(innings: dict[str, Any]) -> dict[str, Any]:
    balls = int(innings.get("legal_balls") or 0)
    snap = dict(innings)
    snap["over_text"] = f"{balls // 6}.{balls % 6}"
    return snap


def innings_text(innings: dict[str, Any], target: int | None = None) -> str:
    score = f"{int(innings.get('runs') or 0)}/{int(innings.get('wickets') or 0)}"
    overs = innings.get("over_text") or "0.0"
    extra = f" | 🎯 Target {target}" if target is not None else ""
    return f"{score} ({overs} ov){extra}"
