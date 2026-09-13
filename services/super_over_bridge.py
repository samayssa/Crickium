from __future__ import annotations

import asyncio
import html
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


async def start_decider(
    *,
    origin_engine: str,
    origin_match_id: int,
    chat_id: int,
    origin_match: dict,
    innings_1: dict,
    innings_2: dict,
) -> int:
    """Create a PLAYSO decider linked back to the original T20 match."""
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
        "origin_innings": [dict(innings_1), dict(innings_2)],
        "origin_pitch": str(origin_match.get("pitch") or "even"),
        "next_super_over_batting_user": int(innings_2["batting_team_id"]),
        "next_super_over_bowling_user": int(innings_2["bowling_team_id"]),
        "is_repeat_super_over": False,
        "previous_bowlers": {},
        "origin_xp_awarded": False,
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
    await start_setup(chat_id, dict(fresh), 1)
    return int(decider["match_id"])


async def _load_origin_session(engine: str, match_id: int):
    if engine == "PLAY":
        from engines.play_runtime import get_session
    elif engine == "PLAYINT":
        from engines.playint_runtime import get_playint_session as get_session
    elif engine == "PLAYIPL":
        from engines.playipl_runtime import get_playipl_session as get_session
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
    else:
        raise ValueError(engine)
    return _record_player_squad_stats, _award_match_xp_and_stats, _match_result_text


async def finalize_decider(decider_match: dict, so_history: list[dict], winner_id: int) -> None:
    """Finalize the originating match after a Super Over winner exists."""
    state = dict(decider_match.get("state") or {})
    origin_engine = str(state.get("origin_engine") or "")
    origin_match_id = int(state.get("origin_match_id") or 0)
    origin_match = None
    session = await _load_origin_session(origin_engine, origin_match_id)

    # The original match row is still the authoritative source for mentions and
    # metadata. If its session is present, use it. Otherwise the runtime resume
    # layer can repopulate the session before finalization.
    if session is not None:
        origin_match = dict(session.match)
    else:
        if origin_engine == "PLAY":
            from database.play_repo import get_match
        elif origin_engine == "PLAYINT":
            from database.playint_repo import get_match
        elif origin_engine == "PLAYIPL":
            from database.playipl_repo import get_match
        else:
            return
        origin_match = await get_match(origin_match_id)
    if not origin_match:
        return

    origin_innings = list(state.get("origin_innings") or [])
    if len(origin_innings) < 2:
        return
    innings_1, innings_2 = origin_innings[0], origin_innings[1]

    record_stats, award_stats, result_builder = _helpers(origin_engine)

    winner_id = int(winner_id)
    winner_display = _display(origin_match, winner_id)
    winner_mention = _display(origin_match, winner_id)
    loser_id = int(origin_match["opponent_id"] if winner_id == int(origin_match["challenger_id"]) else origin_match["challenger_id"])
    loser_username, loser_name = _user_fields(origin_match, loser_id)

    # Keep the original match's result text structure. It gets the original
    # 20-over match highlights/POTM, with the winner headline rewritten to the
    # Super Over decision. Then append a compact Super Over decision block.
    try:
        base = result_builder(innings_1, innings_2)
        winner_team = (
            innings_1["batting_team_display"]
            if winner_id == int(innings_1["batting_team_id"])
            else innings_2["batting_team_display"]
        )
        base = base.replace("🤝 Match Tied!", f"🎉 {winner_team} XI won in Super Over!")
        so1 = so_history[0] if so_history else {}
        so2 = so_history[1] if len(so_history) > 1 else {}
        so_block = (
            "\n\n<b>╭━━〔 ⚡ SUPER OVER DECIDER 〕━━╮</b>\n\n"
            f"🏏 First Super Over ➤ {int(so1.get('runs') or 0)}/{int(so1.get('wickets') or 0)}"
            f" ({so1.get('over_text') or '0.0'} Ov)\n"
            f"🏏 Second Super Over ➤ {int(so2.get('runs') or 0)}/{int(so2.get('wickets') or 0)}"
            f" ({so2.get('over_text') or '0.0'} Ov)\n\n"
            f"🏆 Winner ➤ {html.escape(str(winner_team))} XI\n"
            "⚡ Result ➤ WON IN SUPER OVER\n\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯</b>"
        )
        final_text = base + so_block
        await app.send_message(int(origin_match["chat_id"]), final_text, parse_mode="HTML")
    except Exception as exc:
        print(f"[super_over_bridge] final result message failed: {exc!r}")

    try:
        await record_stats(session, innings_1, innings_2) if session is not None else None
    except Exception as exc:
        print(f"[super_over_bridge] player stats failed: {exc!r}")
    try:
        await award_stats(session, innings_1, innings_2) if session is not None else None
    except Exception as exc:
        print(f"[super_over_bridge] rewards/H2H failed: {exc!r}")

    try:
        from services.match_summary import send_match_summary, player_details
        if origin_engine == "PLAY":
            from engines.play_runtime import player_of_the_match
        elif origin_engine == "PLAYINT":
            from engines.playint_runtime import player_of_the_match
        else:
            from engines.playipl_runtime import player_of_the_match
        potm_name = player_of_the_match(innings_1, innings_2)
        await send_match_summary(
            app,
            int(origin_match["chat_id"]),
            [innings_1, innings_2],
            winner=winner_display,
            margin="IN SUPER OVER",
            potm=player_details([innings_1, innings_2], potm_name),
            caption=(
                __import__("services.match_rewards", fromlist=["build_result_caption"]).build_result_caption(
                    f"<a href=\"tg://user?id={winner_id}\">{html.escape(str(winner_display))}</a>",
                    f"<a href=\"tg://user?id={loser_id}\">{html.escape(str(loser_name or loser_username or loser_id))}</a>",
                )
            ),
        )
    except Exception as exc:
        print(f"[super_over_bridge] summary image failed: {exc!r}")

    try:
        if origin_engine == "PLAY":
            from database.play_repo import update_status
        elif origin_engine == "PLAYINT":
            from database.playint_repo import update_status
        else:
            from database.playipl_repo import update_status
        await update_status(origin_match_id, "completed")
    except Exception as exc:
        print(f"[super_over_bridge] failed to mark original match completed: {exc!r}")

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
