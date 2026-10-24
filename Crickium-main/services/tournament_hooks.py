"""Match results flowing back into the tournament.

:func:`on_match_report` is called at the very top of ``services.match_analysis.sender.send_report`` - the one
funnel every finished, exited, timed-out or abandoned match of the four engines passes through (a Super Over
decider reports through its origin match). It never raises.
"""
from __future__ import annotations

import html
import json
import traceback
from typing import Any

from database import tournament_repo as tr
from services import tournament_core as tc
from services import tournament_text as tx
from services import tournament_tg as _tg

ENGINES = {"PLAY", "PLAYIPL", "PLAYWPL", "PLAYINT"}


async def _origin_of_decider(payload: dict) -> tuple[str, int] | None:
    from database.query import fetchrow
    row = await fetchrow("SELECT state FROM playso_matches WHERE match_id=$1;", int(payload.get("match_id") or 0))
    if not row:
        return None
    state = row["state"]
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except ValueError:
            return None
    state = state or {}
    if state.get("origin_engine") and state.get("origin_match_id"):
        return str(state["origin_engine"]).upper(), int(state["origin_match_id"])
    return None


def result_card(t: dict, fx: dict, home: dict, away: dict, res: dict) -> str:
    stage = tc.STAGE_LABELS.get(str(fx.get("stage") or "league"), "Match")
    head = f"🏆 <b>{html.escape(str(t.get('name')))}</b> · {html.escape(stage)} · M{int(fx['fno'])}"
    lines = [head]
    if res.get("home_runs") is not None:
        lines.append(f"<b>{html.escape(tx.team_short(home))}</b> {tc.score_text(res['home_runs'], res['home_wkts'], res['home_balls'])}")
        lines.append(f"<b>{html.escape(tx.team_short(away))}</b> {tc.score_text(res['away_runs'], res['away_wkts'], res['away_balls'])}")
    lines.append(f"<blockquote>{html.escape(res.get('result_text') or 'Result recorded')}</blockquote>")
    if res.get("potm"):
        lines.append(f"🌟 Player of the Match: <b>{html.escape(str(res['potm']))}</b>")
    return "\n".join(lines)


async def on_match_report(payload: dict) -> None:
    try:
        await _handle(payload)
    except Exception as exc:
        print(f"[tournament/hooks] result processing failed: {exc!r}")
        traceback.print_exc()


async def _handle(payload: dict) -> None:
    engine = str(payload.get("engine") or "").upper()
    term = str(payload.get("termination") or "completed")
    if term == "live":
        return
    match_id = int(payload.get("match_id") or 0)
    if engine == "PLAYSO":
        if term == "completed":
            return                      # the origin match reports the final result (with the Super Over)
        origin = await _origin_of_decider(payload)
        if origin is None:
            return
        engine, match_id = origin
    if engine not in ENGINES or not match_id:
        return
    fx = await tr.fixture_by_match(engine, match_id)
    if fx is None or fx.get("status") != "live":
        return
    tid, fno = int(fx["tid"]), int(fx["fno"])
    t = await tr.get(tid)
    home, away = await tr.team(tid, int(fx["home_team_no"])), await tr.team(tid, int(fx["away_team_no"]))
    if t is None or home is None or away is None:
        return
    tg = _tg.get()
    chat_id = int(t["host_chat_id"])
    if term == "abandoned":             # an admin stopped the game: the fixture goes back on the schedule
        await tr.reset_fixture(tid, fno)
        await tg.send(chat_id, f"⚠️ <b>Match {fno}</b> was abandoned by an admin. It is back on the schedule — "
                               f"both players can use /tplay to play it again.", tx.markup([tx.hub_button(tid)]))
        _wake()
        return

    home_user, away_user = int(fx["home_user_id"]), int(fx["away_user_id"])
    by_team = {home_user: home, away_user: away}
    winner_user = int(payload.get("winner_id") or 0) or None
    winner_team = by_team[winner_user] if winner_user in by_team else None
    res: dict[str, Any] = {"winner_team_no": None, "winner_user_id": None}
    lines: list[dict] = []
    if term == "completed":
        ext = tc.extract_match(payload, {home_user: int(home["team_no"]), away_user: int(away["team_no"])})
        res.update(tc.fixture_scores(ext, int(home["team_no"]), int(away["team_no"])))
        lines = ext["lines"]
        if winner_team is not None:
            res.update(result_type="win", winner_team_no=int(winner_team["team_no"]), winner_user_id=winner_user)
            super_over = bool(payload.get("super_over_rounds")) or "super over" in str(payload.get("reason") or "").lower()
            margin = tc.match_margin(ext["innings"], int(winner_team["team_no"]), super_over)
            res["result_text"] = f"{tx.team_short(winner_team)} won {margin}".strip()
        else:
            res.update(result_type="tie", result_text="Match tied")
    else:                               # exited / timed_out: the player who stayed gets a walkover win
        if winner_team is None:
            res.update(result_type="no_result", result_text="No result")
        else:
            why = "opponent timed out" if term == "timed_out" else "opponent left the match"
            res.update(result_type="walkover", winner_team_no=int(winner_team["team_no"]), winner_user_id=winner_user,
                       result_text=f"{tx.team_short(winner_team)} won — {why}")
    potm = (payload.get("potm") or {}).get("name")
    res["potm"] = potm if potm and potm != "—" and term == "completed" else None
    done = await tr.finish_fixture(tid, fno, res, lines)
    if done is None:
        return
    buttons = [[tx.button("📊 Points table", f"trs:{tid}:table")], [tx.hub_button(tid)]]
    await tg.send(chat_id, result_card(t, done, home, away, res), tx.markup(*buttons))
    _wake()


def _wake() -> None:
    try:
        from services import tournament_runtime
        tournament_runtime.wake()
    except Exception:
        pass
