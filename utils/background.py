"""Fire-and-forget task helper that keeps a strong reference until completion.

``asyncio.create_task()`` only holds a weak reference to the task, so a task whose
return value is dropped can be garbage-collected mid-flight. ``spawn`` keeps the task
in a set until it finishes and logs any exception instead of silently losing it.
"""
from __future__ import annotations

import asyncio
from typing import Any, Coroutine

_TASKS: set[asyncio.Task] = set()


def _done(task: asyncio.Task) -> None:
    _TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        print(f"[background] task {task.get_name()!r} failed: {exc!r}")


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    _TASKS.add(task)
    task.add_done_callback(_done)
    return task
