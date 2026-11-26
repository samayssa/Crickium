import time

from app import app
from handlers.registry import register
from database.playso_repo import get_active_match_for_user as playso_active
from engines import play_runtime, playint_runtime, playipl_runtime, playwpl_runtime, playwbbl_runtime, playbbl_runtime, playilt_runtime
from services.lineup_rich import send_lineup
from services.match_guard import user_in_match
from services.match_score_view import build_session_view, build_so_view
from utils.pause_state import is_paused

COOLDOWN_SECONDS = 3
_last_use: dict[int, float] = {}

_ENGINES = (
    ("PLAY", "PLAY", play_runtime._SESSIONS),
    ("PLAYINT", "PLAYINT", playint_runtime._PLAYINT_SESSIONS),
    ("PLAYIPL", "PLAYIPL", playipl_runtime._PLAYIPL_SESSIONS),
    ("PLAYWPL", "PLAYWPL", playwpl_runtime._PLAYWPL_SESSIONS),
    ("PLAYWBBL", "PLAYWBBL", playwbbl_runtime._PLAYWBBL_SESSIONS),
    ("PLAYBBL", "PLAYBBL", playbbl_runtime._PLAYBBL_SESSIONS),
    ("PLAYILT", "PLAYILT", playilt_runtime._PLAYILT_SESSIONS),
)


def _throttled(user_id: int) -> bool:
    now = time.monotonic()
    if now - _last_use.get(user_id, 0.0) < COOLDOWN_SECONDS:
        return True
    if len(_last_use) > 2000:
        cutoff = now - 60
        for key in [k for k, v in _last_use.items() if v < cutoff]:
            _last_use.pop(key, None)
    _last_use[user_id] = now
    return False


def _find_session(user_id: int):
    found = []
    for engine, label, store in _ENGINES:
        for match_id, session in list(store.items()):
            match = session.match or {}
            if user_id in (int(match.get("challenger_id") or 0), int(match.get("opponent_id") or 0)):
                found.append((engine, label, match_id, session))
    return found


async def _reply(chat_id, thread_id, text):
    await app.send_message(chat_id, text, parse_mode="HTML")


@register("matchscore")
async def matchscore_command(message):
    chat_id = message["chat"]["id"]
    thread_id = message.get("message_thread_id")
    user_id = int((message.get("from") or {}).get("id") or 0)
    if not user_id or _throttled(user_id):
        return
    if message["chat"].get("type") == "private":
        await _reply(chat_id, thread_id, "<b>⚠️ Use /matchscore in the group where your match is running.</b>")
        return

    mine = _find_session(user_id)
    here = [item for item in mine if int(item[3].chat_id) == int(chat_id)]
    if here:
        engine, label, match_id, session = here[0]
        view = build_session_view(session, label, is_paused(engine, match_id))
        await send_lineup(chat_id, view.rich(), view.plain(), message_thread_id=thread_id)
        return
    if mine:
        await _reply(chat_id, thread_id, "<b>⚠️ Your match is running in a different group. Send /matchscore there.</b>")
        return

    try:
        so_match = await playso_active(user_id)
    except Exception as exc:
        print(f"[matchscore] playso lookup failed: {exc!r}")
        so_match = None
    if so_match:
        if int(so_match.get("chat_id") or 0) != int(chat_id):
            await _reply(chat_id, thread_id, "<b>⚠️ Your match is running in a different group. Send /matchscore there.</b>")
            return
        state = so_match.get("state") or {}
        if so_match.get("status") == "live" and state.get("batting_user"):
            view = build_so_view(so_match, state)
            await send_lineup(chat_id, view.rich(), view.plain(), message_thread_id=thread_id)
            return
        await _reply(chat_id, thread_id, "<b>⏳ Your match has not started yet. Try again once the first ball is bowled.</b>")
        return

    if await user_in_match(user_id):
        await _reply(chat_id, thread_id, "<b>⏳ Your match has not started yet. Try again once the first ball is bowled.</b>")
        return
    await _reply(chat_id, thread_id, "<b>🚫 You are not in any game.</b>")
