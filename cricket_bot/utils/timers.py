"""
Reusable per-stage timer system.

Used for:
- Challenge accept/decline window (60s, no reminders - just expires)
- Toss call, Bat/Bowl decision, and (later) delivery/shot choice stages
  (default 90s, with reminder pings at "1 minute remaining" and
  "30 seconds remaining", then an auto-timeout action if still unresolved)

Each timer is keyed by (scope, id) e.g. ("toss_call", challenge_id), so a
new stage can start its own timer, and a user's action can cancel the
timer for that specific stage early.
"""

import asyncio

DEFAULT_STAGE_TIMEOUT = 90          # seconds, for interactive game-choice stages
REMINDER_CHECKPOINTS = [60, 30]     # "seconds remaining" marks to ping at

_active_timers: dict[str, asyncio.Task] = {}


def _key(scope: str, id_) -> str:
    return f"{scope}:{id_}"


async def _timer_coroutine(scope, id_, total_seconds, on_reminder, on_timeout):
    checkpoints = [c for c in REMINDER_CHECKPOINTS if c < total_seconds]
    elapsed = 0
    try:
        while elapsed < total_seconds:
            await asyncio.sleep(1)
            elapsed += 1
            remaining = total_seconds - elapsed
            if remaining in checkpoints:
                print(f"[timers] {scope}:{id_} - {remaining}s remaining, sending reminder.")
                try:
                    await on_reminder(remaining)
                except Exception as e:
                    print(f"[timers] !! on_reminder failed for {scope}:{id_}: {e!r}")

        print(f"[timers] {scope}:{id_} - time's up, running auto-timeout action.")
        try:
            await on_timeout()
        except Exception as e:
            print(f"[timers] !! on_timeout failed for {scope}:{id_}: {e!r}")
    except asyncio.CancelledError:
        print(f"[timers] {scope}:{id_} - cancelled (user acted in time).")
        raise
    finally:
        _active_timers.pop(_key(scope, id_), None)


def start_timer(scope: str, id_, on_reminder, on_timeout, total_seconds: int = DEFAULT_STAGE_TIMEOUT):
    """Starts a timer for (scope, id_). Cancels any existing timer for the
    same key first (so restarting a stage doesn't double-fire)."""
    cancel_timer(scope, id_)
    key = _key(scope, id_)
    task = asyncio.create_task(
        _timer_coroutine(scope, id_, total_seconds, on_reminder, on_timeout)
    )
    _active_timers[key] = task
    print(f"[timers] Started timer '{key}' for {total_seconds}s.")
    return task


def cancel_timer(scope: str, id_):
    """Cancel a timer early - call this the moment the user takes the
    expected action, so reminders/auto-timeout don't fire afterwards."""
    key = _key(scope, id_)
    task = _active_timers.pop(key, None)
    if task and not task.done():
        task.cancel()
        print(f"[timers] Cancelled timer '{key}'.")
