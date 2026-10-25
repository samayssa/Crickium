"""Tournament match days: schedule, "ready / present", launching matches, playoffs, completion.

Everything is driven by the database, so a restart loses nothing:

* a *day* opens at the start of its window (default 5 PM), a reminder tags both players of every unplayed
  match one hour before the end, and at the end the day closes: nobody present -> abandoned (0 points),
  one player present -> walkover, both present but not played (the group was busy) -> carried to the next day;
* players may play **whenever they like** (``/tplay``): their next unplayed fixture - from any day - starts as
  soon as both are ready and the group is free, inside the window or not;
* a match that is already running when the window closes is allowed to finish;
* if the bot was down while a window was open, the day is extended / moved so nobody is punished for it.
"""
from __future__ import annotations

import asyncio
import random
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from typing import Any

from database import tournament_repo as tr
from services import tournament_core as tc
from services import tournament_engines as te
from services import tournament_text as tx
from services import tournament_tg as _tg
from services import tournament_views as tv

POLL = 5.0
_WAKE: asyncio.Event | None = None
_WORKER: asyncio.Task | None = None
_LOCKS: dict[int, asyncio.Lock] = {}


def wake() -> None:
    if _WAKE is not None:
        _WAKE.set()


def _lock(tid: int) -> asyncio.Lock:
    return _LOCKS.setdefault(int(tid), asyncio.Lock())


def _utc(dt: Any) -> datetime:
    return dt if getattr(dt, "tzinfo", None) else dt.replace(tzinfo=timezone.utc)


async def _say(t: dict, text: str, markup: dict | None = None) -> int | None:
    try:
        return await _tg.get().send(int(t["host_chat_id"]), text, markup)
    except Exception as exc:
        print(f"[tournament/runtime] send failed for #{t.get('tid')}: {exc!r}")
        return None


def can_manage(t: dict | None, user_id: Any) -> bool:
    from services.tournament_auction import can_control
    return can_control(t, user_id)


# ---------------------------------------------------------------------------
# starting a tournament + the schedule
# ---------------------------------------------------------------------------
def _day_times(t: dict, day: date) -> tuple[datetime, datetime, datetime]:
    opens, closes = tc.window_bounds(day, int(t["window_start_min"]), int(t["window_end_min"]), int(t["tz_min"]))
    return opens, closes - timedelta(minutes=tc.REMIND_BEFORE_MIN), closes


async def _create_days(t: dict, day_nos: list[int], dates: dict[int, date] | None = None) -> None:
    start = tc.first_play_date(datetime.now(timezone.utc), int(t["window_start_min"]), int(t["window_end_min"]), int(t["tz_min"]))
    existing = {int(d["day_no"]) for d in await tr.days(int(t["tid"]))}
    cursor = start
    for n in sorted(set(day_nos)):
        if n in existing:
            continue
        day = (dates or {}).get(n) or cursor
        opens, remind, closes = _day_times(t, day)
        await tr.create_day(int(t["tid"]), n, day, opens, remind, closes)
        cursor = day + timedelta(days=1)


async def apply_schedule(tid: int, by_user: int, text: str) -> dict[str, Any]:
    """Replace the (untouched) schedule by the one in ``text`` (see tournament_core.parse_schedule_text)."""
    t = await tr.get(tid)
    if t is None:
        return {"ok": False, "error": "Tournament not found."}
    if not can_manage(t, by_user):
        return {"ok": False, "error": "Only the tournament host can change the schedule."}
    if t["status"] not in ("registration", "ready", "live"):
        return {"ok": False, "error": "The schedule can be set after teams have joined (and before matches are played)."}
    teams = await tr.teams(tid)
    matches, errors = tc.parse_schedule_text(text, teams)
    if errors:
        return {"ok": False, "error": "\n".join(errors[:8]) + (f"\n… and {len(errors) - 8} more" if len(errors) > 8 else "")}
    cleared = await tr.clear_schedule(tid)
    if cleared < 0:
        return {"ok": False, "error": "Matches have already started — the schedule can no longer be replaced."}
    rows = await tr.create_fixtures(tid, [{"stage": m["stage"], "day_no": m["day_no"], "seq": m["seq"], "home": m["home"], "away": m["away"]} for m in matches])
    dates: dict[int, date] = {}
    for m in matches:
        if m.get("date"):
            dates[int(m["day_no"])] = date.fromisoformat(m["date"])
    await _create_days(t, [int(m["day_no"]) for m in matches], dates)
    await _sync_fixture_users(tid)
    wake()
    return {"ok": True, "matches": len(rows), "days": len({int(m["day_no"]) for m in matches})}


async def _sync_fixture_users(tid: int) -> None:
    owners = {int(x["team_no"]): x.get("owner_id") for x in await tr.teams(tid)}
    for f in await tr.fixtures(tid):
        if f["status"] != "done":
            await tr.set_fixture_users(tid, int(f["fno"]), owners.get(int(f["home_team_no"])), owners.get(int(f["away_team_no"])))


async def schedule_file(tid: int) -> tuple[str, str]:
    """(file name, text) of the current schedule in the editable format."""
    t = await tr.get(tid) or {}
    teams, fixtures, days = await tr.teams(tid), await tr.fixtures(tid), await tr.days(tid)
    dates = {int(d["day_no"]): d["play_date"].isoformat() for d in days}
    return f"schedule_{tid}.txt", tc.render_schedule_text(fixtures, teams, dates)


async def _validate_start(t: dict, teams: list[dict]) -> str | None:
    prof = tc.profile(t["engine"])
    if len(teams) < int(prof.get("min_teams") or 2):
        return f"At least {prof.get('min_teams') or 2} teams are needed."
    missing = [tx.team_short(x) for x in teams if x.get("owner_id") is None]
    if missing:
        return "These teams have no owner: " + ", ".join(missing) + ". Assign an owner or remove the team."
    if t["mode"] == "direct":
        problems = []
        for team in teams:
            if t["engine"] == "PLAY":
                why = await te.custom_player_problem(int(team["owner_id"]))
            else:
                why = await te.franchise_squad_problem(t["engine"], team["code"])
            if why:
                problems.append(f"{tx.team_short(team)}: {why}")
        if problems:
            return "Squads are not ready —\n" + "\n".join(problems[:6])
    return None


async def start_tournament(tid: int, by_user: int) -> dict[str, Any]:
    async with _lock(tid):
        t = await tr.get(tid)
        if t is None:
            return {"ok": False, "error": "Tournament not found."}
        if not can_manage(t, by_user):
            return {"ok": False, "error": "Only the tournament host can start it."}
        allowed = ("ready",) if t["mode"] == "auction" else ("registration",)
        if t["status"] not in allowed:
            return {"ok": False, "error": "The auction must finish first." if t["mode"] == "auction" and t["status"] != "live" else "It cannot be started now."}
        teams = await tr.teams(tid)
        problem = await _validate_start(t, teams)
        if problem:
            return {"ok": False, "error": problem}
        claimed = await tr.cas_status(tid, list(allowed), "live", started_at=datetime.now(timezone.utc), current_day=1)
        if claimed is None:
            return {"ok": False, "error": "Already starting."}
        try:
            fixtures = await tr.fixtures(tid)
            if not fixtures:
                plan = tc.build_league_fixtures([int(x["team_no"]) for x in teams], int(t.get("legs") or 0) or None)
                await tr.create_fixtures(tid, [{"stage": "league", **p} for p in plan])
                fixtures = await tr.fixtures(tid)
            await _create_days(t, [int(f["day_no"]) for f in fixtures])
            await _align_days(t)
            await _sync_fixture_users(tid)
            await tr.touch_tick(tid)
        except Exception:
            traceback.print_exc()
            await tr.cas_status(tid, ["live"], allowed[0])
            return {"ok": False, "error": "Could not build the schedule."}
    t = await tr.get(tid) or t
    days = await tr.days(tid)
    first = days[0]["play_date"] if days else None
    await _say(t, f"🏆 <b>{tx.esc(t['name'])}</b> has started!\n"
                  f"<blockquote>👥 {len(teams)} teams · {len(fixtures)} matches · {len(days)} match days\n"
                  f"🕔 {tx.esc(tc.fmt_window(int(t['window_start_min']), int(t['window_end_min'])))} daily"
                  + (f" · first day <b>{tx.esc(tc.fmt_day(first))}</b>" if first else "") + "</blockquote>"
                  "Play any time with /tplay · see /tour for the table.",
               tx.markup([tx.hub_button(tid)]))
    wake()
    return {"ok": True, "matches": len(fixtures), "days": len(days)}


async def _align_days(t: dict) -> None:
    """Days built before the tournament started (manual schedule) must not lie in the past."""
    days = await tr.non_closed_days(int(t["tid"]))
    if not days:
        return
    today = tc.first_play_date(datetime.now(timezone.utc), int(t["window_start_min"]), int(t["window_end_min"]), int(t["tz_min"]))
    delta = (today - days[0]["play_date"]).days
    if delta > 0:
        for d in days:
            day = d["play_date"] + timedelta(days=delta)
            await tr.shift_day(int(t["tid"]), int(d["day_no"]), day, *_day_times(t, day))


# ---------------------------------------------------------------------------
# ready / present / play
# ---------------------------------------------------------------------------
async def find_for_user(chat_id: int | None, user_id: int) -> dict | None:
    """The live tournament this player is in - preferably the one hosted in this chat."""
    mine = [x for x in await tr.list_for_user(user_id, 20) if x["status"] == "live"]
    for x in mine:
        if chat_id and int(x["host_chat_id"] or 0) == int(chat_id) and await tr.team_of_user(int(x["tid"]), user_id):
            return x
    for x in mine:
        if await tr.team_of_user(int(x["tid"]), user_id):
            return x
    return None


async def ready(tid: int, user_id: int, fno: int | None = None) -> dict[str, Any]:
    """The player says "I want to play now". Starts the match when the opponent is ready too."""
    t = await tr.get(tid)
    if t is None or t["status"] != "live":
        return {"ok": False, "message": "This tournament is not running."}
    team = await tr.team_of_user(tid, user_id)
    if team is None:
        return {"ok": False, "message": "You do not own a team here."}
    fx = await tr.fixture(tid, fno) if fno else await tr.next_fixture_of_team(tid, int(team["team_no"]))
    if fx is None or fx["status"] == "done":
        return {"ok": False, "message": "You have no pending match."}
    if fx["status"] == "live":
        return {"ok": False, "message": "Your match is already being played."}
    home = int(fx["home_team_no"]) == int(team["team_no"])
    if not home and int(fx["away_team_no"]) != int(team["team_no"]):
        return {"ok": False, "message": "That match is not yours."}
    fx = await tr.set_presence(tid, int(fx["fno"]), "home" if home else "away", ready=True) or await tr.fixture(tid, int(fx["fno"]))
    other_no = int(fx["away_team_no"] if home else fx["home_team_no"])
    other = await tr.team(tid, other_no)
    if not (fx["ready_home"] and fx["ready_away"]):
        await tr.queue_fixture(tid, int(fx["fno"]))
        return {"ok": True, "state": "waiting", "fno": int(fx["fno"]), "opponent": other,
                "message": f"✅ You are ready for M{fx['fno']}. Waiting for {tx.team_short(other)} ({(other or {}).get('owner_name') or 'opponent'})."}
    started = await _launch_ready(t, int(fx["fno"]))
    return {"ok": True, "state": started, "fno": int(fx["fno"]), "opponent": other,
            "message": {"started": "🏏 Both ready — your match is starting!", "queued": "⏳ Both ready. The group is busy — your match starts automatically when it is free.",
                        "user_busy": "⏳ One of you is in another game. Finish it first — then the match starts.",
                        "error": "⚠️ Could not start the match. Try /tplay again."}.get(started, "⏳ Queued.")}


async def present(tid: int, fno: int, side: str, user_id: int) -> dict[str, Any]:
    """The "Present" button of the reminder: the right player only; counts as ready as well."""
    fx = await tr.fixture(tid, fno)
    if fx is None or fx["status"] != "scheduled":
        return {"ok": False, "message": "This match is no longer waiting."}
    want = fx["home_user_id"] if side == "h" else fx["away_user_id"]
    if int(want or 0) != int(user_id):
        return {"ok": False, "message": "This button is for the other player."}
    return await ready(tid, user_id, fno)


async def _launch_ready(t: dict, fno: int) -> str:
    """started | queued | user_busy | error"""
    tid = int(t["tid"])
    async with _lock(tid):
        fx = await tr.fixture(tid, fno)
        if fx is None or fx["status"] != "scheduled" or not (fx["ready_home"] and fx["ready_away"]):
            return "queued"
        home, away = await tr.team(tid, int(fx["home_team_no"])), await tr.team(tid, int(fx["away_team_no"]))
        hu, au = int(home["owner_id"]), int(away["owner_id"])
        state = await te.busy(int(t["host_chat_id"]), [hu, au])
        if state["chat"]:
            await tr.queue_fixture(tid, fno)
            return "queued"
        if state["users"]:
            await tr.queue_fixture(tid, fno)
            return "user_busy"
        claimed = await tr.claim_fixture_start(tid, fno, hu, au, int(t["host_chat_id"]), t["engine"])
        if claimed is None:
            return "queued"
        members = {int(m["user_id"]): m for m in await tr.members(tid)}
        hmem, amem = members.get(hu, {}), members.get(au, {})
        hmem = {"id": hu, "username": hmem.get("username") or home.get("owner_username"), "first_name": hmem.get("first_name") or home.get("owner_name")}
        amem = {"id": au, "username": amem.get("username") or away.get("owner_username"), "first_name": amem.get("first_name") or away.get("owner_name")}
        res = await te.launch(t, claimed, home, away, hmem, amem)
        if not res.get("ok"):
            await tr.release_fixture(tid, fno)
            await _say(t, f"⚠️ M{fno} could not be started ({tx.esc(res.get('error'))[:120]}). It stays on the schedule.")
            return "error"
        return "started"


# ---------------------------------------------------------------------------
# the worker
# ---------------------------------------------------------------------------
async def _teams_map(tid: int) -> dict[int, dict]:
    return {int(x["team_no"]): x for x in await tr.teams(tid)}


async def _open_day(t: dict, day: dict) -> None:
    if await tr.day_step(int(t["tid"]), int(day["day_no"]), "pending", "open", "opened_at") is None:
        return
    await tr.update_fields(int(t["tid"]), current_day=int(day["day_no"]))
    fixtures = [f for f in await tr.day_fixtures(int(t["tid"]), int(day["day_no"]))]
    teams = await _teams_map(int(t["tid"]))
    markup = tx.markup([tx.button("▶️ Ready to play", f"trr:{int(t['tid'])}", style="success")], [tx.hub_button(int(t["tid"]))])
    mid = await _say(t, tv.day_card(t, day, fixtures, teams), markup)
    if mid:
        await tr.set_day_card(int(t["tid"]), int(day["day_no"]), mid)


async def _remind(t: dict, day: dict) -> None:
    if await tr.mark_reminded(int(t["tid"]), int(day["day_no"])) is None:
        return
    teams = await _teams_map(int(t["tid"]))
    for f in await tr.day_fixtures(int(t["tid"]), int(day["day_no"])):
        if f["status"] != "scheduled":
            continue
        await _say(t, tv.reminder_text(t, f, teams[int(f["home_team_no"])], teams[int(f["away_team_no"])]), tv.present_markup(int(t["tid"]), f))
        await asyncio.sleep(1.2)


async def _close_day(t: dict, day: dict) -> None:
    tid, dno = int(t["tid"]), int(day["day_no"])
    if await tr.day_step(tid, dno, "open", "closed", "closed_at") is None:
        return
    teams = await _teams_map(tid)
    table = {int(r["team_no"]): i for i, r in enumerate(tc.table_from_rows(list(teams.values()), await tr.fixtures(tid)), start=1)}
    notes: list[str] = []
    carried: list[int] = []
    for f in await tr.day_fixtures(tid, dno):
        if f["status"] != "scheduled":
            continue
        h, a = teams[int(f["home_team_no"])], teams[int(f["away_team_no"])]
        if f["ready_home"] and f["ready_away"]:
            carried.append(int(f["fno"]))
            continue
        decision = tc.resolve_no_show(bool(f["present_home"]), bool(f["present_away"]), str(f["stage"]),
                                      table.get(int(h["team_no"]), 99), table.get(int(a["team_no"]), 99))
        label = f"M{f['fno']} {tx.team_short(h)} vs {tx.team_short(a)}"
        if decision["action"] == "walkover" or (decision["action"] == "abandoned" and decision.get("winner")):
            win, lose = (h, a) if decision["winner"] == "home" else (a, h)
            kind = "walkover"
            text = f"{tx.team_short(win)} won by walkover" if decision["action"] == "walkover" else f"{tx.team_short(win)} advances (better seed)"
            await tr.finish_fixture(tid, int(f["fno"]), {"result_type": kind, "winner_team_no": int(win["team_no"]), "winner_user_id": win["owner_id"], "result_text": text}, [])
            notes.append(f"🏳️ {tx.esc(label)} — {tx.esc(text)}")
        else:
            await tr.finish_fixture(tid, int(f["fno"]), {"result_type": "abandoned", "result_text": "Abandoned — nobody showed up"}, [])
            notes.append(f"❌ {tx.esc(label)} — abandoned, 0 points each")
    if carried:
        nxt = [d for d in await tr.non_closed_days(tid) if int(d["day_no"]) != dno]
        target = int(nxt[0]["day_no"]) if nxt else dno + 1
        if not nxt:
            last = day["play_date"] + timedelta(days=1)
            await tr.create_day(tid, target, last, *_day_times(t, last))
        await tr.move_fixtures_to_day(tid, carried, target)
        notes.append(f"⏭ {len(carried)} match(es) where both were ready but the group was busy move to day {target}.")
    head = f"⏰ <b>Match Day {dno} is over</b>"
    await _say(t, head + ("\n" + "\n".join(notes) if notes else "\nAll matches of the day were played. 👏"), tx.markup([tx.button("📊 Points table", f"trs:{tid}:table")]))


async def _after_downtime(t: dict, last: datetime, now: datetime) -> None:
    days = await tr.non_closed_days(int(t["tid"]))
    if not days:
        return
    first = days[0]
    opens, closes = _utc(first["opens_at"]), _utc(first["closes_at"])
    if closes <= now:                       # the whole window passed while the bot was away: start over today/tomorrow
        today = tc.first_play_date(now, int(t["window_start_min"]), int(t["window_end_min"]), int(t["tz_min"]))
        delta = (today - first["play_date"]).days
        for d in days:
            day = d["play_date"] + timedelta(days=max(delta, 0))
            await tr.shift_day(int(t["tid"]), int(d["day_no"]), day, *_day_times(t, day))
        if first["status"] == "open":
            await tr.extend_day(int(t["tid"]), int(first["day_no"]), *_day_times(t, today)[1:])
    elif opens <= now and last < closes:    # a running window lost time: give it back (max 3 h)
        lost = min((now - max(last, opens)).total_seconds(), 3 * 3600)
        await tr.extend_day(int(t["tid"]), int(first["day_no"]), _utc(first["remind_at"]) + timedelta(seconds=lost), closes + timedelta(seconds=lost))
    await _say(t, "🛠 The bot was offline for a while — today's match window has been extended so nobody loses their time.")


async def _tick(tid: int) -> None:
    st = await tr.probe(tid)
    if st is None or st["status"] != "live":
        return
    t = await tr.get(tid)
    now = _utc(st["db_now"])
    last = st.get("last_tick_at")
    if last is not None and (now - _utc(last)).total_seconds() > 600:
        await _after_downtime(t, _utc(last), now)
        st = await tr.probe(tid) or st
    await tr.touch_tick(tid)
    day = st.get("day")
    if day:
        if day["status"] == "pending" and _utc(day["opens_at"]) <= now:
            await _open_day(t, day)
            day = {**day, "status": "open"}
        if day["status"] == "open":
            if day.get("reminded_at") is None and _utc(day["remind_at"]) <= now < _utc(day["closes_at"]):
                await _remind(t, day)
            if _utc(day["closes_at"]) <= now:
                await _close_day(t, day)
    if int(st.get("queued") or 0):
        for f in await tr.ready_fixtures(tid):
            await _launch_ready(t, int(f["fno"]))
    if int(st.get("undone") or 0) == 0 or day is None:
        await _progress(t)


# ---------------------------------------------------------------------------
# playoffs + the end
# ---------------------------------------------------------------------------
async def _progress(t: dict) -> None:
    tid = int(t["tid"])
    async with _lock(tid):
        t = await tr.get(tid)
        if t is None or t["status"] != "live":
            return
        fixtures = await tr.fixtures(tid)
        teams = list(await tr.teams(tid))
        for f in fixtures:      # a knockout with no result is played again
            if f["stage"] != "league" and f["status"] == "done" and f["result_type"] in ("no_result", "tie"):
                await tr.reset_fixture(tid, int(f["fno"]))
                return
        if any(f["status"] != "done" for f in fixtures):
            return
        league = [f for f in fixtures if f["stage"] == "league"]
        plan = tc.playoff_plan(len(teams))
        knock = {f["stage"]: f for f in fixtures if f["stage"] != "league"}
        if not t.get("league_done") and league:
            await tr.update_fields(tid, league_done=True)
            table = tc.table_from_rows(teams, fixtures)
            await _say(t, "🏁 <b>League stage complete!</b>\n" + tv.table_text(t, table, len(league), len(league)),
                       tx.markup([tx.button("🟠 Orange", f"trs:{tid}:orange"), tx.button("🟣 Purple", f"trs:{tid}:purple"), tx.button("⭐ MVP", f"trs:{tid}:mvp")]))
        done = {s: {"winner": f["winner_team_no"], "loser": int(f["home_team_no"] if f["winner_team_no"] == f["away_team_no"] else f["away_team_no"])}
                for s, f in knock.items() if f["winner_team_no"] is not None}
        if plan["stages"] and "F" in done:
            await _complete(t, int(done["F"]["winner"]), int(done["F"]["loser"]))
            return
        if not plan["stages"]:       # two teams: the league leader is the champion
            table = tc.table_from_rows(teams, fixtures)
            await _complete(t, int(table[0]["team_no"]), int(table[1]["team_no"]))
            return
        seeds = [int(r["team_no"]) for r in tc.table_from_rows(teams, fixtures)]
        new = tc.next_playoff_fixtures(plan, seeds, done, set(knock))
        if not new:
            return
        day_no = max(int(f["day_no"]) for f in fixtures)
        specs = []
        for item in new:
            if item["new_day"] or not specs:
                day_no += 1
            specs.append({"stage": item["stage"], "day_no": day_no, "seq": len(specs) + 1, "home": item["home"], "away": item["away"]})
        rows = await tr.create_fixtures(tid, specs)
        await _create_days(t, [int(r["day_no"]) for r in rows])
        await _sync_fixture_users(tid)
        by = await _teams_map(tid)
        lines = [f"• <b>{tx.esc(tc.STAGE_LABELS[r['stage']])}</b> — {tx.esc(tx.team_short(by[int(r['home_team_no'])]))} vs {tx.esc(tx.team_short(by[int(r['away_team_no'])]))} · day {r['day_no']}" for r in rows]
        await _say(t, "🔥 <b>Playoffs!</b>\n" + "\n".join(lines) + "\n<i>Play with /tplay as always.</i>", tx.markup([tx.hub_button(tid)]))


async def _complete(t: dict, champ_no: int, runner_no: int) -> None:
    tid = int(t["tid"])
    champ, runner = await tr.team(tid, champ_no), await tr.team(tid, runner_no)
    done = await tr.cas_status(tid, ["live"], "completed", completed_at=datetime.now(timezone.utc), champion_team_no=champ_no,
                               champion_user_id=(champ or {}).get("owner_id"), runner_up_team_no=runner_no,
                               runner_up_user_id=(runner or {}).get("owner_id"))
    if done is None:
        return
    text = tv.champion_text(t, champ, runner)
    prizes = await _pay_prizes(t, champ, runner)
    await _say(t, text + (("\n" + prizes) if prizes else ""),
               tx.markup([tx.button("🟠 Orange", f"trs:{tid}:orange"), tx.button("🟣 Purple", f"trs:{tid}:purple"), tx.button("⭐ MVP", f"trs:{tid}:mvp")], [tx.hub_button(tid)]))


async def _pay_prizes(t: dict, champ: dict | None, runner: dict | None) -> str:
    """Divide the prize pool automatically (once). Returns the text block for the champion card."""
    from services import tournament_prize as tp
    try:
        res = await tp.payout(int(t["tid"]))
    except Exception:
        traceback.print_exc()
        return "⚠️ The prize could not be paid automatically — the host has been notified."
    if not res:
        return ""
    return "\n🎁 <b>Prize pool paid</b> — " + tx.esc(tp.describe(res["prize"])) + "\n<blockquote>" + "\n".join(res["lines"]) + "</blockquote>"


# ---------------------------------------------------------------------------
# worker plumbing
# ---------------------------------------------------------------------------
async def _worker() -> None:
    last_clean = 0.0
    while True:
        try:
            for tid in await tr.live_ids():
                try:
                    await _tick(tid)
                except Exception as exc:
                    print(f"[tournament/runtime] tick failed for #{tid}: {exc!r}")
                    traceback.print_exc()
            if time.monotonic() - last_clean > 3600:
                last_clean = time.monotonic()
                await _cleanup()
        except Exception as exc:
            print(f"[tournament/runtime] worker error: {exc!r}")
        try:
            await asyncio.wait_for(_WAKE.wait(), timeout=POLL)
        except asyncio.TimeoutError:
            pass
        _WAKE.clear()


async def _cleanup() -> None:
    """Auction squads of long finished tournaments leave playint_players again (the history stays in tour_squad)."""
    from database import tournament_auction_repo as ar
    for t in await tr.cleanup_candidates(3):
        try:
            if t["engine"] in te.FRANCHISE_ENGINES:
                codes = [x["code"] for x in await tr.teams(int(t["tid"]))]
                await ar.purge_engine_squads(te.ENGINE_KEY[t["engine"]], codes)
            await tr.update_fields(int(t["tid"]), cleaned=True)
        except Exception as exc:
            print(f"[tournament/runtime] cleanup of #{t['tid']} failed: {exc!r}")


def start_worker() -> asyncio.Task:
    global _WORKER, _WAKE
    if _WAKE is None:
        _WAKE = asyncio.Event()
    if _WORKER is None or _WORKER.done():
        _WORKER = asyncio.create_task(_worker())
    return _WORKER


async def stop_worker() -> None:
    global _WORKER
    if _WORKER is not None:
        _WORKER.cancel()
        try:
            await _WORKER
        except (asyncio.CancelledError, Exception):
            pass
        _WORKER = None
