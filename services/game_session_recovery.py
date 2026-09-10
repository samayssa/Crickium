"""Persistent recovery for the four interactive game runtimes.

This module deliberately sits outside the game engines. It snapshots the
existing runtime session objects so a bot process restart loses no live
in-memory match state. Restored sessions are injected back into the same
runtime registries the engines already use.
"""
from __future__ import annotations

import asyncio
import pickle
from typing import Any

from database.query import execute, fetchrow, fetch

_TERMINAL = {"timed_out", "completed", "declined", "ended", "expired"}
_SAVER_TASK: asyncio.Task | None = None
_SAVE_LOCK = asyncio.Lock()


async def ensure_schema() -> None:
    await execute(
        """
        CREATE TABLE IF NOT EXISTS game_session_snapshots(
            engine TEXT NOT NULL,
            match_id BIGINT NOT NULL,
            payload BYTEA NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (engine, match_id)
        );
        """
    )


async def delete_snapshot(engine: str, match_id: int) -> None:
    try:
        await ensure_schema()
        await execute(
            "DELETE FROM game_session_snapshots WHERE engine=$1 AND match_id=$2;",
            str(engine).upper(), int(match_id),
        )
    except Exception as exc:
        print(f"[session-recovery] delete failed {engine}:{match_id}: {exc!r}")


def _normalize_for_pickle(session: Any) -> Any:
    # asyncpg.Record is not reliably pickleable. Runtime `match` values are
    # plain mapping data, so normalize that field in-place before pickling.
    try:
        if hasattr(session, "match") and session.match is not None:
            session.match = dict(session.match)
    except Exception:
        pass
    return session


async def persist_session(engine: str, session: Any) -> bool:
    if session is None:
        return False
    try:
        match_id = int(getattr(session, "match_id"))
    except Exception:
        return False
    try:
        payload = pickle.dumps(_normalize_for_pickle(session), protocol=pickle.HIGHEST_PROTOCOL)
        await ensure_schema()
        await execute(
            """
            INSERT INTO game_session_snapshots(engine,match_id,payload,updated_at)
            VALUES($1,$2,$3,NOW())
            ON CONFLICT(engine,match_id) DO UPDATE SET
                payload=EXCLUDED.payload,
                updated_at=NOW();
            """,
            str(engine).upper(), match_id, payload,
        )
        return True
    except Exception as exc:
        print(f"[session-recovery] persist failed {engine}:{match_id}: {exc!r}")
        return False


async def _load_snapshot(engine: str, match_id: int) -> Any | None:
    await ensure_schema()
    row = await fetchrow(
        "SELECT payload FROM game_session_snapshots WHERE engine=$1 AND match_id=$2;",
        str(engine).upper(), int(match_id),
    )
    if not row:
        return None
    try:
        return pickle.loads(bytes(row["payload"]))
    except Exception as exc:
        print(f"[session-recovery] snapshot decode failed {engine}:{match_id}: {exc!r}")
        await delete_snapshot(engine, match_id)
        return None


def _inject(engine: str, session: Any) -> Any | None:
    mid = int(getattr(session, "match_id"))
    if engine == "PLAY":
        from engines.play_runtime import _SESSIONS
        _SESSIONS[mid] = session
    elif engine == "PLAYINT":
        from engines.playint_runtime import _PLAYINT_SESSIONS
        _PLAYINT_SESSIONS[mid] = session
    elif engine == "PLAYIPL":
        from engines.playipl_runtime import _PLAYIPL_SESSIONS
        _PLAYIPL_SESSIONS[mid] = session
    else:
        return None
    return session


async def restore_session(engine: str, match_id: int) -> Any | None:
    """Restore an existing snapshot into the engine registry if needed."""
    engine = str(engine).upper()
    mid = int(match_id)
    try:
        if engine == "PLAY":
            from engines.play_runtime import get_session
            live = get_session(mid)
        elif engine == "PLAYINT":
            from engines.playint_runtime import get_playint_session
            live = get_playint_session(mid)
        elif engine == "PLAYIPL":
            from engines.playipl_runtime import get_playipl_session
            live = get_playipl_session(mid)
        else:
            return None
        if live is not None:
            return live
    except Exception:
        pass

    session = await _load_snapshot(engine, mid)
    if session is None:
        return None
    try:
        return _inject(engine, session)
    except Exception as exc:
        print(f"[session-recovery] inject failed {engine}:{mid}: {exc!r}")
        return None


def _active_runtime_sessions() -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    try:
        from engines.play_runtime import _SESSIONS
        out.extend(("PLAY", s) for s in list(_SESSIONS.values()))
    except Exception:
        pass
    try:
        from engines.playint_runtime import _PLAYINT_SESSIONS
        out.extend(("PLAYINT", s) for s in list(_PLAYINT_SESSIONS.values()))
    except Exception:
        pass
    try:
        from engines.playipl_runtime import _PLAYIPL_SESSIONS
        out.extend(("PLAYIPL", s) for s in list(_PLAYIPL_SESSIONS.values()))
    except Exception:
        pass
    return out


async def _periodic_saver() -> None:
    while True:
        try:
            await asyncio.sleep(0.5)
            sessions = _active_runtime_sessions()
            if not sessions:
                continue
            async with _SAVE_LOCK:
                for engine, session in sessions:
                    await persist_session(engine, session)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            print(f"[session-recovery] periodic saver failed: {exc!r}")


def ensure_background_saver() -> None:
    global _SAVER_TASK
    if _SAVER_TASK is not None and not _SAVER_TASK.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _SAVER_TASK = loop.create_task(_periodic_saver())


async def post_callback_sync(engine: str, match_id: int) -> None:
    """Persist the post-callback runtime state, or remove it when terminal."""
    engine = str(engine).upper()
    ensure_background_saver()
    if engine == "PLAY":
        from database.play_repo import get_match
        match = await get_match(int(match_id))
        from engines.play_runtime import get_session
        session = get_session(int(match_id))
    elif engine == "PLAYINT":
        from database.playint_repo import get_match
        match = await get_match(int(match_id))
        from engines.playint_runtime import get_playint_session
        session = get_playint_session(int(match_id))
    elif engine == "PLAYIPL":
        from database.playipl_repo import get_match
        match = await get_match(int(match_id))
        from engines.playipl_runtime import get_playipl_session
        session = get_playipl_session(int(match_id))
    else:
        return

    if not match or str(match.get("status") or "") in _TERMINAL:
        await delete_snapshot(engine, int(match_id))
        return
    if session is not None:
        await persist_session(engine, session)


async def _rebuild_play(match: dict) -> Any | None:
    from database.play_repo import get_match
    from engines.play_runtime import create_play_session
    from engines.lineup_engine import load_current_xi
    from utils.stadium import random_stadium
    from utils.temperature import random_weather

    decision = str(match.get("decision") or "").strip().lower()
    if not match.get("pitch") or not match.get("toss_winner_id") or decision not in {"bat", "bowl"}:
        return None
    challenger_id = int(match["challenger_id"])
    opponent_id = int(match["opponent_id"])
    toss_winner_id = int(match["toss_winner_id"])
    if decision == "bat":
        batting_id = toss_winner_id
        bowling_id = opponent_id if batting_id == challenger_id else challenger_id
    else:
        bowling_id = toss_winner_id
        batting_id = opponent_id if bowling_id == challenger_id else challenger_id
    batting = await load_current_xi(batting_id) or []
    bowling = await load_current_xi(bowling_id) or []
    from utils.mentions import display_name
    session = create_play_session(
        match_id=int(match["match_id"]), chat_id=int(match["chat_id"]), match=dict(match),
        pitch=str(match.get("pitch") or "green"), stadium=random_stadium(), weather=random_weather().format(),
        batting_team_id=batting_id, bowling_team_id=bowling_id,
        batting_team_display=display_name(
            match.get("challenger_username") if batting_id == challenger_id else match.get("opponent_username"),
            match.get("challenger_name") if batting_id == challenger_id else match.get("opponent_name"),
        ),
        bowling_team_display=display_name(
            match.get("challenger_username") if bowling_id == challenger_id else match.get("opponent_username"),
            match.get("challenger_name") if bowling_id == challenger_id else match.get("opponent_name"),
        ),
        batting_squad=batting, bowling_squad=bowling,
    )
    try:
        from database.player_upgrades_repo import restore_snapshot, load_snapshot_players, persist_snapshot
        snap = await restore_snapshot(int(match["match_id"]))
        if not snap:
            snap = await load_snapshot_players([challenger_id, opponent_id], {
                challenger_id: await load_current_xi(challenger_id) or [],
                opponent_id: await load_current_xi(opponent_id) or [],
            })
            await persist_snapshot(int(match["match_id"]), snap)
        session.upgrade_snapshot = snap
        session.upgrade_snapshot_restored = True
    except Exception:
        pass
    from engines.innings_engine import start_new_partnership
    start_new_partnership(session)
    return session


async def _rebuild_team_engine(engine: str, match: dict) -> Any | None:
    if not match.get("pitch") or not match.get("toss_winner_id") or str(match.get("decision") or "").lower() not in {"bat", "bowl"}:
        return None
    import json
    if engine == "PLAYINT":
        from database.playint_repo import get_teams_player_ids
        from engines.playint_runtime import create_playint_session
        engine_key = "T20I"
        from database.playint_teams_repo import team_name
        challenger_ids = match.get("challenger_xi") or []
        opponent_ids = match.get("opponent_xi") or []
        if isinstance(challenger_ids, str): challenger_ids = json.loads(challenger_ids)
        if isinstance(opponent_ids, str): opponent_ids = json.loads(opponent_ids)
        loader = get_teams_player_ids
        c_squad = await loader(match["challenger_team_code"], challenger_ids, engine_key=engine_key)
        o_squad = await loader(match["opponent_team_code"], opponent_ids, engine_key=engine_key)
        names = team_name
    else:
        from database.playipl_repo import get_teams_player_ids
        from engines.playipl_runtime import create_playipl_session
        engine_key = "IPL"
        from database.playipl_teams_repo import team_name
        challenger_ids = match.get("challenger_xi") or []
        opponent_ids = match.get("opponent_xi") or []
        if isinstance(challenger_ids, str): challenger_ids = json.loads(challenger_ids)
        if isinstance(opponent_ids, str): opponent_ids = json.loads(opponent_ids)
        c_squad = await get_teams_player_ids(match["challenger_team_code"], challenger_ids)
        o_squad = await get_teams_player_ids(match["opponent_team_code"], opponent_ids)
        names = team_name

    challenger_id = int(match["challenger_id"]); opponent_id = int(match["opponent_id"]); toss_winner_id = int(match["toss_winner_id"])
    decision = str(match["decision"]).lower()
    if decision == "bat":
        batting_id, bowling_id = toss_winner_id, opponent_id if toss_winner_id == challenger_id else challenger_id
    else:
        bowling_id, batting_id = toss_winner_id, opponent_id if toss_winner_id == challenger_id else challenger_id
    batting_code = match["challenger_team_code"] if batting_id == challenger_id else match["opponent_team_code"]
    bowling_code = match["challenger_team_code"] if bowling_id == challenger_id else match["opponent_team_code"]
    batting_squad = c_squad if batting_id == challenger_id else o_squad
    bowling_squad = c_squad if bowling_id == challenger_id else o_squad
    from engines.play_engine import playing_xi
    # The live runtimes take a full squad and derive their bowling pool/XI.
    session = create_playint_session(
        match_id=int(match["match_id"]), chat_id=int(match["chat_id"]), match=dict(match),
        pitch=str(match.get("pitch") or "green"), stadium="Recovery Stadium", weather="Recovery Weather",
        batting_team_id=batting_id, bowling_team_id=bowling_id,
        batting_team_display=names(batting_code), bowling_team_display=names(bowling_code),
        batting_squad=batting_squad, bowling_squad=bowling_squad,
    ) if engine == "PLAYINT" else create_playipl_session(
        match_id=int(match["match_id"]), chat_id=int(match["chat_id"]), match=dict(match),
        pitch=str(match.get("pitch") or "green"), stadium="Recovery Stadium", weather="Recovery Weather",
        batting_team_id=batting_id, bowling_team_id=bowling_id,
        batting_team_display=names(batting_code), bowling_team_display=names(bowling_code),
        batting_squad=batting_squad, bowling_squad=bowling_squad,
    )
    try:
        from database.player_upgrades_repo import restore_snapshot
        snap = await restore_snapshot(int(match["match_id"]))
        if snap:
            session.upgrade_snapshot = snap
    except Exception:
        pass
    from engines.innings_engine import start_new_partnership
    start_new_partnership(session)
    return session


async def restore_or_rebuild(engine: str, match: dict) -> Any | None:
    engine = str(engine).upper()
    mid = int(match["match_id"])
    session = await restore_session(engine, mid)
    if session is not None:
        return session
    # Only rebuild live/runtime stages. Earlier stages are fully represented
    # in their match rows and do not need an in-memory runtime object.
    if str(match.get("status") or "") != "lineup":
        return None
    if engine == "PLAY":
        session = await _rebuild_play(match)
    elif engine in {"PLAYINT", "PLAYIPL"}:
        session = await _rebuild_team_engine(engine, match)
    else:
        session = None
    if session is not None:
        await persist_session(engine, session)
    return session
