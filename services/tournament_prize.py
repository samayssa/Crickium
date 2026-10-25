"""Prize pool of a tournament: coins, rubies and (optionally) one player card.

* A normal creator must really own what he puts up: the coins, the rubies and the exact card are taken from him
  the moment the prize is set (held in escrow on the tournament) and handed back if the tournament is cancelled
  or the prize is replaced.
* The bot owner / admin does not need to own anything: the prize is simply created when it is paid out.
* When the tournament ends the pool is divided automatically (see SPLITS). The card goes to the champion.
"""
from __future__ import annotations

import json
import re
from typing import Any

from config import ADMIN_USER_ID
from database import tournament_repo as tr
from database.query import transaction

SETTABLE = ("setup", "registration", "auction", "ready")
MAX_SQUAD = 25
SPLITS: dict[str, dict[str, Any]] = {
    "winner": {"label": "🥇 Winner takes all", "parts": {"champion": 100}},
    "top2": {"label": "🥇🥈 Champion 65 · Runner-up 35", "parts": {"champion": 65, "runner_up": 35}},
    "classic": {"label": "🏆 Champion 50 · Runner-up 25 · Orange 10 · Purple 10 · MVP 5",
                "parts": {"champion": 50, "runner_up": 25, "orange": 10, "purple": 10, "mvp": 5}},
    "full": {"label": "🎖 Every award (teams + caps + records)",
             "parts": {"champion": 30, "runner_up": 18, "third": 9, "fourth": 6, "orange": 8, "purple": 8, "mvp": 6,
                       "fifties": 4, "hundreds": 4, "sr": 3, "sixes": 4}},
}
DEFAULT_SPLIT = "full"
ROLE_LABEL = {"champion": "🥇 Champion", "runner_up": "🥈 Runner-up", "third": "🥉 3rd place", "fourth": "4️⃣ 4th place",
              "orange": "🟠 Orange Cap", "purple": "🟣 Purple Cap", "mvp": "⭐ MVP", "fifties": "5️⃣0️⃣ Most 50s",
              "hundreds": "💯 Most 100s", "sr": "📈 Best Strike Rate", "sixes": "🚀 Most Sixes"}
ROLE_ALIAS = {"champion": "champion", "winner": "champion", "runner": "runner_up", "runnerup": "runner_up", "runner_up": "runner_up",
              "second": "runner_up", "third": "third", "3rd": "third", "fourth": "fourth", "4th": "fourth", "orange": "orange",
              "purple": "purple", "mvp": "mvp", "fifties": "fifties", "50s": "fifties", "hundreds": "hundreds", "100s": "hundreds",
              "sr": "sr", "strike": "sr", "sixes": "sixes", "6s": "sixes"}
BOARD_OF = {"orange": "orange", "purple": "purple", "mvp": "mvp", "fifties": "fifties", "hundreds": "hundreds", "sr": "sr", "sixes": "sixes"}


def split_label(prize: dict | None) -> str:
    pr = prize or {}
    if pr.get("split") == "custom":
        return "✍️ Custom split by the host"
    return SPLITS.get(pr.get("split") or DEFAULT_SPLIT, SPLITS[DEFAULT_SPLIT])["label"]


def parts_of(prize: dict | None) -> dict[str, int]:
    """The percentage of every award (a custom split of the host wins over the presets)."""
    pr = prize or {}
    custom = pr.get("parts")
    if isinstance(custom, dict) and custom:
        return {k: int(v) for k, v in custom.items() if k in ROLE_LABEL and int(v) > 0}
    return dict(SPLITS.get(pr.get("split") or DEFAULT_SPLIT, SPLITS[DEFAULT_SPLIT])["parts"])


def parse_custom_parts(text: str) -> tuple[dict[str, int] | None, str]:
    """'champion=40 runner=20 third=10 orange=10 ...' -> ({role: pct}, '') or (None, error)."""
    out: dict[str, int] = {}
    for tok in re.split(r"[\s,]+", text.strip()):
        if not tok:
            continue
        m = re.match(r"^([A-Za-z0-9_]+)\s*[=:]\s*(\d{1,3})%?$", tok)
        if not m or m.group(1).lower() not in ROLE_ALIAS:
            return None, f"I did not understand “{tok}”. Use names like champion, runner, third, fourth, orange, purple, mvp, 50s, 100s, sr, sixes."
        out[ROLE_ALIAS[m.group(1).lower()]] = int(m.group(2))
    if not out:
        return None, "No awards given."
    if sum(out.values()) != 100:
        return None, f"The percentages add up to {sum(out.values())} — they must add up to exactly 100."
    return out, ""


def is_admin(uid: Any) -> bool:
    return int(uid or 0) == int(ADMIN_USER_ID)


def _squad(raw: Any) -> list[dict]:
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    return [dict(p) for p in (raw or []) if isinstance(p, dict)]


def card_label(card: dict | None) -> str:
    if not card:
        return ""
    from services.card_identity import display_card_name
    return display_card_name(card)


def describe(prize: dict | None) -> str:
    """Plain-text lines (HTML-safe parts are escaped by the caller)."""
    if not prize or not (prize.get("coins") or prize.get("rubies") or prize.get("card")):
        return ""
    bits = []
    if prize.get("coins"):
        bits.append(f"🪙 {int(prize['coins']):,} coins")
    if prize.get("rubies"):
        bits.append(f"💎 {int(prize['rubies']):,} rubies")
    if prize.get("card"):
        bits.append(f"🃏 {prize.get('card_label') or card_label(prize['card'])}")
    return " · ".join(bits)


def shares(prize: dict, holders: dict[str, int | None]) -> dict[int, dict[str, Any]]:
    """{user_id: {coins, rubies, roles:[...]}}; a role nobody holds (no caps yet ...) falls to the champion."""
    parts = parts_of(prize)
    coins, rubies = int(prize.get("coins") or 0), int(prize.get("rubies") or 0)
    champ = holders.get("champion")
    out: dict[int, dict[str, Any]] = {}
    given_c = given_r = 0
    for role, pct in parts.items():
        uid = holders.get(role) or champ
        if not uid:
            continue
        c, r = coins * pct // 100, rubies * pct // 100
        given_c += c
        given_r += r
        e = out.setdefault(int(uid), {"coins": 0, "rubies": 0, "roles": []})
        e["coins"] += c
        e["rubies"] += r
        e["roles"].append(role)
    if champ:    # rounding leftovers
        e = out.setdefault(int(champ), {"coins": 0, "rubies": 0, "roles": ["champion"]})
        e["coins"] += coins - given_c
        e["rubies"] += rubies - given_r
    return out


async def _put_card(conn, uid: int, card: dict, force: bool = False) -> str:
    """'ok' | 'owns' | 'full' | 'no_squad'"""
    from services.card_identity import owned_same_card
    row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(uid))
    if not row:
        return "no_squad"
    squad = _squad(row["squad"])
    if any(owned_same_card(p, card) for p in squad):
        return "owns"
    if len(squad) >= MAX_SQUAD and not force:
        return "full"
    squad.append(card)
    await conn.execute("UPDATE team_squads SET squad=$1::jsonb, updated_at=NOW() WHERE user_id=$2;", json.dumps(squad, default=str), int(uid))
    return "ok"


async def _refund_tx(conn, prize: dict) -> None:
    if not prize or prize.get("state") != "held" or prize.get("minted") or not prize.get("funder_id"):
        return
    uid = int(prize["funder_id"])
    if prize.get("coins") or prize.get("rubies"):
        await conn.execute("UPDATE users SET balance=COALESCE(balance,0)+$2, rubies=COALESCE(rubies,0)+$3 WHERE user_id=$1;",
                           uid, int(prize.get("coins") or 0), int(prize.get("rubies") or 0))
    if prize.get("card"):
        await _put_card(conn, uid, prize["card"], force=True)


async def _store(conn, tid: int, prize: dict) -> None:
    await conn.execute("UPDATE tour_tournaments SET prize=$2::jsonb WHERE tid=$1;", int(tid), json.dumps(prize, default=str))


async def set_prize(tid: int, by_user: int, coins: int, rubies: int, card: dict | None, split: str | None = None) -> dict[str, Any]:
    """Create / replace the prize. Returns {"ok": True, "prize": {...}} or {"ok": False, "error": "..."}."""
    coins, rubies = max(0, int(coins or 0)), max(0, int(rubies or 0))
    if not (coins or rubies or card):
        return {"ok": False, "error": "Add at least coins, rubies or a card."}
    if split and split not in SPLITS:
        split = None
    admin = is_admin(by_user)
    entry = None
    if card:
        from services.giveaway import squad_entry
        entry = squad_entry(card)

    async def _tx(conn):
        t = await conn.fetchrow("SELECT status, creator_id, prize FROM tour_tournaments WHERE tid=$1 FOR UPDATE;", int(tid))
        if t is None:
            return {"ok": False, "error": "Tournament not found."}
        if t["status"] not in SETTABLE:
            return {"ok": False, "error": "The prize can only be set before the tournament starts."}
        if int(t["creator_id"]) != int(by_user) and not admin:
            return {"ok": False, "error": "Only the tournament host can set the prize."}
        old = tr._json(t["prize"], {})
        await _refund_tx(conn, old)
        if not admin:
            from services.card_identity import owned_same_card
            u = await conn.fetchrow("SELECT COALESCE(balance,0) AS balance, COALESCE(rubies,0) AS rubies FROM users WHERE user_id=$1 FOR UPDATE;", int(by_user))
            if u is None:
                raise _Abort("You need a Crickium account first (/debut).")
            if coins > int(u["balance"]):
                raise _Abort(f"You only have 🪙 {int(u['balance']):,} coins — you cannot put up 🪙 {coins:,}.")
            if rubies > int(u["rubies"]):
                raise _Abort(f"You only have 💎 {int(u['rubies']):,} rubies — you cannot put up 💎 {rubies:,}.")
            if entry:
                row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", int(by_user))
                squad = _squad(row["squad"]) if row else []
                keep = [p for p in squad if not owned_same_card(p, entry)]
                if len(keep) == len(squad):
                    raise _Abort(f"You do not own the card {card_label(entry)}. Only cards in your squad can be a prize.")
                await conn.execute("UPDATE team_squads SET squad=$1::jsonb, updated_at=NOW() WHERE user_id=$2;", json.dumps(keep, default=str), int(by_user))
            if coins or rubies:
                await conn.execute("UPDATE users SET balance=COALESCE(balance,0)-$2, rubies=COALESCE(rubies,0)-$3 WHERE user_id=$1;", int(by_user), coins, rubies)
        prize = {"coins": coins, "rubies": rubies, "card": entry, "card_label": card_label(entry) if entry else "",
                 "split": split or old.get("split") or DEFAULT_SPLIT, "funder_id": int(by_user), "minted": admin, "state": "held"}
        await _store(conn, tid, prize)
        return {"ok": True, "prize": prize}

    try:
        return await transaction(_tx)
    except _Abort as exc:
        return {"ok": False, "error": str(exc)}


class _Abort(Exception):
    pass


async def set_split(tid: int, by_user: int, split: str) -> dict[str, Any]:
    t = await tr.get(tid)
    if t is None or split not in SPLITS:
        return {"ok": False, "error": "Unknown option."}
    if int(t["creator_id"]) != int(by_user) and not is_admin(by_user):
        return {"ok": False, "error": "Only the tournament host can do this."}
    prize = dict(t.get("prize") or {})
    if prize.get("state") != "held":
        return {"ok": False, "error": "Set a prize first."}
    prize["split"] = split
    prize.pop("parts", None)
    await tr.update_fields(tid, prize=prize)
    return {"ok": True, "prize": prize}


async def set_custom_parts(tid: int, by_user: int, parts: dict[str, int]) -> dict[str, Any]:
    t = await tr.get(tid)
    if t is None:
        return {"ok": False, "error": "Tournament not found."}
    if int(t["creator_id"]) != int(by_user) and not is_admin(by_user):
        return {"ok": False, "error": "Only the tournament host can do this."}
    prize = dict(t.get("prize") or {})
    if prize.get("state") != "held":
        return {"ok": False, "error": "Set a prize first with /prize."}
    prize["parts"] = parts
    prize["split"] = "custom"
    await tr.update_fields(tid, prize=prize)
    return {"ok": True, "prize": prize}


async def refund(tid: int) -> bool:
    """Give an unpaid, held prize back to whoever funded it (tournament cancelled / draft deleted)."""
    async def _tx(conn):
        t = await conn.fetchrow("SELECT prize FROM tour_tournaments WHERE tid=$1 FOR UPDATE;", int(tid))
        if t is None:
            return False
        prize = tr._json(t["prize"], {})
        if prize.get("state") != "held":
            return False
        await _refund_tx(conn, prize)
        prize["state"] = "refunded"
        await _store(conn, tid, prize)
        return True
    return bool(await transaction(_tx))


async def payout(tid: int) -> dict[str, Any] | None:
    """Pay the pool out (exactly once). Returns {"lines": [...], "prize": {...}} or None when there is nothing to pay."""
    t = await tr.get(tid)
    if t is None:
        return None
    teams = {int(x["team_no"]): x for x in await tr.teams(tid)}

    def owner(team_no: Any) -> int | None:
        x = teams.get(int(team_no)) if team_no is not None else None
        return int(x["owner_id"]) if x and x.get("owner_id") else None

    holders: dict[str, int | None] = {"champion": t.get("champion_user_id") or owner(t.get("champion_team_no")),
                                      "runner_up": t.get("runner_up_user_id") or owner(t.get("runner_up_team_no"))}
    # 3rd / 4th: the loser of Qualifier 2 / the Eliminator; otherwise the next teams of the league table
    from services import tournament_core as tc
    fixtures = await tr.fixtures(tid)

    def stage_loser(stage: str) -> int | None:
        f = next((x for x in fixtures if x["stage"] == stage and x["status"] == "done" and x.get("winner_team_no") is not None), None)
        if f is None:
            return None
        return int(f["home_team_no"] if int(f["winner_team_no"]) == int(f["away_team_no"]) else f["away_team_no"])

    taken = {int(x) for x in (t.get("champion_team_no"), t.get("runner_up_team_no")) if x is not None}
    rest = [int(r["team_no"]) for r in tc.table_from_rows(list(teams.values()), fixtures) if int(r["team_no"]) not in taken]
    third_no = stage_loser("Q2") or (rest[0] if rest else None)
    fourth_no = stage_loser("EL") or next((n for n in rest if n != third_no), None)
    holders["third"], holders["fourth"] = owner(third_no), owner(fourth_no)
    caps: dict[str, str | None] = {}
    for role, board in BOARD_OF.items():
        rows = await tr.leaders(tid, board, 1)
        holders[role] = owner(rows[0]["team_no"]) if rows else None
        caps[role] = rows[0]["name"] if rows else None

    async def _tx(conn):
        row = await conn.fetchrow("SELECT prize FROM tour_tournaments WHERE tid=$1 FOR UPDATE;", int(tid))
        prize = tr._json(row["prize"], {}) if row else {}
        if prize.get("state") != "held":
            return None
        split = shares(prize, holders)
        names = {int(x["owner_id"]): (x.get("owner_name") or "Player") for x in teams.values() if x.get("owner_id")}
        lines, paid = [], []
        for uid, e in split.items():
            if e["coins"] or e["rubies"]:
                await conn.execute("UPDATE users SET balance=COALESCE(balance,0)+$2, rubies=COALESCE(rubies,0)+$3, "
                                   "total_rubies_earned=COALESCE(total_rubies_earned,0)+$3 WHERE user_id=$1;", uid, e["coins"], e["rubies"])
            got = " · ".join(x for x in (f"🪙 {e['coins']:,}" if e["coins"] else "", f"💎 {e['rubies']:,}" if e["rubies"] else "") if x)
            roles = ", ".join(ROLE_LABEL[r] + (f" ({caps[r]})" if caps.get(r) else "") for r in e["roles"])
            lines.append(f"{names.get(uid, 'Player')} — {got or '—'}  <i>{roles}</i>")
            paid.append({"user_id": uid, "name": names.get(uid), "coins": e["coins"], "rubies": e["rubies"], "roles": e["roles"]})
        champ = holders.get("champion")
        if prize.get("card") and champ:
            res = await _put_card(conn, int(champ), prize["card"])
            if res == "ok":
                lines.append(f"🃏 {prize.get('card_label')} → {names.get(int(champ), 'Champion')}")
            else:
                why = {"owns": "already owns it", "full": "squad is full", "no_squad": "has no squad"}[res]
                if not prize.get("minted") and prize.get("funder_id"):
                    await _put_card(conn, int(prize["funder_id"]), prize["card"], force=True)
                    lines.append(f"🃏 {prize.get('card_label')} — champion {why}, card returned to the host")
                else:
                    lines.append(f"🃏 {prize.get('card_label')} — champion {why}")
        prize["state"] = "paid"
        prize["paid"] = paid
        await _store(conn, tid, prize)
        return {"lines": lines, "prize": prize}

    return await transaction(_tx)
