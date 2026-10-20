"""Mini App endpoints for the Giveaways page."""
from __future__ import annotations

from typing import Any

from database import giveaway_repo as repo
from services import giveaway as gw

from . import api as base_api


def _prize(card: dict) -> dict:
    return base_api._player_payload(dict(card), owned=False)


def _winner(g: dict) -> dict | None:
    if not g.get("winner_id"):
        return None
    return {"id": int(g["winner_id"]), "name": g.get("winner_name") or "Player",
            "username": g.get("winner_username")}


async def build_list(viewer) -> dict[str, Any]:
    await repo.ensure_schema()
    uid = int(viewer.id)
    live = await repo.list_live()
    finished = await repo.list_recent_finished(10)
    ids = [int(g["giveaway_id"]) for g in live]
    counts = await repo.entry_counts(ids + [int(g["giveaway_id"]) for g in finished])
    joined = await repo.user_entries(uid, ids)
    member = await gw.is_main_member(uid)
    link = await gw.main_group_link()

    items = []
    for g in live:
        gid = int(g["giveaway_id"])
        card = g["player"]
        req = await gw.requirements(uid, card, member=member)
        standing = None
        if gid in joined or g["status"] == "active":
            try:
                standing = await gw.my_standing(gid, uid)
            except Exception as exc:
                print(f"[giveaway_api] standing failed: {type(exc).__name__}: {exc}")
        start = gw._aware(g.get("start_at"))
        end = gw._aware(g.get("end_at"))
        items.append({
            "id": gid, "status": g["status"], "prize": _prize(card),
            "start_at": gw.iso(start), "end_at": gw.iso(end),
            "start_text": gw.fmt_ist(start), "end_text": gw.fmt_ist(end),
            "total_seconds": int((end - start).total_seconds()) if start and end else 0,
            "seconds_left": gw.seconds_left(end), "seconds_to_start": gw.seconds_left(start),
            "participants": counts.get(gid, 0), "joined": gid in joined,
            "requirements": req,
            "score": standing["score"] if standing else None,
            "chance": standing["chance"] if standing else None,
            "reason_text": gw.REASON_TEXT.get(req["reason"]) if req["reason"] else None,
        })
    history = [{
        "id": int(g["giveaway_id"]), "prize": _prize(g["player"]), "winner": _winner(g),
        "participants": int(g.get("entries_final") or counts.get(int(g["giveaway_id"]), 0)),
        "ended_at": gw.iso(g.get("ended_at")), "ended_text": gw.fmt_ist(g.get("ended_at")),
        "note": g.get("result_note"),
        "mine": int(g.get("winner_id") or 0) == uid,
    } for g in finished]
    return {"ok": True, "now": gw.iso(gw.now_utc()), "main_group": {"link": link, "member": member},
            "items": items, "history": history,
            "how": ["Be a member of the main group", "Tap Participate",
                    "More activity (matches, wins, claims, quests) = higher chance"]}


async def join(viewer, gid: int) -> tuple[int, dict]:
    await repo.ensure_schema()
    result = await gw.join(int(gid), int(viewer.id), getattr(viewer, "username", None), getattr(viewer, "first_name", None))
    status = result.get("status")
    out = {k: v for k, v in result.items() if k != "start_at"}
    out["ok"] = status in {"ok", "already"}
    return (200 if out["ok"] else 409), out
