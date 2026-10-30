from __future__ import annotations

import asyncio
import html
import json
import traceback
from typing import Any

from app import app
from database.playso_repo import create_match, get_match, set_basic, set_state


def _user_fields(match: dict[str, Any], user_id: int) -> tuple[Any, Any]:
    if int(user_id) == int(match["challenger_id"]):
        return match.get("challenger_username"), match.get("challenger_name")
    return match.get("opponent_username"), match.get("opponent_name")


def _display(match: dict[str, Any], user_id: int) -> str:
    username, name = _user_fields(match, user_id)
    return username or name or str(user_id)


def build_draw_result_text(innings_1: dict, innings_2: dict, top_batters_fn, top_bowlers_fn) -> str:
    def _block(snap: dict) -> str:
        bats = top_batters_fn(snap)
        bowls = top_bowlers_fn(snap)
        bat_lines = "\n".join(
            f"⭐ {b['name']} - {b['runs']} ({b['balls']})" for b in bats
        ) or "⭐ -"
        bowl_lines = "\n".join(
            f"🎯 {b['name']} - {b['wickets']}W ({b['runs']}R)" for b in bowls
        ) or "🎯 -"
        return (
            f"🏏 {snap['batting_team_display']} Innings — "
            f"{snap['runs']}/{snap['wickets']} ({snap['over_text']} Ov)\n"
            f"{bat_lines}\n{bowl_lines}"
        )

    return (
        "<b>╭━━〔 ⚖️ MATCH RESULT 〕━━╮\n\n"
        "🤝 MATCH DRAW\n\n"
        "📋 MATCH HIGHLIGHTS\n\n"
        f"{_block(innings_1)}\n\n"
        f"{_block(innings_2)}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ The match is tied. A Super Over will decide the winner.\n\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


def _jsonable(value: Any) -> Any:
    """Round-trip through JSON so playso_matches.state (JSONB) can always store the origin snapshots."""
    return json.loads(json.dumps(value, default=str))


async def start_decider(
    *,
    origin_engine: str,
    origin_match_id: int,
    chat_id: int,
    origin_match: dict,
    innings_1: dict,
    innings_2: dict,
) -> int:
    """Create a PLAYSO decider linked back to the original T20 match.

    If the decider cannot be created for any reason the original match is finished as a normal tie instead of
    being left live forever (the old "stuck on tie" failure mode).
    """
    try:
        return await _start_decider(
            origin_engine=origin_engine, origin_match_id=origin_match_id, chat_id=chat_id,
            origin_match=origin_match, innings_1=innings_1, innings_2=innings_2,
        )
    except Exception as exc:
        print(f"[super_over_bridge] could not start the Super Over for {origin_engine}:{origin_match_id}: {exc!r}")
        traceback.print_exc()
        try:
            await _finish_as_tie(str(origin_engine), int(origin_match_id), int(chat_id), dict(origin_match), innings_1, innings_2)
        except Exception as exc2:
            print(f"[super_over_bridge] tie fallback failed as well: {exc2!r}")
            traceback.print_exc()
            await force_close_origin({"origin_engine": origin_engine, "origin_match_id": origin_match_id}, "completed")
        return 0


async def _start_decider(
    *,
    origin_engine: str,
    origin_match_id: int,
    chat_id: int,
    origin_match: dict,
    innings_1: dict,
    innings_2: dict,
) -> int:
    try:   # the tied original match must not time out while its Super Over is being played
        from utils.game_inactivity import cancel_match
        cancel_match(str(origin_engine).upper(), int(origin_match_id))
    except Exception as exc:
        print(f"[super_over_bridge] could not stop the origin inactivity clock: {exc!r}")
    challenger_id = int(origin_match["challenger_id"])
    opponent_id = int(origin_match["opponent_id"])
    decider = await create_match(
        chat_id,
        {
            "id": challenger_id,
            "username": origin_match.get("challenger_username"),
            "first_name": origin_match.get("challenger_name"),
        },
        {
            "id": opponent_id,
            "username": origin_match.get("opponent_username"),
            "first_name": origin_match.get("opponent_name"),
        },
    )
    decider = dict(decider)
    source_state = {
        "origin_engine": str(origin_engine),
        "origin_match_id": int(origin_match_id),
        "origin_chat_id": int(chat_id),
        "origin_innings": _jsonable([dict(innings_1), dict(innings_2)]),
        "origin_pitch": str(origin_match.get("pitch") or "even"),
        "next_super_over_batting_user": int(innings_2["batting_team_id"]),
        "next_super_over_bowling_user": int(innings_2["bowling_team_id"]),
        "is_repeat_super_over": False,
        "previous_bowlers": {},
        "origin_xp_awarded": False,
        "stadium": origin_match.get("stadium"),
        "weather": origin_match.get("weather"),
    }
    await set_basic(
        int(decider["match_id"]),
        pitch=str(origin_match.get("pitch") or "even"),
        innings_no=1,
        status="lineup",
        expires_at=None,
    )
    await set_state(int(decider["match_id"]), source_state, status="lineup")

    from handlers.playso.setup import start_setup
    fresh = await get_match(int(decider["match_id"]))
    try:
        await start_setup(chat_id, dict(fresh), 1)
    except Exception:
        # do not leave an orphan live decider behind
        await set_state(int(decider["match_id"]), source_state, status="ended")
        raise
    return int(decider["match_id"])


async def _load_origin_session(engine: str, match_id: int):
    if engine == "PLAY":
        from engines.play_runtime import get_session
    elif engine == "PLAYINT":
        from engines.playint_runtime import get_playint_session as get_session
    elif engine == "PLAYIPL":
        from engines.playipl_runtime import get_playipl_session as get_session
    elif engine == "PLAYWPL":
        from engines.playwpl_runtime import get_playwpl_session as get_session
    else:
        return None
    return get_session(int(match_id))


def _clear_origin_session(engine: str, match_id: int) -> None:
    if engine == "PLAY":
        from engines.play_runtime import clear_session
    elif engine == "PLAYINT":
        from engines.playint_runtime import clear_playint_session as clear_session
    elif engine == "PLAYIPL":
        from engines.playipl_runtime import clear_playipl_session as clear_session
    elif engine == "PLAYWPL":
        from engines.playwpl_runtime import clear_playwpl_session as clear_session
    else:
        return
    clear_session(int(match_id))


def _helpers(engine: str):
    if engine == "PLAY":
        from handlers.play.live import _record_player_squad_stats, _award_match_xp_and_stats, _match_result_text
    elif engine == "PLAYINT":
        from handlers.playint.live import _record_player_squad_stats, _award_match_xp_and_stats, _match_result_text
    elif engine == "PLAYIPL":
        from handlers.playipl.live import _record_player_squad_stats, _award_match_xp_and_stats, _match_result_text
    elif engine == "PLAYWPL":
        from handlers.playwpl.live import _record_player_squad_stats, _award_match_xp_and_stats, _match_result_text
    else:
        raise ValueError(engine)
    return _record_player_squad_stats, _award_match_xp_and_stats, _match_result_text


def _repo_get(engine: str):
    if engine == "PLAY":
        from database.play_repo import get_match
    elif engine == "PLAYINT":
        from database.playint_repo import get_match
    elif engine == "PLAYIPL":
        from database.playipl_repo import get_match
    elif engine == "PLAYWPL":
        from database.playwpl_repo import get_match
    else:
        return None
    return get_match


def _repo_update_status(engine: str):
    if engine == "PLAY":
        from database.play_repo import update_status
    elif engine == "PLAYINT":
        from database.playint_repo import update_status
    elif engine == "PLAYIPL":
        from database.playipl_repo import update_status
    elif engine == "PLAYWPL":
        from database.playwpl_repo import update_status
    else:
        return None
    return update_status


_TERMINAL = {"completed", "ended", "timed_out", "expired", "declined"}


async def force_close_origin(state: dict, status: str = "ended") -> None:
    """Close the original T20 match that is waiting for its Super Over (exit / abandon / timeout / failure)."""
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except ValueError:
            state = {}
    engine = str((state or {}).get("origin_engine") or "").upper()
    match_id = int((state or {}).get("origin_match_id") or 0)
    if not engine or not match_id:
        return
    upd = _repo_update_status(engine)
    if upd is not None:
        try:
            await upd(match_id, status)
        except Exception as exc:
            print(f"[super_over_bridge] could not set {engine}:{match_id} to {status}: {exc!r}")
    try:
        _clear_origin_session(engine, match_id)
    except Exception:
        pass
    try:
        from utils.game_inactivity import cancel_match
        cancel_match(engine, match_id)
    except Exception:
        pass


async def _award_super_over_result(origin_match: dict, origin_match_id: int, winner_id: int, loser_id: int) -> None:
    """XP / coins / win-loss / H2H for the Super Over winner (the T20 innings alone were a tie)."""
    try:
        from services.match_rewards import award_competitive_rewards
        await award_competitive_rewards(int(winner_id), int(loser_id))
    except Exception as exc:
        print(f"[super_over_bridge] rewards failed: {exc!r}")
    try:
        from database.user_stats_repo import record_h2h_result
        await record_h2h_result(int(origin_match_id), int(origin_match["challenger_id"]), int(origin_match["opponent_id"]), int(winner_id))
    except Exception as exc:
        print(f"[super_over_bridge] H2H failed: {exc!r}")


async def _origin_row(engine: str, match_id: int) -> dict | None:
    getter = _repo_get(engine)
    if getter is None:
        return None
    row = await getter(match_id)
    return dict(row) if row else None


async def _finish_as_tie(engine: str, match_id: int, chat_id: int, origin_match: dict, innings_1: dict, innings_2: dict) -> None:
    """Fallback: the Super Over could not start - finish the match as a normal tie (no one stays stuck)."""
    session = await _load_origin_session(engine, match_id)
    record_stats, award_stats, result_builder = _helpers(engine)
    try:
        await app.send_message(chat_id, "<b>⚠️ The Super Over could not be started, so this match is recorded as a tie.</b>", parse_mode="HTML")
    except Exception:
        pass
    if session is not None:
        try:
            await record_stats(session, innings_1, innings_2)
            await award_stats(session, innings_1, innings_2)
        except Exception as exc:
            print(f"[super_over_bridge] tie stats failed: {exc!r}")
    upd = _repo_update_status(engine)
    if upd is not None:
        await upd(match_id, "completed")
    try:
        from services.match_analysis import collect_runtime_session, collect_snapshots, send_report
        payload = (collect_runtime_session(engine, session, termination="completed", winner_id=None, loser_id=None)
                   if session is not None else
                   collect_snapshots(engine, origin_match, [innings_1, innings_2], termination="completed"))
        await send_report(app, payload)
    except Exception as exc:
        print(f"[super_over_bridge] tie report failed: {exc!r}")
    _clear_origin_session(engine, match_id)


async def finalize_decider(decider_match: dict, so_history: list[dict], winner_id: int) -> None:
    """Finalize the originating match after a Super Over winner exists."""
    state = dict(decider_match.get("state") or {})
    origin_engine = str(state.get("origin_engine") or "")
    origin_match_id = int(state.get("origin_match_id") or 0)
    if not origin_engine or not origin_match_id:
        return
    session = await _load_origin_session(origin_engine, origin_match_id)

    # The DB row is authoritative for "already finished?" (double finalisation guard); the session (when it
    # still exists in memory) supplies the richer live data. After a bot restart there is no session: the
    # snapshots stored in the decider state are enough to report and close the match.
    row = await _origin_row(origin_engine, origin_match_id)
    if row and str(row.get("status") or "") in _TERMINAL:
        _clear_origin_session(origin_engine, origin_match_id)
        return
    origin_match = dict(session.match) if session is not None else row
    if not origin_match:
        return

    origin_innings = list(state.get("origin_innings") or [])
    if len(origin_innings) < 2:
        await force_close_origin(state, "completed")
        return
    innings_1, innings_2 = origin_innings[0], origin_innings[1]

    record_stats, _award_stats_unused, result_builder = _helpers(origin_engine)

    winner_id = int(winner_id)
    winner_display = _display(origin_match, winner_id)
    loser_id = int(origin_match["opponent_id"] if winner_id == int(origin_match["challenger_id"]) else origin_match["challenger_id"])
    loser_username, loser_name = _user_fields(origin_match, loser_id)
    final_text = ""

    # Keep the original match's result text structure (20-over highlights/POTM) with the headline rewritten to
    # the Super Over decision, then append a compact Super Over block.
    winner_team = (
        innings_1["batting_team_display"]
        if winner_id == int(innings_1["batting_team_id"])
        else innings_2["batting_team_display"]
    )
    try:
        base = result_builder(innings_1, innings_2)
        base = base.replace("🤝 Match Tied!", f"🎉 {html.escape(str(winner_team))} XI won in Super Over!")
        so1 = so_history[0] if so_history else {}
        so2 = so_history[1] if len(so_history) > 1 else {}
        rounds = list(state.get("super_over_rounds") or [])
        so_block = (
            "\n\n<b>╭━━〔 ⚡ SUPER OVER DECIDER 〕━━╮</b>\n\n"
            + (f"🔁 Super Overs played ➤ {len(rounds) + 1}\n" if rounds else "")
            + f"🏏 First Super Over innings ➤ {int(so1.get('runs') or 0)}/{int(so1.get('wickets') or 0)}"
            f" ({so1.get('over_text') or '0.0'} Ov)\n"
            f"🏏 Second Super Over innings ➤ {int(so2.get('runs') or 0)}/{int(so2.get('wickets') or 0)}"
            f" ({so2.get('over_text') or '0.0'} Ov)\n\n"
            f"🏆 Winner ➤ {html.escape(str(winner_team))} XI\n"
            "⚡ Result ➤ WON IN SUPER OVER\n\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯</b>"
        )
        final_text = base + so_block
        await app.send_message(int(origin_match["chat_id"]), final_text, parse_mode="HTML")
    except Exception as exc:
        print(f"[super_over_bridge] final result message failed: {exc!r}")

    if session is not None:
        try:
            await record_stats(session, innings_1, innings_2)
        except Exception as exc:
            print(f"[super_over_bridge] player stats failed: {exc!r}")
        try:
            from services.quest_engine import record_match_summary_for_session
            await record_match_summary_for_session(session, innings_1, innings_2, engine=origin_engine, match_type=origin_engine)
        except Exception as exc:
            print(f"[super_over_bridge] quest summary failed: {exc!r}")
    # rewards, win/loss and H2H follow the SUPER OVER winner (the T20 innings alone were a tie)
    await _award_super_over_result(origin_match, origin_match_id, winner_id, loser_id)

    try:
        from services.match_summary import send_match_summary, player_details
        if origin_engine == "PLAY":
            from engines.play_runtime import player_of_the_match
        elif origin_engine == "PLAYINT":
            from engines.playint_runtime import player_of_the_match
        elif origin_engine == "PLAYIPL":
            from engines.playipl_runtime import player_of_the_match
        else:
            from engines.playwpl_runtime import player_of_the_match
        potm_name = player_of_the_match(innings_1, innings_2)
        from services.match_rewards import build_result_caption
        await send_match_summary(
            app,
            int(origin_match["chat_id"]),
            [innings_1, innings_2],
            winner=winner_display,
            margin="IN SUPER OVER",
            potm=player_details([innings_1, innings_2], potm_name),
            caption=build_result_caption(
                f"<a href=\"tg://user?id={winner_id}\">{html.escape(str(winner_display))}</a>",
                f"<a href=\"tg://user?id={loser_id}\">{html.escape(str(loser_name or loser_username or loser_id))}</a>",
            ),
        )
    except Exception as exc:
        print(f"[super_over_bridge] summary image failed: {exc!r}")

    # Mark the origin match completed BEFORE the report: the report is what feeds tournaments, and nothing
    # below may leave the match "live" if it fails.
    upd = _repo_update_status(origin_engine)
    try:
        if upd is not None:
            await upd(origin_match_id, "completed")
    except Exception as exc:
        print(f"[super_over_bridge] failed to mark original match completed: {exc!r}")

    try:
        from services.match_analysis import collect_runtime_session, collect_snapshots, send_report
        if session is not None:
            report = collect_runtime_session(
                origin_engine, session, termination="completed", winner_id=winner_id, loser_id=loser_id,
                reason="decided by Super Over",
            )
        else:
            report = collect_snapshots(
                origin_engine, origin_match, [innings_1, innings_2], termination="completed",
                winner_id=winner_id, loser_id=loser_id, reason="decided by Super Over",
            )
        report["game_name"] = {"PLAY": "Play", "PLAYINT": "PlayInt", "PLAYIPL": "PlayIPL", "PLAYWPL": "PlayWPL"}.get(origin_engine, origin_engine) + " + Super Over"
        rounds = list(state.get("super_over_rounds") or [])
        rounds.append({"round": len(rounds) + 1, "innings": so_history})
        report["super_over_rounds"] = rounds
        await send_report(app, report)
    except Exception as exc:
        print(f"[match_analysis] Super Over origin report failed: {exc!r}")

    try:
        from services.match_notification import send_match_completion_notification
        await send_match_completion_notification(
            app,
            engine=origin_engine,
            pitch=origin_match.get("pitch"),
            user1=(origin_match.get("challenger_username"), origin_match.get("challenger_name")),
            user2=(origin_match.get("opponent_username"), origin_match.get("opponent_name")),
            innings_1=innings_1,
            innings_2=innings_2,
            result=final_text,
        )
    except Exception as exc:
        print(f"[super_over_bridge] completion notification failed: {exc!r}")

    _clear_origin_session(origin_engine, origin_match_id)
    try:
        from utils.game_inactivity import cancel_match
        cancel_match(origin_engine, origin_match_id)
    except Exception:
        pass
