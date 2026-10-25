"""Mini App endpoints for Auction Tournaments (list, hub, live auction room, bid, host controls).

Read paths hit the database directly (the same repos the bot uses); the two writes - bid and host control - go
through services.tournament_auction so rules and permissions are exactly the bot's.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database import tournament_auction_repo as ar
from database import tournament_repo as tr
from services import tournament_auction as auc
from services import tournament_core as tc
from services import tournament_prize as tp
from services import tournament_text as tx
from services import tournament_views as tv

_STATUS_ORDER = {"auction": 0, "live": 1, "registration": 2, "ready": 3, "completed": 4}


def _iso(dt: Any) -> str | None:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _money(lakh: Any) -> str:
    return tc.fmt_money(int(lakh or 0))


def _team(x: dict | None) -> dict | None:
    if not x:
        return None
    return {"no": int(x["team_no"]), "code": x.get("base_code"), "short": tx.team_short(x), "name": x.get("name"),
            "owner": x.get("owner_name"), "owner_id": x.get("owner_id"), "purse": int(x.get("purse_left") or 0), "purse_text": _money(x.get("purse_left")),
            "spent_text": _money(x.get("spent")), "squad": int(x.get("squad_count") or 0), "overseas": int(x.get("overseas_count") or 0)}


def _prize(t: dict) -> dict | None:
    pr = t.get("prize") or {}
    if pr.get("state") not in ("held", "paid") or not (pr.get("coins") or pr.get("rubies") or pr.get("card")):
        return None
    parts = []
    for role, pct in tp.parts_of(pr).items():
        parts.append({"role": tp.ROLE_LABEL[role], "pct": pct, "coins": int(pr.get("coins") or 0) * pct // 100, "rubies": int(pr.get("rubies") or 0) * pct // 100})
    return {"coins": int(pr.get("coins") or 0), "rubies": int(pr.get("rubies") or 0), "card": pr.get("card_label") or None,
            "card_image": (pr.get("card") or {}).get("card_image_url") or (pr.get("card") or {}).get("image_url"),
            "split": tp.split_label(pr), "parts": parts, "state": pr["state"], "by_owner": bool(pr.get("minted")),
            "paid": [{"name": x.get("name"), "coins": x.get("coins"), "rubies": x.get("rubies"), "roles": [tp.ROLE_LABEL.get(r, r) for r in x.get("roles", [])]} for x in pr.get("paid", [])]}


def _card(t: dict) -> dict:
    prof = tc.profile(t["engine"])
    return {"id": int(t["tid"]), "name": t["name"], "kind": t["kind"], "engine": t["engine"], "preset": t["preset"], "icon": prof["icon"],
            "title": prof["title"], "mode": t["mode"], "status": t["status"], "max_teams": int(t["max_teams"]),
            "window": tc.fmt_window(int(t["window_start_min"]), int(t["window_end_min"])), "group": t.get("host_chat_title"),
            "champion_team_no": t.get("champion_team_no"), "created_at": _iso(t.get("created_at")), "prize": _prize(t)}


async def build_list(viewer) -> dict[str, Any]:
    uid = int(viewer.id)
    mine = await tr.list_for_user(uid, 40)
    official = await tr.list_official(30)
    seen: set[int] = set()
    items = []
    for t in mine + official:
        if int(t["tid"]) in seen:
            continue
        seen.add(int(t["tid"]))
        team = await tr.team_of_user(int(t["tid"]), uid)
        c = _card(t)
        c.update({"mine": bool(team) or int(t["creator_id"]) == uid, "my_team": _team(team), "teams": len(await tr.teams(int(t["tid"])))})
        items.append(c)
    items.sort(key=lambda x: (_STATUS_ORDER.get(x["status"], 9), -x["id"]))
    return {"ok": True, "now": _iso(datetime.now(timezone.utc)), "items": items, "career": await tr.career(uid)}


async def _fixture_rows(tid: int, teams: dict[int, dict]) -> list[dict]:
    out = []
    for f in await tr.fixtures(tid):
        h, a = teams.get(int(f["home_team_no"])), teams.get(int(f["away_team_no"]))
        out.append({"fno": int(f["fno"]), "stage": f["stage"], "stage_label": tc.STAGE_LABELS.get(f["stage"], f["stage"]), "day": int(f["day_no"]),
                    "status": f["status"], "home": _team(h), "away": _team(a), "result_type": f.get("result_type"), "result_text": f.get("result_text"),
                    "winner_team_no": f.get("winner_team_no"), "potm": f.get("potm"), "engine": f.get("engine"), "match_id": f.get("match_id"),
                    "home_score": tc.score_text(f["home_runs"], f["home_wkts"], f["home_balls"]) if f.get("home_balls") else None,
                    "away_score": tc.score_text(f["away_runs"], f["away_wkts"], f["away_balls"]) if f.get("away_balls") else None,
                    "ready_home": bool(f.get("ready_home")), "ready_away": bool(f.get("ready_away"))})
    return out


async def build_detail(viewer, tid: int) -> dict[str, Any] | None:
    t = await tr.get(tid)
    if t is None or t["status"] in ("setup", "cancelled"):
        return None
    uid = int(viewer.id)
    teams_l = await tr.teams(tid)
    teams = {int(x["team_no"]): x for x in teams_l}
    fixtures = await _fixture_rows(tid, teams)
    table = tc.table_from_rows(teams_l, await tr.fixtures(tid)) if fixtures else []
    mine = await tr.team_of_user(tid, uid)
    days = [{"day": int(d["day_no"]), "date": d["play_date"].isoformat(), "status": d["status"], "opens_at": _iso(d["opens_at"]), "closes_at": _iso(d["closes_at"])} for d in await tr.days(tid)]
    return {"ok": True, "now": _iso(datetime.now(timezone.utc)), "tournament": _card(t), "can_control": auc.can_control(t, uid),
            "my_team": _team(mine), "teams": [_team(x) for x in teams_l], "fixtures": fixtures, "days": days,
            "table": [{"pos": i, "team": _team(teams.get(int(r["team_no"]))), "played": r["played"], "won": r["won"], "lost": r["lost"], "nr": r["nr"],
                       "points": r["points"], "nrr": tc.fmt_nrr(r["nrr"]), "form": r["form"][-5:]} for i, r in enumerate(table, start=1)],
            "orange": await _board(tid, "orange", teams, 5), "purple": await _board(tid, "purple", teams, 5), "mvp": await _board(tid, "mvp", teams, 5),
            "champion": _team(teams.get(int(t["champion_team_no"]))) if t.get("champion_team_no") else None,
            "runner_up": _team(teams.get(int(t["runner_up_team_no"]))) if t.get("runner_up_team_no") else None,
            "auction_live": t["status"] == "auction"}


async def _board(tid: int, board: str, teams: dict[int, dict], limit: int = 10) -> list[dict]:
    rows = await tr.leaders(tid, board, limit)
    out = []
    for r in rows:
        d = {k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v)) for k, v in dict(r).items()}
        d["team"] = tx.team_short(teams.get(int(r.get("team_no") or 0)))
        out.append(d)
    return out


async def build_board(tid: int, name: str) -> dict[str, Any] | None:
    if name not in tv.BOARD_TITLES or await tr.get(tid) is None:
        return None
    teams = {int(x["team_no"]): x for x in await tr.teams(tid)}
    icon, title = tv.BOARD_TITLES[name]
    return {"ok": True, "board": name, "icon": icon, "title": title, "rows": await _board(tid, name, teams, 15)}


async def build_squad(tid: int, team_no: int) -> dict[str, Any]:
    rows = await ar.squad(tid, team_no)
    return {"ok": True, "players": [{"name": r["name"], "role": r["role"], "role_icon": tx.role_icon(r["role"]), "price_text": _money(r.get("price_lakh")),
                                     "xi": bool(r.get("xi")), "overseas": bool(r.get("overseas"))} for r in rows]}


async def build_squads(tid: int) -> dict[str, Any] | None:
    t = await tr.get(tid)
    if t is None or t["status"] in ("setup", "cancelled"):
        return None
    squads = await ar.all_squads(tid)
    out = []
    for x in await tr.teams(tid):
        rows = squads.get(int(x["team_no"]), [])
        out.append({**_team(x), "spent_lakh": sum(int(r.get("price_lakh") or 0) for r in rows),
                    "players": [{"name": r["name"], "role": r["role"], "role_icon": tx.role_icon(r["role"]), "price_text": _money(r.get("price_lakh")),
                                 "xi": bool(r.get("xi")), "overseas": bool(r.get("overseas"))} for r in rows]})
    return {"ok": True, "teams": out}


async def build_auction(viewer, tid: int) -> dict[str, Any] | None:
    t = await tr.get(tid)
    if t is None:
        return None
    uid = int(viewer.id)
    a = await ar.get_auction(tid)
    teams_l = await tr.teams(tid)
    mine = next((x for x in teams_l if x.get("owner_id") and int(x["owner_id"]) == uid), None)
    now = datetime.now(timezone.utc)
    out: dict[str, Any] = {"ok": True, "now": _iso(now), "tournament": _card(t), "can_control": auc.can_control(t, uid), "my_team": _team(mine),
                           "teams": [_team(x) for x in teams_l], "status": a["status"] if a else "idle", "version": int(a["version"]) if a else 0,
                           "squad_max": int(t["squad_max"]), "squad_min": int(t["squad_min"]),
                           "ladder": [list(r) for r in tc.ladder_rows(t.get("ladder"))]}
    if not a:
        return out
    out["counts"] = await ar.counts(tid)
    out["total_lots"] = int(a.get("total_lots") or 0)
    out["round"] = int(a.get("round") or 1)
    out["paused"] = a["status"] == "paused"
    out["next_lot_at"] = _iso(a.get("next_lot_at"))
    if a.get("cur_ord") is not None:
        lot = await ar.lot_view(tid, int(a["cur_ord"]))
        if lot:
            has_bid = int(lot.get("bid_count") or 0) > 0
            price = tc.next_bid(int(lot.get("cur_bid_lakh") or 0), int(lot.get("base_lakh") or 0), has_bid, t.get("ladder"))
            left = None
            if lot.get("deadline_at") is not None and lot["status"] == "live":
                left = max(0.0, (lot["deadline_at"] - now).total_seconds()) if a["status"] == "running" else (a.get("paused_left_ms") or 0) / 1000
            elif a["status"] == "paused" and a.get("paused_left_ms") is not None:
                left = a["paused_left_ms"] / 1000
            ok, why = (True, "")
            if mine is not None and lot["status"] == "live":
                try:
                    ok, why = tc.check_bid_allowed(mine, lot, price, {"squad_max": t["squad_max"], "overseas_squad_max": t["overseas_squad_max"], "squad_min": t["squad_min"], "min_base": t["min_base_lakh"]})
                except Exception:
                    ok, why = True, ""
            out["lot"] = {"ord": int(lot["ord"]), "name": lot["name"], "role": lot["role"], "role_label": tx.role_label(lot["role"]), "role_icon": tx.role_icon(lot["role"]),
                          "country": lot.get("country"), "overseas": bool(lot.get("overseas")), "bat": int(lot.get("bat_level") or 0), "bowl": int(lot.get("bowl_level") or 0),
                          "ovr": tc.player_ovr(lot), "bat_hand": lot.get("batting_hand"), "bowl_hand": lot.get("bowling_hand"),
                          "set": lot.get("set_name"), "set_pos": int(lot.get("set_pos") or 1), "set_size": int(lot.get("set_size") or 1), "pool": lot.get("pool_name"),
                          "status": lot["status"], "base_text": _money(lot.get("base_lakh")), "cur_lakh": int(lot.get("cur_bid_lakh") or 0), "cur_text": _money(lot.get("cur_bid_lakh") if has_bid else lot.get("base_lakh")),
                          "has_bid": has_bid, "bid_count": int(lot.get("bid_count") or 0), "next_lakh": price, "next_text": _money(price),
                          "leader_team_no": lot.get("leader_team_no"), "leader_short": tx.team_short({"short": lot.get("leader_short"), "base_code": lot.get("leader_base")}) if lot.get("leader_team_no") else None,
                          "leader_owner": lot.get("leader_owner"), "seconds_left": left, "round": int(lot.get("round") or 1),
                          "card_image": ((lot.get("card") or {}).get("image") if isinstance(lot.get("card"), dict) else None),
                          "can_bid": bool(mine) and lot["status"] == "live" and a["status"] == "running" and ok and int(lot.get("leader_team_no") or 0) != int((mine or {}).get("team_no") or -1),
                          "bid_block": why if (mine and not ok) else "",
                          "bids": [{"team": tx.team_short({"short": b.get("short"), "base_code": b.get("base_code")}), "amount_text": _money(b["amount_lakh"]), "owner": b.get("owner_name")} for b in await ar.bids(tid, int(lot["ord"]), 6)]}
    out["recent"] = [{"ord": int(r["ord"]), "name": r["name"], "role": r["role"], "status": r["status"], "price_text": _money(r.get("sold_lakh")) if r["status"] == "sold" else "Unsold",
                      "team": r.get("base_code")} for r in await ar.recent_sales(tid, 10)]
    out["upcoming"] = [{"ord": int(r["ord"]), "name": r["name"], "role": r["role"], "base_text": _money(r["base_lakh"]), "set": r.get("set_name")} for r in await ar.upcoming(tid, 6)]
    out["sets"] = [{"name": r["set_name"], "pool": r["pool_name"], "total": int(r["total"]), "done": int(r["done"] or 0)} for r in await ar.set_progress(tid)]
    return out


async def place_bid(viewer, tid: int, ord_: int, price: int) -> tuple[int, dict]:
    res = await auc.bid(tid, ord_, int(viewer.id), expected_lakh=price)
    return (200 if res.get("ok") else 409), {**{k: v for k, v in res.items() if isinstance(v, (int, float, str, bool, type(None)))}, "message": tx.bid_feedback(res)}


async def control(viewer, tid: int, action: str) -> tuple[int, dict]:
    res = await auc.control(tid, int(viewer.id), action)
    return (200 if res.get("ok") else 403), res


async def handle(viewer, method: str, path: str, query: dict, payload: dict) -> tuple[int, dict]:
    parts = [p for p in path.split("/") if p][2:]       # after api/tournaments
    try:
        if not parts and method == "GET":
            return 200, await build_list(viewer)
        tid = int(parts[0])
        sub = parts[1] if len(parts) > 1 else ""
        if method == "GET":
            if sub == "":
                data = await build_detail(viewer, tid)
            elif sub == "auction":
                data = await build_auction(viewer, tid)
            elif sub == "board":
                data = await build_board(tid, str(query.get("name") or "orange"))
            elif sub == "squads":
                data = await build_squads(tid)
            elif sub == "squad":
                data = await build_squad(tid, int(query.get("team") or 0))
            else:
                data = None
            return (200, data) if data is not None else (404, {"ok": False, "detail": "Not found"})
        if sub == "bid":
            return await place_bid(viewer, tid, int(payload.get("ord") or 0), int(payload.get("price") or 0))
        if sub == "control":
            return await control(viewer, tid, str(payload.get("action") or ""))
    except (ValueError, IndexError):
        return 400, {"ok": False, "detail": "Bad request"}
    return 404, {"ok": False, "detail": "Not found"}
