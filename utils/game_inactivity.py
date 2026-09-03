from __future__ import annotations

import asyncio
import html
import json
from typing import Any, Awaitable, Callable

from app import app
from database.query import execute, fetchrow
from utils.mentions import mention_html

_TIMEOUT_SECONDS = 180
_REMINDER_SECONDS = (120, 60)

_tasks: dict[str, asyncio.Task] = {}
_locks: dict[str, asyncio.Lock] = {}
_generations: dict[str, int] = {}


def _key(engine: str, match_id: int, user_id: int) -> str:
    return f"{engine}:{int(match_id)}:{int(user_id)}"


def _lock_key(engine: str, match_id: int) -> str:
    return f"{engine}:{int(match_id)}"


def _lock_for(engine: str, match_id: int) -> asyncio.Lock:
    return _locks.setdefault(_lock_key(engine, match_id), asyncio.Lock())


def cancel(engine: str, match_id: int, user_id: int) -> None:
    key = _key(engine, match_id, user_id)
    _generations[key] = _generations.get(key, 0) + 1
    task = _tasks.pop(key, None)
    if task and not task.done():
        task.cancel()


def cancel_match(engine: str, match_id: int) -> None:
    prefix = f"{engine}:{int(match_id)}:"
    for key in list(_tasks):
        if key.startswith(prefix):
            uid = key.rsplit(":", 1)[-1]
            cancel(engine, match_id, int(uid))


def _display(user: dict[str, Any]) -> str:
    return mention_html(int(user.get("id") or 0), user.get("username"), user.get("first_name"))


async def _send_reminder(chat_id: int, user: dict[str, Any], remaining: int) -> None:
    minutes = remaining // 60
    await app.send_message(
        chat_id,
        f"⏳ <b>{_display(user)}</b>, you have <b>{minutes} minute{'s' if minutes != 1 else ''}</b> remaining to respond.\nPlease make your move to continue the game. 🏏",
        parse_mode="HTML",
    )


async def _finish_timeout(engine: str, match_id: int, timed_out_user_id: int, callback: Callable[[dict, dict, dict], Awaitable[None]]) -> None:
    lock = _lock_for(engine, match_id)
    async with lock:
        current = await _current_match(engine, match_id)
        expected = await _expected_users(engine, current)
        if not current or int(timed_out_user_id) not in expected:
            return
        current = dict(current)
        current["_engine"] = engine
        other_ids = [uid for uid in expected if int(uid) != int(timed_out_user_id)]
        participants = [int(current.get("challenger_id") or 0), int(current.get("opponent_id") or 0)]
        winner_id = other_ids[0] if other_ids else next((uid for uid in participants if uid != int(timed_out_user_id)), 0)
        if not winner_id:
            return

        # Atomic terminal-state claim prevents two simultaneous timers from
        # awarding the same match twice.
        claimed = await fetchrow(
            _status_claim_sql(engine),
            match_id,
        )
        if not claimed:
            return
        await callback(current, {
            "id": int(timed_out_user_id),
            "username": current.get("opponent_username") if int(timed_out_user_id) == int(current.get("opponent_id") or 0) else current.get("challenger_username"),
            "first_name": current.get("opponent_name") if int(timed_out_user_id) == int(current.get("opponent_id") or 0) else current.get("challenger_name"),
        }, {
            "id": int(winner_id),
            "username": current.get("opponent_username") if int(winner_id) == int(current.get("opponent_id") or 0) else current.get("challenger_username"),
            "first_name": current.get("opponent_name") if int(winner_id) == int(current.get("opponent_id") or 0) else current.get("challenger_name"),
        })


def _status_claim_sql(engine: str) -> str:
    if engine == "PLAY":
        table, statuses = "play_matches", "ARRAY['accepted','pitch_selected','toss_done','lineup']"
    elif engine == "PLAYINT":
        table, statuses = "playint_matches", "ARRAY['accepted','team_selection','pitch_selected','toss_done','lineup']"
    else:
        table, statuses = "playipl_matches", "ARRAY['accepted','team_selection','pitch_selected','toss_done','lineup','live']"
    return f"UPDATE {table} SET status='timed_out' WHERE match_id=$1 AND status=ANY({statuses}::text[]) RETURNING *;"


async def _current_match(engine: str, match_id: int):
    if engine == "PLAY":
        return await fetchrow("SELECT * FROM play_matches WHERE match_id=$1;", match_id)
    if engine == "PLAYINT":
        return await fetchrow("SELECT * FROM playint_matches WHERE match_id=$1;", match_id)
    return await fetchrow("SELECT * FROM playipl_matches WHERE match_id=$1;", match_id)


async def _expected_users(engine: str, match: Any) -> list[int]:
    if not match:
        return []
    m = dict(match)
    if engine == "PLAY":
        try:
            from engines.play_runtime import get_session
            session = get_session(int(m["match_id"]))
        except Exception:
            session = None
    elif engine == "PLAYINT":
        try:
            from engines.playint_runtime import get_playint_session
            session = get_playint_session(int(m["match_id"]))
        except Exception:
            session = None
    else:
        try:
            from engines.playipl_runtime import get_playipl_session
            session = get_playipl_session(int(m["match_id"]))
        except Exception:
            session = None

    if session is not None:
        if engine == "PLAYIPL":
            impact = (session.match.get("_impact") or {})
            pending = []
            for code, state in impact.items():
                if state.get("done"):
                    continue
                try:
                    owner = int(session.match["challenger_id"] if code == session.match.get("challenger_team_code") else session.match["opponent_id"])
                    pending.append(owner)
                except Exception:
                    pass
            if pending:
                return pending

        stage = str(session.stage or "")
        if stage in {"choose_bowler", "choose_tactic"}:
            return [int(session.bowling_team_id)]
        if stage == "choose_strategy":
            return [int(session.batting_team_id)]

    c = int(m.get("challenger_id") or 0)
    o = int(m.get("opponent_id") or 0)
    if engine == "PLAY":
        status = str(m.get("status") or "")
        if status == "accepted": return [c]
        if status == "pitch_selected": return [o]
        if status == "toss_done": return [int(m.get("toss_winner_id") or 0)] if m.get("toss_winner_id") else []
        return []

    # A pending (not yet accepted) or declined challenge has no one "on the
    # clock" - team selection, pitch, toss etc. only begin once the
    # opponent accepts, so no inactivity timer should exist before then.
    # (The PLAY branch above already gates on status the same way; this
    # mirrors it for PLAYINT/PLAYIPL instead of falling straight into the
    # team-code checks below regardless of whether the challenge was ever
    # accepted.)
    status = str(m.get("status") or "")
    if status in {"", "pending", "declined", "expired"}:
        return []

    cc, oc = m.get("challenger_team_code"), m.get("opponent_team_code")
    if not cc: return [c]
    if not oc: return [o]
    cxi = m.get("challenger_xi_confirmed")
    oxi = m.get("opponent_xi_confirmed")
    if not cxi or not oxi:
        return [uid for uid, ok in ((c, cxi), (o, oxi)) if not ok]
    if not m.get("pitch"): return [c]
    if status == "pitch_selected": return [o]
    if status == "toss_done": return [int(m.get("toss_winner_id") or 0)] if m.get("toss_winner_id") else []
    return []


def _signature_value(v: Any):
    if hasattr(v, "__dict__"):
        return str(v)
    if isinstance(v, (dict, list, tuple)):
        try:
            return json.dumps(v, sort_keys=True, default=str)
        except Exception:
            return repr(v)
    return v


async def game_signature(engine: str, match_id: int):
    m = await _current_match(engine, match_id)
    if not m:
        return None
    md = dict(m)
    session_state = None
    try:
        if engine == "PLAY":
            from engines.play_runtime import get_session
            s = get_session(match_id)
        elif engine == "PLAYINT":
            from engines.playint_runtime import get_playint_session
            s = get_playint_session(match_id)
        else:
            from engines.playipl_runtime import get_playipl_session
            s = get_playipl_session(match_id)
        if s is not None:
            session_state = {
                "stage": getattr(s, "stage", None),
                "bowler": getattr(getattr(s, "current_bowler", None), "get", lambda *_: None)("player_id") if getattr(s, "current_bowler", None) else None,
                "tactic": getattr(s, "current_tactic", None),
                "strategy": getattr(s, "current_strategy", None),
                "innings": getattr(getattr(getattr(s, "innings", None), "score", None), "legal_balls", None),
                "impact": _signature_value(md.get("_impact") if "_impact" in md else getattr(s, "match", {}).get("_impact") if s else None),
            }
            if s is not None and engine == "PLAYIPL":
                session_state["impact"] = _signature_value(getattr(s, "match", {}).get("_impact"))
    except Exception:
        pass
    fields = ["status", "challenger_team_code", "opponent_team_code", "challenger_xi", "opponent_xi",
              "challenger_xi_confirmed", "opponent_xi_confirmed", "pitch", "toss_winner_id", "decision"]
    return tuple(_signature_value(md.get(f)) for f in fields) + (_signature_value(session_state),)


async def sync_after_change(engine: str, match_id: int, actor_id: int | None = None) -> None:
    match = await _current_match(engine, match_id)
    if not match or str(match.get("status") or "") in {"timed_out", "completed", "declined", "ended", "expired"}:
        cancel_match(engine, match_id)
        return
    expected = await _expected_users(engine, match)
    expected_set = set(int(x) for x in expected if int(x or 0))
    for key in list(_tasks):
        prefix = f"{engine}:{int(match_id)}:"
        if key.startswith(prefix):
            uid = int(key.rsplit(":", 1)[-1])
            if uid not in expected_set:
                cancel(engine, match_id, uid)
    for uid in expected_set:
        key = _key(engine, match_id, uid)
        if key not in _tasks or (actor_id is not None and int(actor_id) == uid):
            user = _user_from_match(match, uid)
            _start(engine, match_id, int(match["chat_id"]), user)


def _user_from_match(match, uid: int) -> dict[str, Any]:
    if int(uid) == int(match.get("challenger_id") or 0):
        return {"id": uid, "username": match.get("challenger_username"), "first_name": match.get("challenger_name")}
    return {"id": uid, "username": match.get("opponent_username"), "first_name": match.get("opponent_name")}


def _start(engine: str, match_id: int, chat_id: int, user: dict[str, Any]) -> None:
    key = _key(engine, match_id, int(user["id"]))
    cancel(engine, match_id, int(user["id"]))
    generation = _generations.get(key, 0)

    async def precise_runner():
        try:
            for remaining in (120, 60):
                await asyncio.sleep(60 if remaining == 120 else 60)
                if _generations.get(key) != generation:
                    return
                current = await _current_match(engine, match_id)
                expected = await _expected_users(engine, current)
                if int(user["id"]) not in expected:
                    return
                await _send_reminder(chat_id, user, remaining)
            await asyncio.sleep(60)
            if _generations.get(key) != generation:
                return
            current = await _current_match(engine, match_id)
            expected = await _expected_users(engine, current)
            if int(user["id"]) not in expected:
                return
            await _finish_timeout(engine, match_id, int(user["id"]), _default_timeout_callback)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            print(f"[inactivity] timer failed for {engine}:{match_id}:{user['id']}: {exc!r}")
        finally:
            _tasks.pop(key, None)

    _tasks[key] = asyncio.create_task(precise_runner())


async def _default_timeout_callback(match: dict, timed_out: dict, winner: dict) -> None:
    engine = _engine_for_match(match)
    from services.match_rewards import award_timeout_rewards, build_timeout_message
    await award_timeout_rewards(int(winner["id"]), int(timed_out["id"]))
    await _clear_engine_session(engine, int(match["match_id"]))
    await _clear_engine_messages(match)
    winner_mention = _display(winner)
    loser_mention = _display(timed_out)
    await app.send_message(
        int(match["chat_id"]),
        build_timeout_message(winner_mention, loser_mention, engine),
        parse_mode="HTML",
    )


def _engine_for_match(match):
    return str(match.get("_engine") or "")


async def _clear_engine_messages(match):
    chat_id = int(match.get("chat_id") or 0)
    mids = {match.get("message_id")}
    for mid in list(mids):
        if mid:
            try: await app.edit_message_text(chat_id, int(mid), "<b>⏰ Match ended due to inactivity.</b>", parse_mode="HTML", reply_markup={"inline_keyboard": []})
            except Exception: pass


async def _clear_engine_session(engine: str, match_id: int):
    try:
        if engine == "PLAY":
            from engines.play_runtime import get_session, clear_session
            s = get_session(match_id)
            await _delete_session_messages(s)
            clear_session(match_id)
        elif engine == "PLAYINT":
            from engines.playint_runtime import get_playint_session, clear_playint_session
            s = get_playint_session(match_id)
            await _delete_session_messages(s)
            clear_playint_session(match_id)
        elif engine == "PLAYIPL":
            from engines.playipl_runtime import get_playipl_session, clear_playipl_session
            s = get_playipl_session(match_id)
            await _delete_session_messages(s)
            clear_playipl_session(match_id)
    except Exception as exc:
        print(f"[inactivity] session cleanup failed {engine}:{match_id}: {exc!r}")


async def _delete_session_messages(session):
    if not session:
        return
    chat_id = int(getattr(session, "chat_id", 0) or 0)
    mids = {getattr(session, "live_message_id", None), getattr(session, "ready_message_id", None), getattr(session, "message_id", None)}
    for mid in mids:
        if mid:
            try: await app.delete_message(chat_id, int(mid))
            except Exception: pass
