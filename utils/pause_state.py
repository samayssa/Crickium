from __future__ import annotations

_paused: dict[tuple[str, int], int] = {}
_queued: dict[tuple[str, int], int] = {}


def _key(engine: str, match_id: int) -> tuple[str, int]:
    return str(engine).upper(), int(match_id)


def block(engine: str, match_id: int, chat_id: int) -> None:
    _paused[_key(engine, match_id)] = int(chat_id)


def release(engine: str, match_id: int) -> None:
    key = _key(engine, match_id)
    _paused.pop(key, None)
    _queued.pop(key, None)


def is_paused(engine: str, match_id: int) -> bool:
    return _key(engine, match_id) in _paused


def queue(engine: str, match_id: int, chat_id: int) -> None:
    key = _key(engine, match_id)
    _paused[key] = int(chat_id)
    _queued[key] = int(chat_id)


def unqueue(engine: str, match_id: int) -> None:
    _queued.pop(_key(engine, match_id), None)


def has_queued() -> bool:
    return bool(_queued)


def queued_keys() -> list[tuple[str, int]]:
    return list(_queued)


def reset() -> None:
    _paused.clear()
    _queued.clear()
