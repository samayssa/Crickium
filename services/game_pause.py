from __future__ import annotations

import asyncio
import html
import importlib
import traceback
from typing import Any

from app import app
from database import game_pause_repo as repo
from utils import pause_state
from utils.timers import cancel_timer, start_timer

ENGINE_ORDER = ("PLAYSO", "PLAY", "PLAYINT", "PLAYIPL", "PLAYWPL", "PLAYWBBL", "PLAYBBL", "PLAYILT")
ENGINE_LABELS = {
    "PLAY": "PLAY",
    "PLAYINT": "PLAYINT",
    "PLAYIPL": "PLAYIPL",
    "PLAYWPL": "PLAYWPL",
    "PLAYWBBL": "PLAYWBBL",
    "PLAYBBL": "PLAYBBL",
    "PLAYILT": "PLAYILT",
    "PLAYSO": "PLAYSO (Super Over)",
}
_REPO_MODULES = {
    "PLAY": "database.play_repo",
    "PLAYINT": "database.playint_repo",
    "PLAYIPL": "database.playipl_repo",
    "PLAYWPL": "database.playwpl_repo",
    "PLAYWBBL": "database.playwbbl_repo",
    "PLAYBBL": "database.playbbl_repo",
    "PLAYILT": "database.playilt_repo",
    "PLAYSO": "database.playso_repo",
}
_CHALLENGE_MODULES = {
    "PLAY": ("handlers.play.challenge", "buttons.play_buttons"),
    "PLAYINT": ("handlers.playint.challenge", "buttons.playint_buttons"),
    "PLAYIPL": ("handlers.playipl.challenge", "buttons.playipl_buttons"),
    "PLAYWPL": ("handlers.playwpl.challenge", "buttons.playwpl_buttons"),
    "PLAYWBBL": ("handlers.playwbbl.challenge", "buttons.playwbbl_buttons"),
    "PLAYBBL": ("handlers.playbbl.challenge", "buttons.playbbl_buttons"),
    "PLAYILT": ("handlers.playilt.challenge", "buttons.playilt_buttons"),
    "PLAYSO": ("handlers.playso.challenge", "buttons.playso_buttons"),
}
_LIVE_STATUSES = {
    "pending", "accepted", "team_selection", "pitch_selected",
    "toss_done", "lineup", "live", "innings_break",
}
REQUEST_MAX_AGE_SECONDS = 600
RESUME_DELAY_SECONDS = 3
QUEUE_POLL_SECONDS = 4
NO_KEYBOARD = {"inline_keyboard": []}

_chat_locks: dict[int, asyncio.Lock] = {}
_worker_task: asyncio.Task | None = None


def repo_for(engine: str):
    return importlib.import_module(_REPO_MODULES[str(engine).upper()])


def tag(user_id: int, name: str | None) -> str:
    return f'<a href="tg://user?id={int(user_id)}">{html.escape(name or "Player")}</a>'


def player_tags(match: dict) -> tuple[str, str]:
    return (
        tag(match.get("challenger_id"), match.get("challenger_name")),
        tag(match.get("opponent_id"), match.get("opponent_name")),
    )


def _lock_for(chat_id: int) -> asyncio.Lock:
    return _chat_locks.setdefault(int(chat_id), asyncio.Lock())


async def load_match(engine: str, match_id: int) -> dict | None:
    raw = await repo_for(engine).get_match(int(match_id))
    return dict(raw) if raw else None


def is_live_status(match: dict | None) -> bool:
    return bool(match) and str(match.get("status") or "") in _LIVE_STATUSES


async def find_user_game(user_id: int, chat_id: int) -> tuple[str | None, dict | None]:
    for engine in ENGINE_ORDER:
        try:
            raw = await repo_for(engine).get_active_match_for_user(int(user_id))
        except Exception as exc:
            print(f"[pause] lookup failed for {engine}: {exc!r}")
            continue
        if raw and int(dict(raw).get("chat_id") or 0) == int(chat_id):
            return engine, dict(raw)
    return None, None


async def is_tournament_match(engine: str, match_id: int) -> bool:
    try:
        from database import tournament_repo
        return bool(await tournament_repo.fixture_by_match(engine, int(match_id)))
    except Exception as exc:
        print(f"[pause] tournament lookup failed for {engine}:{match_id}: {exc!r}")
        return True


async def chat_busy(chat_id: int) -> bool:
    for engine in ENGINE_ORDER:
        try:
            if await repo_for(engine).get_active_match_in_chat(int(chat_id)):
                return True
        except Exception as exc:
            print(f"[pause] chat lookup failed for {engine}: {exc!r}")
    return False


def _origin_of(engine: str, match: dict) -> tuple[str, int] | None:
    if engine != "PLAYSO":
        return None
    state = match.get("state") or {}
    origin_engine = str(state.get("origin_engine") or "").upper()
    origin_id = int(state.get("origin_match_id") or 0)
    if origin_engine in _REPO_MODULES and origin_id:
        return origin_engine, origin_id
    return None


async def apply_pause(row: dict, match: dict) -> None:
    engine = str(row["engine"]).upper()
    match_id = int(row["match_id"])
    chat_id = int(row["chat_id"])
    pause_state.block(engine, match_id, chat_id)
    origin = _origin_of(engine, match)
    if origin:
        await repo.add_linked(
            origin[0], origin[1], chat_id, int(row["challenger_id"]), int(row["opponent_id"]),
            int(row["requested_by"]), engine, match_id,
        )
        pause_state.block(origin[0], origin[1], chat_id)
    try:
        from utils.game_inactivity import cancel_match
        cancel_match(engine, match_id)
        if origin:
            cancel_match(origin[0], origin[1])
    except Exception as exc:
        print(f"[pause] could not stop inactivity timer for {engine}:{match_id}: {exc!r}")
    cancel_timer("playso_challenge" if engine == "PLAYSO" else "challenge", match_id)


def queue_text(match: dict) -> str:
    a, b = player_tags(match)
    return (
        "<b>⏳ GAME QUEUED</b>\n\n"
        f"{a} and {b}, another game is currently running in this group.\n"
        "<blockquote>As soon as it ends, your paused game will start automatically "
        "from exactly where you left off.</blockquote>"
    )


def resuming_text(engine: str, match: dict) -> str:
    a, b = player_tags(match)
    return (
        "<b>🔄 MATCH RESUMING</b>\n\n"
        f"{a} ⚔️ {b}\n"
        "<blockquote>"
        f"🎮 Game engine ➤ {ENGINE_LABELS.get(engine, engine)}\n"
        f"⏳ Picking up right where you paused in {RESUME_DELAY_SECONDS} seconds..."
        "</blockquote>"
    )


async def _expire_challenge(engine: str, match_id: int, chat_id: int) -> None:
    current = await load_match(engine, match_id)
    if not current or str(current.get("status") or "") != "pending":
        return
    message_id = current.get("message_id")
    if engine == "PLAYSO":
        await repo_for(engine).update_locked(int(match_id), {"pending"}, lambda d, s: ({"expired": True}, "expired"))
        text = "<b>⏳ PLAYSO challenge expired.\n\nNo Super Over was started.</b>"
    else:
        await repo_for(engine).update_status(int(match_id), "expired")
        text = "<b>⌛ CHALLENGE EXPIRED\n\nNobody responded in time. The challenge has expired.</b>"
    if message_id:
        try:
            await app.edit_message_text(int(chat_id), int(message_id), text, parse_mode="HTML", reply_markup=NO_KEYBOARD)
        except Exception as exc:
            print(f"[pause] could not edit expired challenge for {engine}:{match_id}: {exc!r}")


async def resend_challenge(engine: str, match: dict) -> None:
    chat_id = int(match["chat_id"])
    match_id = int(match["match_id"])
    old_id = match.get("message_id")
    if old_id:
        try:
            await app.delete_message(chat_id, int(old_id))
        except Exception as exc:
            print(f"[pause] could not delete old challenge for {engine}:{match_id}: {exc!r}")
    from utils.mentions import mention_html
    text_module, button_module = _CHALLENGE_MODULES[engine]
    text_fn = importlib.import_module(text_module)._challenge_text
    keyboard_fn = importlib.import_module(button_module).challenge_keyboard
    a = mention_html(match.get("challenger_id"), match.get("challenger_username"), match.get("challenger_name"))
    b = mention_html(match.get("opponent_id"), match.get("opponent_username"), match.get("opponent_name"))
    sent = await app.send_message(chat_id, text_fn(a, b), parse_mode="HTML", reply_markup=keyboard_fn(match_id))
    await repo_for(engine).set_message_id(match_id, int(sent["message_id"]))

    async def on_reminder(remaining):
        return None

    async def on_timeout():
        await _expire_challenge(engine, match_id, chat_id)

    start_timer("playso_challenge" if engine == "PLAYSO" else "challenge", match_id, on_reminder, on_timeout, total_seconds=60)


async def _reopen(engine: str, row: dict, resumer_id: int) -> None:
    match_id = int(row["match_id"])
    chat_id = int(row["chat_id"])
    match = await load_match(engine, match_id)
    if not is_live_status(match):
        await app.send_message(chat_id, "<b>⚠️ This paused game has already ended, so there is nothing to resume.</b>", parse_mode="HTML")
        return
    if str(match.get("status") or "") == "pending":
        await resend_challenge(engine, match)
    else:
        from handlers.resume import resume_active
        await resume_active({"from": {"id": int(resumer_id)}, "chat": {"id": chat_id}})
    from utils.game_inactivity import sync_after_change
    await sync_after_change(engine, match_id, int(resumer_id))


async def run_resume(row: dict, resumer_id: int) -> None:
    engine = str(row["engine"]).upper()
    match_id = int(row["match_id"])
    chat_id = int(row["chat_id"])
    match = await load_match(engine, match_id) or {
        "challenger_id": row["challenger_id"], "opponent_id": row["opponent_id"],
    }
    try:
        await app.send_message(chat_id, resuming_text(engine, match), parse_mode="HTML")
    except Exception as exc:
        print(f"[pause] could not announce resume for {engine}:{match_id}: {exc!r}")
    await asyncio.sleep(RESUME_DELAY_SECONDS)
    linked = await repo.linked_rows(engine, match_id)
    pause_state.release(engine, match_id)
    for extra in linked:
        pause_state.release(extra["engine"], int(extra["match_id"]))
    try:
        await _reopen(engine, row, resumer_id)
    except Exception:
        traceback.print_exc()
        await repo.restore_paused(engine, match_id)
        pause_state.block(engine, match_id, chat_id)
        for extra in linked:
            pause_state.block(extra["engine"], int(extra["match_id"]), chat_id)
        try:
            await app.send_message(
                chat_id,
                "<b>⚠️ Something went wrong while resuming, so the game stays paused.\nSend /resume to try again.</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return
    await repo.delete_with_links(engine, match_id)


async def handle_resume_command(message: dict) -> bool:
    user_id = int((message.get("from") or {}).get("id") or 0)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    row = await repo.primary_for_user(user_id)
    if not row:
        return False
    engine = str(row["engine"]).upper()
    if int(row["chat_id"]) != chat_id:
        await app.send_message(
            chat_id,
            "<b>⚠️ Your paused game belongs to another group.\nSend /resume in that group to continue it.</b>",
            parse_mode="HTML",
        )
        return True
    if row["state"] == "resuming":
        await app.send_message(chat_id, "<b>🔄 Your game is already resuming. One moment...</b>", parse_mode="HTML")
        return True
    async with _lock_for(chat_id):
        if await chat_busy(chat_id):
            queued = await repo.mark_queued(engine, int(row["match_id"]))
            if queued:
                pause_state.queue(engine, int(row["match_id"]), chat_id)
                match = await load_match(engine, int(row["match_id"])) or {
                    "challenger_id": row["challenger_id"], "opponent_id": row["opponent_id"],
                }
                await app.send_message(chat_id, queue_text(match), parse_mode="HTML")
            return True
        claimed = await repo.claim_resume(engine, int(row["match_id"]))
    if claimed:
        await run_resume(claimed, user_id)
    return True


async def drain_queue() -> None:
    rows = await repo.queued_in_order()
    waiting = {(str(r["engine"]).upper(), int(r["match_id"])) for r in rows}
    for key in pause_state.queued_keys():
        if key not in waiting:
            pause_state.unqueue(*key)
    for row in rows:
        chat_id = int(row["chat_id"])
        async with _lock_for(chat_id):
            if await chat_busy(chat_id):
                continue
            claimed = await repo.claim_resume(row["engine"], int(row["match_id"]))
        if claimed:
            await run_resume(claimed, int(claimed["challenger_id"]))


async def _worker() -> None:
    while True:
        try:
            await asyncio.sleep(QUEUE_POLL_SECONDS)
            if pause_state.has_queued():
                await drain_queue()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            print(f"[pause] queue worker failed: {exc!r}")


async def restore_state() -> None:
    await repo.reset_resuming()
    pause_state.reset()
    for row in await repo.active_rows():
        engine = str(row["engine"]).upper()
        if row["state"] == "queued":
            pause_state.queue(engine, int(row["match_id"]), int(row["chat_id"]))
        else:
            pause_state.block(engine, int(row["match_id"]), int(row["chat_id"]))


def start_worker() -> None:
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_task = asyncio.get_running_loop().create_task(_worker())
