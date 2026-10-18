"""HTTP API for the Mini App "Play Game" mode (user vs computer).

Routes (all under /api/game, Telegram initData authenticated by the server):

  GET  /api/game/config        static options (difficulties, overs, pitches, ...)
  GET  /api/game/state         active game view (or null) + career stats + recent
  GET  /api/game/history       recent results
  POST /api/game/start         {difficulty, overs, sim, pitch, replace?}
  POST /api/game/xi/swap       {rev, out_pid, in_pid}
  POST /api/game/xi/move       {rev, pid, dir}
  POST /api/game/xi/confirm    {rev}
  POST /api/game/toss          {rev, call}
  POST /api/game/toss/decide   {rev, decision}
  POST /api/game/bowler        {rev, slot}
  POST /api/game/play          {rev, choice}     (one ball or one whole over)
  POST /api/game/continue      {rev}             (innings break -> chase)
  POST /api/game/forfeit       {rev?}

One request mutates one game under a per-user lock; `rev` guards against
double taps and stale screens.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from database import miniapp_game_repo as repo
import services.miniapp_game_engine as ge
import services.miniapp_game_view as gv
import services.miniapp_game_winprob as wp

_CACHE: dict[int, dict] = {}
_LOCKS: dict[int, asyncio.Lock] = {}
_STATS_CACHE: dict[int, tuple[float, dict]] = {}
_IDLE_SECONDS = 45 * 60
_PERSIST_ACTIONS = {"start", "xi/confirm", "toss", "toss/decide", "continue", "forfeit"}


def _lock(user_id: int) -> asyncio.Lock:
    lock = _LOCKS.get(user_id)
    if lock is None:
        if len(_LOCKS) > 4000:
            for key in [k for k, v in _LOCKS.items() if not v.locked()][:2000]:
                _LOCKS.pop(key, None)
        lock = _LOCKS[user_id] = asyncio.Lock()
    return lock


def _gc_cache() -> None:
    now = time.time()
    for uid in [u for u, e in _CACHE.items() if now - e["at"] > _IDLE_SECONDS]:
        _CACHE.pop(uid, None)


async def _active(user_id: int) -> dict | None:
    entry = _CACHE.get(user_id)
    if entry:
        entry["at"] = time.time()
        return entry["g"]
    game = await repo.load_active(user_id)
    if game and game.get("stage") != "done":
        _CACHE[user_id] = {"g": game, "at": time.time()}
        _gc_cache()
        return game
    return None


def _ok(game: dict, **extra: Any) -> tuple[int, dict]:
    body = {"ok": True, "game": gv.build_view(game, extra.pop("events", None))}
    body.update(extra)
    return 200, body


def _err(status: int, code: str, detail: str, **extra: Any) -> tuple[int, dict]:
    return status, {"ok": False, "status": code, "detail": detail, **extra}


async def _estimate(game: dict, record: bool = True) -> None:
    """Win probability in a worker thread (pure CPU, tens of ms when warm)."""
    loop = asyncio.get_running_loop()
    try:
        est = await loop.run_in_executor(None, wp.estimate, game)
    except Exception as exc:                               # never block a delivery on the model
        print(f"[miniapp_game] win probability failed: {type(exc).__name__}: {exc}")
        return
    if record:
        wp.record_point(game, est)


async def _warm(user_id: int, game: dict) -> None:
    """Pre-match estimate + cache warm-up, off the request path."""
    rev0 = game["rev"]
    stage0 = game["stage"]
    try:
        loop = asyncio.get_running_loop()
        est = await loop.run_in_executor(None, wp.estimate, game)
    except Exception as exc:
        print(f"[miniapp_game] warm-up failed: {type(exc).__name__}")
        return
    if game["stage"] == stage0 and game["rev"] == rev0 and not game["inn"]:
        wp.record_point(game, est)
        game["wp"]["start"] = est["user"]


async def _career(user_id: int, force: bool = False) -> dict:
    cached = _STATS_CACHE.get(user_id)
    if cached and not force and time.time() - cached[0] < 20:
        return cached[1]
    try:
        stats = await repo.career_stats(user_id)
    except Exception as exc:
        print(f"[miniapp_game] career stats failed: {type(exc).__name__}")
        stats = {}
    _STATS_CACHE[user_id] = (time.time(), stats)
    return stats


async def _finish_hooks(user_id: int, game: dict) -> dict:
    """Store the result, refresh stats and check achievements."""
    extra: dict[str, Any] = {}
    try:
        await repo.record_result(game)
    except Exception as exc:
        print(f"[miniapp_game] result save failed: {type(exc).__name__}: {exc}")
    _STATS_CACHE.pop(user_id, None)
    link = game.get("link")
    if link and link.get("token"):
        asyncio.create_task(_report_to_group(game, dict(link)))
    try:
        from services import achievements
        newly = await achievements.check_and_unlock(user_id)
        if newly:
            extra["new_achievements"] = newly
    except Exception as exc:
        print(f"[miniapp_game] achievements check failed: {type(exc).__name__}: {exc}")
    return extra


async def _persist(game: dict) -> None:
    try:
        await repo.save_session(game)
    except Exception as exc:
        print(f"[miniapp_game] save failed: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
async def handle(viewer: Any, method: str, path: str, query: dict, payload: dict) -> tuple[int, dict]:
    route = path.removeprefix("/api/game").strip("/")
    uid = int(viewer.id)
    try:
        if method == "GET":
            if route == "config":
                return 200, {"ok": True, **gv.config_block()}
            if route in {"", "state"}:
                return await _state(viewer)
            if route == "link":
                return await _link_info(viewer, query)
            if route == "watch":
                return await _watch(viewer, query)
            if route == "history":
                rows = await repo.recent_results(uid, 20)
                return 200, {"ok": True, "results": rows}
            return _err(404, "not_found", "Unknown game route")
        if route == "start":
            return await _start(viewer, payload)
        if route in {"xi/swap", "xi/move", "xi/confirm", "toss", "toss/decide", "bowler", "play", "continue",
                     "forfeit"}:
            return await _act(viewer, route, payload)
        return _err(404, "not_found", "Unknown game route")
    except ge.GameError as exc:
        return _err(409, exc.code, exc.detail)
    except Exception as exc:
        print(f"[miniapp_game] {route or 'state'} failed: {type(exc).__name__}: {exc}")
        return _err(500, "server_error", "The match engine hit a problem. Please try again.")


async def _state(viewer: Any) -> tuple[int, dict]:
    uid = int(viewer.id)
    game = await _active(uid)
    stats = await _career(uid)
    recent = []
    try:
        recent = await repo.recent_results(uid, 6)
    except Exception as exc:
        print(f"[miniapp_game] recent failed: {type(exc).__name__}")
    body: dict[str, Any] = {"ok": True, "game": gv.build_view(game) if game else None,
                            "summary": ge.simple_summary(game) if game else None,
                            "stats": stats, "recent": recent}
    return 200, body


async def _start(viewer: Any, payload: dict) -> tuple[int, dict]:
    from miniapp_backend import api as core_api
    uid = int(viewer.id)
    async with _lock(uid):
        cfg = ge.normalize_config(payload)
        existing = await _active(uid)
        if existing:
            started = bool(existing["inn"])
            if started and not payload.get("replace"):
                return _err(409, "active_game", "You already have a match in progress.",
                            summary=ge.simple_summary(existing))
            ge.forfeit(existing)
            ge.touch(existing)
            await _persist(existing)
            await _finish_hooks(uid, existing)
            _CACHE.pop(uid, None)
        link = None
        token = str(payload.get("link") or "").strip()
        if token:
            row = await repo.get_link(token)
            if row and int(row["user_id"]) == uid and row["status"] == "open":
                link = row
        await core_api._ensure_user(viewer)
        ctx = await repo.load_user_context(uid)
        if len(ctx["squad"]) < 11:
            return _err(409, "squad_too_small", "You need at least 11 players in your squad to play a match.")
        pool = await repo.ai_pool()
        game = ge.new_game(uid, cfg, first_name=ctx["first_name"] or getattr(viewer, "first_name", None),
                           franchise=ctx["franchise"], squad=ctx["squad"], lineup_pids=ctx["lineup"],
                           ai_pool=pool, rng=random.Random())
        if link:
            game["link"] = {"token": link["token"], "chat_id": int(link["chat_id"]),
                            "message_id": link.get("message_id"), "user_id": uid,
                            "user_name": link.get("user_name") or ctx["first_name"] or ""}
        _CACHE[uid] = {"g": game, "at": time.time()}
        await _persist(game)
        if link:
            try:
                await repo.attach_link(link["token"], game["id"])
            except Exception as exc:
                print(f"[miniapp_game] link attach failed: {type(exc).__name__}")
        asyncio.create_task(_warm(uid, game))
        return _ok(game)


async def _act(viewer: Any, route: str, payload: dict) -> tuple[int, dict]:
    uid = int(viewer.id)
    async with _lock(uid):
        game = await _active(uid)
        if game is None:
            return _err(404, "no_game", "There is no active match. Start a new one.")
        try:
            rev = int(payload.get("rev"))
        except (TypeError, ValueError):
            rev = -1
        if route != "forfeit" and rev != int(game["rev"]):
            status, body = _ok(game)
            body.update({"ok": False, "status": "stale", "detail": "The match moved on. Showing the latest state."})
            return 409, body

        rng = random.Random()
        events: list[dict] = []
        before_stage = game["stage"]
        inn0 = ge.cur(game)
        before_over = inn0["over"]["no"] if inn0 else 0
        before_inn = game["n"]
        persist = route in _PERSIST_ACTIONS
        extra: dict[str, Any] = {}

        if route == "xi/swap":
            ge.xi_swap(game, int(payload.get("out_pid") or 0), int(payload.get("in_pid") or 0))
        elif route == "xi/move":
            ge.xi_move(game, int(payload.get("pid") or 0), int(payload.get("dir") or 0))
        elif route == "xi/confirm":
            ge.xi_confirm(game)
            ge.touch(game)
            await _persist(game)
            asyncio.create_task(_warm(uid, game))
            return _ok(game)
        elif route == "toss":
            ge.toss_call(game, str(payload.get("call") or ""), rng)
        elif route == "toss/decide":
            ge.toss_decide(game, str(payload.get("decision") or ""), rng)
        elif route == "bowler":
            ge.choose_bowler(game, int(payload.get("slot") if payload.get("slot") is not None else -1))
        elif route == "play":
            choice = str(payload.get("choice") or "")
            if game["cfg"]["sim"] == "over":
                events = ge.play_over(game, choice, rng)
            else:
                events = ge.play_ball(game, choice, rng)
        elif route == "continue":
            ge.continue_innings(game, rng)
        elif route == "forfeit":
            ge.forfeit(game)
            persist = True

        ge.touch(game)
        if route in {"play", "toss", "toss/decide", "continue", "bowler"} and game["stage"] != "xi":
            if route != "bowler":
                await _estimate(game)
        inn1 = ge.cur(game)
        over_changed = bool(inn1) and (game["n"] != before_inn or inn1["over"]["no"] != before_over)
        if game["stage"] != before_stage or over_changed:
            persist = True
        if game["stage"] == "done":
            persist = True
            res = game.get("result") or {}
            final = 100.0 if res.get("winner") == "user" else (50.0 if res.get("type") == "tie" else 0.0)
            wpd = game.setdefault("wp", {})
            wpd["user"] = final
            wpd["proj"] = None
            wpd["range"] = None
            wpd.setdefault("series", []).append([int(game.get("balls_played") or 0) + 1, final])
            extra = await _finish_hooks(uid, game)
        if persist:
            await _persist(game)
        view_status = _ok(game, events=events, **extra)
        if game["stage"] == "done":
            _CACHE.pop(uid, None)
        return view_status


# ---------------------------------------------------------------------------
# group links + live spectating (/playai)
# ---------------------------------------------------------------------------
_FINAL: dict[str, tuple[float, dict]] = {}


async def _report_to_group(game: dict, link: dict) -> None:
    """Send the shared match-completion notice and the AI match report."""
    try:
        from miniapp_backend.config import BOT_TOKEN
        from services import miniapp_game_report as report
        res = game.get("result") or {}
        try:
            await repo.finish_link(link["token"], res.get("text"))
        except Exception as exc:
            print(f"[miniapp_game] finish_link failed: {type(exc).__name__}")
        view = gv.build_view(game)
        _FINAL[link["token"]] = (time.time(), view)
        if len(_FINAL) > 200:
            for key in sorted(_FINAL, key=lambda k: _FINAL[k][0])[:100]:
                _FINAL.pop(key, None)
        if not game.get("inn"):
            return                                  # nothing was played - no match result to announce

        # Use the same cross-engine number as PLAY, PLAYINT, PLAYIPL, PLAYWPL
        # and MATCH. The result row has already been saved by _finish_hooks.
        number = 0
        try:
            from services.match_notification import next_match_number
            number = await next_match_number()
        except Exception as exc:
            print(f"[miniapp_game] shared match number unavailable: {type(exc).__name__}: {exc}")

        # PLAY AI uses the same central notification group and message format
        # as the other engines. Keep report delivery independent so a Telegram
        # notification problem does not prevent the existing card/HTML report.
        try:
            from app import app as telegram_app
            from services.match_notification import send_match_completion_notification
            innings = report._snapshots(view)
            user_team = (game.get("teams") or {}).get("user") or {}
            ai_team = (game.get("teams") or {}).get("ai") or {}
            await send_match_completion_notification(
                telegram_app,
                engine="PLAY AI",
                pitch=(game.get("cfg") or {}).get("pitch"),
                user1=(None, link.get("user_name") or user_team.get("name") or "Player"),
                user2=(None, ai_team.get("name") or "Crickium AI"),
                innings_1=innings[0] if innings else {},
                innings_2=innings[1] if len(innings) > 1 else {},
                result=res.get("text"),
                match_number=number,
            )
        except Exception as exc:
            print(f"[miniapp_game] match completion notification failed: {type(exc).__name__}: {exc}")

        await report.deliver(game, link, BOT_TOKEN, number)
    except Exception as exc:
        print(f"[miniapp_game] group report failed: {type(exc).__name__}: {exc}")


def _first(query: dict, key: str) -> str:
    value = query.get(key)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


async def _link_info(viewer: Any, query: dict) -> tuple[int, dict]:
    token = _first(query, "token")
    row = await repo.get_link(token)
    if not row:
        return _err(404, "no_link", "This game link is no longer valid.")
    owner = int(row["user_id"]) == int(viewer.id)
    return 200, {"ok": True, "owner": owner, "status": row["status"], "overs": row.get("overs"),
                 "host": row.get("user_name") or "Player", "result": row.get("result_text")}


async def _watch(viewer: Any, query: dict) -> tuple[int, dict]:
    """Read-only live view of someone else's game (nothing private is exposed)."""
    token = _first(query, "token")
    row = await repo.get_link(token)
    if not row:
        return _err(404, "no_link", "This game link is no longer valid.")
    host = row.get("user_name") or "Player"
    base = {"ok": True, "status": row["status"], "host": host, "overs": row.get("overs"), "result": row.get("result_text")}
    game = None
    owner_uid = int(row["user_id"])
    entry = _CACHE.get(owner_uid)
    if entry and entry["g"].get("link", {}).get("token") == token:
        game = entry["g"]
    elif row["status"] == "done" and token in _FINAL:
        view = dict(_FINAL[token][1]); view.pop("need", None); view["need"] = {"kind": "spectate"}
        return 200, {**base, "game": view}
    elif row.get("game_id"):
        game = await repo.load_state_by_id(row["game_id"])
    if not game:
        return 200, {**base, "game": None}
    view = gv.build_view(game)
    view.pop("xi", None)
    view["events"] = []
    view["need"] = {"kind": "spectate"}
    view["lineups"] = view.get("lineups")
    return 200, {**base, "game": view}
