"""The live auction: pool, auctioneer, group presenter and host controls.

Two halves that never block each other:

* the **auctioneer** (``_tick``) drives the database state machine once a second - open the next lot,
  call "going once / twice", bring the hammer down, start the accelerated round, finish the auction. It only
  talks to the database, so a Telegram flood-wait can never delay a hammer;
* the **presenter** (one per tournament) turns what the database says into the group's messages:
  the pinned player card (A), the live bidding message (B, edited on every bid) and the SOLD / UNSOLD result
  (B edited into the result). Every one of them carries the blue "Open Auction Room" Mini App button.

Everything is restart safe: lot rows remember which messages exist (a_msg_id / b_msg_id) and how far they have
been shown (``shown``), so a restarted bot simply carries on.
"""
from __future__ import annotations

import asyncio
import collections
import time
import traceback
from typing import Any

from config import ADMIN_USER_ID
from database import tournament_auction_repo as ar
from database import tournament_repo as tr
from services import tournament_core as tc
from services import tournament_text as tx
from services import tournament_tg as _tg

# --- tunables (tests shrink them) -----------------------------------------------------------------
INTRO_SECONDS = 45            # from "Start auction" to the first lot
OPENING_GRACE = 3             # the first bid window is a little longer: the group messages need a moment to appear
MIN_EDIT_GAP = 3.5            # seconds between two edits of the same live message
MIN_CALL_GAP = 1.2            # seconds between two Telegram calls into the same group
CALLS_PER_MINUTE_LOW = 14     # live-message edits are held back when the group already got this many calls this minute
CALLS_PER_MINUTE_HIGH = 19    # structural messages (new lot, result) may use the whole budget
POLL_BUSY = 1.0               # worker period while an auction is running
POLL_IDLE = 5.0

_ACTIVE: set[int] = set()
_WAKE: asyncio.Event | None = None
_WORKER: asyncio.Task | None = None
_PRESENTERS: dict[int, "Presenter"] = {}
_FINISHING: set[int] = set()


# ---------------------------------------------------------------------------
# permissions + small helpers
# ---------------------------------------------------------------------------
def is_admin(user_id: Any) -> bool:
    return int(user_id or 0) == int(ADMIN_USER_ID)


def can_control(tournament: dict | None, user_id: Any) -> bool:
    """The tournament's creator and the bot owner/admin may pause, resume and end the auction."""
    if not tournament:
        return False
    uid = int(user_id or 0)
    return bool(uid) and (uid == int(tournament.get("creator_id") or 0) or is_admin(uid))


def wake() -> None:
    if _WAKE is not None:
        _WAKE.set()


def host_markup_rows(tid: int) -> list[list[dict]]:
    """Row of host-only controls (shown under the pinned lot card)."""
    return [[tx.button("⏸ Pause", f"trh:{tid}:p"), tx.button("▶️ Resume", f"trh:{tid}:r"), tx.button("🛑 End", f"trh:{tid}:e", style="danger")]]


def lot_card_markup(tid: int) -> dict:
    return tx.markup([tx.room_button(tid)], *host_markup_rows(tid))


def ticker_markup(tid: int, lot: dict, paused: bool = False) -> dict:
    rows: list[list[dict]] = []
    if lot.get("status") == "live" and not paused:
        ladder = lot.get("_ladder")
        price = tc.next_bid(int(lot.get("cur_bid_lakh") or 0), int(lot.get("base_lakh") or 0), int(lot.get("bid_count") or 0) > 0, ladder)
        rows.append([tx.button(f"💰 Bid {tc.fmt_money(price)}", f"trb:{tid}:{int(lot['ord'])}:{price}", style="success")])
    rows.append([tx.room_button(tid)])
    return tx.markup(*rows)


# ---------------------------------------------------------------------------
# Telegram adapter (shared with the match-day worker; replaced by a fake in tests)
# ---------------------------------------------------------------------------
def set_telegram(adapter: Any) -> None:
    _tg.set_telegram(adapter)


# ---------------------------------------------------------------------------
# the presenter - what the group sees
# ---------------------------------------------------------------------------
class Presenter:
    def __init__(self, tid: int):
        self.tid = int(tid)
        self.queue: collections.deque = collections.deque()
        self.pending_changed: set[int] = set()
        self.wakeup = asyncio.Event()
        self.task: asyncio.Task | None = None
        self._tour: dict | None = None
        self._tour_at = 0.0
        self.last_edit_at: dict[int, float] = {}
        self.last_render: dict[int, str] = {}
        self.calls: collections.deque = collections.deque()
        self.last_call = 0.0
        self.paused = False
        self.seen_version: int | None = None
        self.seen_status: str | None = None
        self.recovered = False
        self.pin_warned = False

    # -- queueing -----------------------------------------------------------------------------------
    def _push(self, kind: str, arg: Any) -> None:
        self.queue.append((kind, arg))
        self.wakeup.set()
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())

    def new_lot(self, ord_: int) -> None:
        self._push("new", int(ord_))

    def changed(self, ord_: int, urgent: bool = False) -> None:
        if int(ord_) in self.pending_changed and not urgent:
            return
        self.pending_changed.add(int(ord_))
        self._push("changed", (int(ord_), bool(urgent)))

    def result(self, ord_: int, skipped: bool = False) -> None:
        self._push("result", (int(ord_), bool(skipped)))

    def say(self, text: str, markup: dict | None = None) -> None:
        self._push("text", (text, markup))

    async def flush(self, timeout: float = 30.0) -> None:
        """Wait until everything queued so far has been sent (used when finishing / in tests)."""
        end = time.monotonic() + timeout
        while (self.queue or (self.task and not self.task.done() and self._busy)) and time.monotonic() < end:
            await asyncio.sleep(0.05)

    _busy = False

    async def _run(self) -> None:
        while True:
            if not self.queue:
                self.wakeup.clear()
                try:
                    await asyncio.wait_for(self.wakeup.wait(), timeout=120)
                except asyncio.TimeoutError:
                    return                      # idle for two minutes: the task ends, the next push starts a new one
                continue
            kind, arg = self.queue.popleft()
            self._busy = True
            try:
                if kind == "new":
                    await self._show_new(arg)
                elif kind == "changed":
                    await self._show_changed(*arg)
                elif kind == "result":
                    await self._show_result(*arg)
                elif kind == "text":
                    await self._show_text(*arg)
            except Exception as exc:
                print(f"[tournament/auction] presenter {kind} failed for #{self.tid}: {exc!r}")
                traceback.print_exc()
            finally:
                self._busy = False

    # -- pacing -------------------------------------------------------------------------------------
    async def _pace(self, high: bool = True) -> None:
        budget = CALLS_PER_MINUTE_HIGH if high else CALLS_PER_MINUTE_LOW
        while True:
            now = time.monotonic()
            while self.calls and now - self.calls[0] > 60.0:
                self.calls.popleft()
            gap = MIN_CALL_GAP - (now - self.last_call)
            if gap > 0:
                await asyncio.sleep(gap)
                continue
            if len(self.calls) >= budget:
                await asyncio.sleep(min(2.0, max(0.2, 60.0 - (now - self.calls[0]))))
                continue
            break
        self.last_call = time.monotonic()
        self.calls.append(self.last_call)

    async def _tournament(self, fresh: bool = False) -> dict:
        if fresh or self._tour is None or time.monotonic() - self._tour_at > 15:
            row = await tr.get(self.tid)
            if row is not None:
                self._tour, self._tour_at = row, time.monotonic()
        return self._tour or {}

    async def _send(self, chat_id: int, text: str, markup: dict | None, high: bool = True) -> int | None:
        for attempt in range(3):
            await self._pace(high)
            try:
                return await _tg.get().send(chat_id, text, markup)
            except Exception as exc:
                print(f"[tournament/auction] send failed (#{self.tid}, try {attempt + 1}): {exc!r}")
                await asyncio.sleep(1.5 * (attempt + 1))
        return None

    async def _edit(self, chat_id: int, message_id: int, text: str, markup: dict | None, high: bool = False) -> bool:
        await self._pace(high)
        try:
            return bool(await _tg.get().edit(chat_id, message_id, text, markup))
        except Exception as exc:
            print(f"[tournament/auction] edit failed (#{self.tid}): {exc!r}")
            return False

    async def _pin(self, chat_id: int, message_id: int) -> None:
        await self._pace(True)
        try:
            await _tg.get().pin(chat_id, message_id)
        except Exception as exc:
            print(f"[tournament/auction] pin failed (#{self.tid}): {exc!r}")
            if not self.pin_warned:
                self.pin_warned = True
                try:
                    await ar.set_pinned(self.tid, None, failed=True)
                except Exception:
                    pass
                await self._send(chat_id, tx.pin_warning(), tx.markup([tx.room_button(self.tid)]))

    async def _unpin(self, chat_id: int, message_id: int) -> None:
        if self.pin_warned:
            return
        await self._pace(True)
        try:
            await _tg.get().unpin(chat_id, message_id)
        except Exception as exc:
            print(f"[tournament/auction] unpin failed (#{self.tid}): {exc!r}")

    # -- renderers ----------------------------------------------------------------------------------
    async def _view(self, ord_: int) -> tuple[dict | None, dict, dict | None]:
        t = await self._tournament()
        lot = await ar.lot_view(self.tid, ord_)
        auction = await ar.get_auction(self.tid) if lot is not None else None
        if lot is not None:
            lot["_ladder"] = t.get("ladder")
        return lot, t, auction

    @staticmethod
    def _leader(lot: dict) -> dict | None:
        if lot.get("leader_team_no") is None:
            return None
        return {"team_no": lot["leader_team_no"], "short": lot.get("leader_short"), "base_code": lot.get("leader_base"),
                "name": lot.get("leader_name"), "owner_name": lot.get("leader_owner")}

    async def _ticker_payload(self, lot: dict, t: dict, total: int) -> tuple[str, dict]:
        bids = await ar.recent_bids(self.tid, int(lot["ord"]), 3) if int(lot.get("bid_count") or 0) else []
        note = ""
        if self.paused:
            note = "⏸ <b>Auction paused</b>"
        elif int(lot.get("bid_count") or 0) > 0:
            note = tx.going_note("twice" if lot.get("said_twice") else "once" if lot.get("said_once") else "")
        text = tx.ticker(t, lot, total, self._leader(lot), bids, note)
        return text, ticker_markup(self.tid, lot, paused=self.paused)

    async def _show_new(self, ord_: int) -> None:
        lot, t, auction = await self._view(ord_)
        if lot is None or not t.get("host_chat_id"):
            return
        chat_id = int(t["host_chat_id"])
        total = int((auction or {}).get("total_lots") or 0)
        if lot["status"] not in ("live", "pending") and int(lot.get("shown") or 0) < 2:
            await self._show_result(ord_, False)         # settled before we got to show it: go straight to the result
            return
        if int(lot.get("shown") or 0) >= 2:
            return
        a_id = lot.get("a_msg_id")
        if not a_id:
            a_id = await self._send(chat_id, tx.lot_card(t, lot, total), lot_card_markup(self.tid))
            if a_id:
                await ar.set_lot_messages(self.tid, ord_, a_msg_id=a_id)
                await self._pin(chat_id, a_id)                     # the new player is pinned FIRST ...
        b_id = lot.get("b_msg_id")
        if not b_id:
            fresh = await ar.lot_view(self.tid, ord_) or lot
            fresh["_ladder"] = t.get("ladder")
            text, markup = await self._ticker_payload(fresh, t, total)
            b_id = await self._send(chat_id, text, markup)         # ... then the live bidding message
            if b_id:
                await ar.set_lot_messages(self.tid, ord_, b_msg_id=b_id)
                self.last_render[ord_] = text + repr(markup)
        self.last_edit_at[ord_] = time.monotonic()
        await ar.set_shown(self.tid, ord_, 1)

    async def _show_changed(self, ord_: int, urgent: bool) -> None:
        gap = MIN_CALL_GAP if urgent else MIN_EDIT_GAP
        wait = self.last_edit_at.get(ord_, 0.0) + gap - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self.pending_changed.discard(ord_)                       # events that arrive from here on need a new render
        lot, t, auction = await self._view(ord_)
        if lot is None or lot["status"] != "live" or not lot.get("b_msg_id") or not t.get("host_chat_id"):
            return
        total = int((auction or {}).get("total_lots") or 0)
        text, markup = await self._ticker_payload(lot, t, total)
        signature = text + repr(markup)
        if self.last_render.get(ord_) == signature:
            return
        if await self._edit(int(t["host_chat_id"]), int(lot["b_msg_id"]), text, markup, high=urgent):
            self.last_render[ord_] = signature
        self.last_edit_at[ord_] = time.monotonic()

    async def _show_result(self, ord_: int, skipped: bool) -> None:
        lot, t, auction = await self._view(ord_)
        if lot is None or not t.get("host_chat_id") or int(lot.get("shown") or 0) >= 2:
            return
        chat_id = int(t["host_chat_id"])
        total = int((auction or {}).get("total_lots") or 0)
        if lot["status"] == "sold":
            team = await tr.team(self.tid, int(lot["sold_team_no"])) if lot.get("sold_team_no") is not None else None
            text = tx.sold_text(t, lot, team, total)
        else:
            text = tx.unsold_text(t, lot, total, skipped=skipped)
        markup = tx.markup([tx.room_button(self.tid)])
        done = False
        if lot.get("b_msg_id"):
            done = await self._edit(chat_id, int(lot["b_msg_id"]), text, markup, high=True)
        if not done:
            await self._send(chat_id, text, markup)
        if lot.get("a_msg_id"):
            await self._unpin(chat_id, int(lot["a_msg_id"]))
        await ar.set_shown(self.tid, ord_, 2)
        self.pending_changed.discard(ord_)
        self.last_render.pop(ord_, None)
        self.last_edit_at.pop(ord_, None)

    async def _show_text(self, text: str, markup: dict | None) -> None:
        t = await self._tournament()
        if t.get("host_chat_id"):
            await self._send(int(t["host_chat_id"]), text, markup)


def presenter(tid: int) -> Presenter:
    pres = _PRESENTERS.get(int(tid))
    if pres is None:
        pres = _PRESENTERS[int(tid)] = Presenter(int(tid))
    return pres


# ---------------------------------------------------------------------------
# pool
# ---------------------------------------------------------------------------
_VARIANT_KEYS = ("player_kind", "edition", "special_edition_id", "showcase_card_id", "showcase_set_id", "showcase_name", "player_id")


async def load_pool(pool_key: str) -> list[dict[str, Any]]:
    """Every player an auction for this kind of tournament may offer - each real player ONCE, as his highest-level card.

    Custom pools also contain the special-edition and showcase cards; when a player exists as several cards
    (base, special, showcase) only the strongest one is offered. The same goes for a player listed under several teams."""
    from database.query import fetch
    key = str(pool_key or "").upper()
    if key == "CUSTOM":
        rows = await fetch(
            """
            SELECT name, country, role, bat_level, bowl_level, batting_hand, bowling_hand, 'global'::TEXT AS player_kind,
                   player_id::BIGINT AS player_id, NULL::TEXT AS edition, NULL::BIGINT AS special_edition_id,
                   NULL::BIGINT AS showcase_card_id, NULL::BIGINT AS showcase_set_id, NULL::TEXT AS showcase_name FROM players
            UNION ALL
            SELECT name, country, role, bat_level, bowl_level, batting_hand, bowling_hand, 'special'::TEXT, special_player_id::BIGINT,
                   edition, special_player_id::BIGINT, NULL::BIGINT, NULL::BIGINT, NULL::TEXT FROM special_edition_players
            UNION ALL
            SELECT sc.name, sc.country, sc.role, sc.bat_level, sc.bowl_level, sc.batting_hand, sc.bowling_hand, 'showcase'::TEXT,
                   sc.showcase_card_id::BIGINT, NULL::TEXT, NULL::BIGINT, sc.showcase_card_id::BIGINT, sc.showcase_set_id::BIGINT, ss.set_name
            FROM showcase_cards sc JOIN showcase_sets ss ON ss.showcase_set_id = sc.showcase_set_id;
            """) if await _has_variant_tables() else await fetch(
            "SELECT name, country, role, bat_level, bowl_level, batting_hand, bowling_hand, 'global'::TEXT AS player_kind, player_id::BIGINT AS player_id FROM players;")
    else:
        # real franchise / nation squads only (a team code with a trailing number is another tournament's auction squad)
        rows = await fetch(
            "SELECT name, country, role, bat_level, bowl_level, batting_hand, bowling_hand, team_code FROM playint_players "
            "WHERE engine_key=$1 AND team_code !~ '[0-9]$';", key)
    return dedupe_best_cards(dict(r) for r in rows)


def dedupe_best_cards(rows) -> list[dict[str, Any]]:
    """One entry per real player (by normalised name): the card with the highest level wins."""
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        k = tc.player_key(row.get("name"))
        if not k:
            continue
        cur = best.get(k)
        if cur is None or tc.player_ovr(row) > tc.player_ovr(cur):
            best[k] = row
    return list(best.values())


async def _has_variant_tables() -> bool:
    from database.query import fetchval
    try:
        return bool(await fetchval("SELECT to_regclass('special_edition_players') IS NOT NULL AND to_regclass('showcase_cards') IS NOT NULL AND to_regclass('showcase_sets') IS NOT NULL"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# starting
# ---------------------------------------------------------------------------
async def start_auction(tid: int, by_user: int) -> dict[str, Any]:
    """Host presses "Start auction". Returns {"ok": bool, "error": str, ...}."""
    t = await tr.get(tid)
    if t is None:
        return {"ok": False, "error": "Tournament not found."}
    if not can_control(t, by_user):
        return {"ok": False, "error": "Only the tournament host can start the auction."}
    if t.get("mode") != "auction":
        return {"ok": False, "error": "This tournament has no auction."}
    if t.get("status") != "registration":
        return {"ok": False, "error": "The auction can only be started from the registration lobby."}
    teams = await tr.teams(tid)
    prof = tc.profile(t.get("engine"))
    if len(teams) < int(prof.get("min_teams") or 2):
        return {"ok": False, "error": f"At least {prof.get('min_teams') or 2} teams are needed."}
    missing = [tx.team_short(x) for x in teams if x.get("owner_id") is None]
    if missing:
        return {"ok": False, "error": "These teams have no owner yet: " + ", ".join(missing) + ". Assign an owner or remove the team."}
    if any(int(x.get("purse_left") or 0) <= 0 for x in teams):
        return {"ok": False, "error": "Set the purse for every team first."}
    pool = await load_pool(prof["pool_key"])
    if not tc.pool_is_enough(len(teams), int(t.get("squad_min") or 0), len(pool)):
        need = int(len(teams) * int(t.get("squad_min") or 0) * 1.15) + 1
        return {"ok": False, "error": f"The player pool has only {len(pool)} players; {need} are needed for {len(teams)} teams."}
    claimed = await tr.cas_status(tid, ["registration"], "auction")
    if claimed is None:
        return {"ok": False, "error": "The auction is already starting."}
    try:
        target = tc.pool_target(len(teams), int(t.get("squad_max") or 20), len(pool))
        chosen = tc.select_pool(pool, target)
        sets = tc.build_auction_sets(chosen, prof)
        lots = tc.flatten_sets(sets)
        if not lots:
            raise RuntimeError("empty auction")
        await ar.init_auction(tid, lots)
        await ar.start_auction(tid, first_delay_s=INTRO_SECONDS)
    except Exception as exc:
        traceback.print_exc()
        await tr.cas_status(tid, ["auction"], "registration")
        return {"ok": False, "error": f"Could not prepare the auction: {exc}"}
    _ACTIVE.add(int(tid))
    pres = presenter(tid)
    pres.recovered = True
    pres.say(tx.intro(claimed, teams, len(lots), len(sets), INTRO_SECONDS), tx.markup([tx.room_button(tid)], *host_markup_rows(tid)))
    wake()
    return {"ok": True, "lots": len(lots), "sets": len(sets), "teams": len(teams), "players": len(chosen)}


# ---------------------------------------------------------------------------
# bidding + host controls (also used by the Mini App API)
# ---------------------------------------------------------------------------
async def bid(tid: int, ord_: int, user_id: int, expected_lakh: int | None = None) -> dict[str, Any]:
    team = await tr.team_of_user(tid, user_id)
    if team is None:
        return {"ok": False, "reason": "not_in"}
    try:
        result = await ar.place_bid(tid, ord_, int(team["team_no"]), int(user_id), expected_lakh=expected_lakh)
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == "40001":
            return {"ok": False, "reason": "busy"}
        raise
    if result.get("ok"):
        pres = _PRESENTERS.get(int(tid))
        if pres is not None:
            pres.changed(int(ord_))
        wake()
    return result


async def control(tid: int, user_id: int, action: str) -> dict[str, Any]:
    """action: pause | resume | end. Allowed for the tournament creator and the bot owner/admin."""
    t = await tr.get(tid)
    if t is None:
        return {"ok": False, "message": "Tournament not found."}
    if not can_control(t, user_id):
        return {"ok": False, "message": "Only the tournament host or the bot owner can do this."}
    if action == "pause":
        row = await ar.pause(tid)
        if row is None:
            return {"ok": False, "message": "The auction is not running."}
        return {"ok": True, "message": "⏸ Auction paused.", "status": "paused"}
    if action == "resume":
        row = await ar.resume(tid)
        if row is None:
            return {"ok": False, "message": "The auction is not paused."}
        wake()
        return {"ok": True, "message": "▶️ Auction resumed.", "status": "running"}
    if action == "end":
        row = await ar.request_end(tid)
        if row is None:
            return {"ok": False, "message": "The auction is not running."}
        _ACTIVE.add(int(tid))
        wake()
        return {"ok": True, "message": "🛑 Ending the auction…", "status": "ending"}
    return {"ok": False, "message": "Unknown action."}


# ---------------------------------------------------------------------------
# the auctioneer
# ---------------------------------------------------------------------------
async def _accelerated_worthwhile(tid: int, t: dict) -> bool:
    unsold = await ar.unsold_players(tid)
    if not unsold:
        return False
    cheapest = min(int(u.get("base_lakh") or 0) for u in unsold)
    squad_max = int(t.get("squad_max") or 99)
    return any(int(team.get("squad_count") or 0) < squad_max and int(team.get("purse_left") or 0) >= cheapest
               for team in await tr.teams(tid))


async def complete_squads(tid: int) -> None:
    """Top every squad up to its minimum size (and a legal XI) with players nobody bought, cheapest base price."""
    import random
    t = await tr.get(tid)
    if t is None:
        return
    rules = {"squad_min": t["squad_min"], "squad_max": t["squad_max"],
             "overseas_squad_max": t["overseas_squad_max"], "xi_overseas_max": t["xi_overseas_max"]}
    teams = await tr.teams(tid)
    random.shuffle(teams)
    squads = await ar.all_squads(tid)
    unsold = await ar.unsold_players(tid)
    taken: set[str] = set()
    for team in teams:
        have = squads.get(int(team["team_no"]), [])
        candidates = [{**u, "key": u["pkey"]} for u in unsold if u["pkey"] not in taken]
        for player, price in tc.plan_autofill(have, candidates, rules, int(team.get("purse_left") or 0)):
            added = await ar.add_squad_player(tid, int(team["team_no"]), {
                "pkey": player["pkey"], "name": player["name"], "role": player["role"], "country": player.get("country"),
                "bat_level": player.get("bat_level"), "bowl_level": player.get("bowl_level"),
                "batting_hand": player.get("batting_hand"), "bowling_hand": player.get("bowling_hand"),
                "overseas": player.get("overseas"), "card": player.get("card"), "ord": player.get("ord"),
            }, price, source="autofill")
            if added:
                taken.add(player["pkey"])


async def pick_xis(tid: int) -> list[str]:
    """Best legal XI for every squad (stored on the squad rows). Returns the short names of teams that cannot field one."""
    t = await tr.get(tid)
    broken: list[str] = []
    if t is None:
        return broken
    squads = await ar.all_squads(tid)
    for team in await tr.teams(tid):
        squad = squads.get(int(team["team_no"]), [])
        xi = tc.pick_best_xi(squad, int(t.get("xi_overseas_max") or 0))
        if xi is None:
            broken.append(tx.team_short(team))
            await ar.set_xi(tid, int(team["team_no"]), [])
        else:
            await ar.set_xi(tid, int(team["team_no"]), [p["pkey"] for p in xi])
    return broken


async def _finish(tid: int, ended_early: bool = False) -> None:
    if int(tid) in _FINISHING:
        return
    _FINISHING.add(int(tid))
    try:
        pres = presenter(tid)
        if ended_early:
            live = await ar.live_lot(tid)
            if live is not None:
                res = await ar.settle(tid, int(live["ord"]), force=True)
                if res:
                    pres.result(int(live["ord"]), skipped=not res.get("sold"))
            await ar.close_all_pending(tid)
        await complete_squads(tid)
        broken = await pick_xis(tid)
        t = await tr.get(tid)
        if t is not None and t.get("engine") in ("PLAYIPL", "PLAYWPL", "PLAYINT"):
            await ar.materialize_engine_squads(tid, tc.profile(t["engine"])["pool_key"])
        await tr.cas_status(tid, ["auction"], "ready")
        await ar.finish_auction(tid)
        summary = await ar.sale_summary(tid)
        teams = await tr.teams(tid)
        text = tx.finish_text(t or {}, teams, summary["buys"], summary["sold"], summary["unsold"], ended_early=ended_early)
        if broken:
            text += "\n⚠️ <b>No legal XI possible:</b> " + ", ".join(tx.esc(b) for b in broken)
        pres.say(text, tx.markup([tx.room_button(tid)], [tx.hub_button(tid)]))
        try:
            from services import tournament_squads
            asyncio.create_task(tournament_squads.send_all(tid))
        except Exception:
            traceback.print_exc()
    finally:
        _FINISHING.discard(int(tid))
        _ACTIVE.discard(int(tid))


async def _advance(tid: int, st: dict) -> None:
    """Between lots and the pause is over: open the next lot, or run the accelerated round, or finish."""
    pres = presenter(tid)
    nxt = await ar.next_pending(tid)
    if nxt is not None:
        lot = await ar.begin_lot(tid, int(nxt["ord"]), bid_seconds=tc.BID_SECONDS + OPENING_GRACE)
        if lot is not None:
            pres.new_lot(int(lot["ord"]))
        return
    t = await tr.get(tid) or {}
    if not st.get("accel_done") and await _accelerated_worthwhile(tid, t):
        lots = tc.accelerated_lots(await ar.unsold_players(tid))
        if lots:
            n = await ar.add_round(tid, 2, lots)
            pres.say(tx.accelerated_text(n), tx.markup([tx.room_button(tid)]))
            return
    await _finish(tid)


async def _recover(tid: int, st: dict) -> None:
    """First look after a (re)start: show whatever the group has not seen yet."""
    pres = presenter(tid)
    pres.recovered = True
    pres.paused = st["a_status"] == "paused"
    for lot in await ar.unfinished_lots(tid):
        pres.result(int(lot["ord"]))
    if st.get("cur_ord") is not None:
        if int(st.get("shown") or 0) == 0:
            pres.new_lot(int(st["cur_ord"]))
        else:
            pres.changed(int(st["cur_ord"]), urgent=True)


async def _tick(tid: int) -> None:
    st = await ar.tick_state(tid)
    if st is None or st["a_status"] == "done":
        _ACTIVE.discard(int(tid))
        return
    status = st["a_status"]
    if status == "idle":
        return
    pres = presenter(tid)
    if status == "ending":
        await _finish(tid, ended_early=True)
        return
    if not pres.recovered:
        await _recover(tid, st)
    # pause / resume notices (whoever triggered them - the bot, the Mini App or the admin)
    if pres.seen_status is not None and pres.seen_status != status:
        if status == "paused":
            pres.paused = True
            pres.say(tx.paused_text("the host"), tx.markup([tx.room_button(tid)]))
            if st.get("cur_ord") is not None:
                pres.changed(int(st["cur_ord"]), urgent=True)
        elif status == "running":
            pres.paused = False
            pres.say(tx.resumed_text("the host"), tx.markup([tx.room_button(tid)]))
            if st.get("cur_ord") is not None:
                pres.changed(int(st["cur_ord"]), urgent=True)
    pres.seen_status = status
    if status == "paused":
        return
    now = st["db_now"]
    ord_ = st.get("cur_ord")
    if ord_ is None:
        if st.get("next_lot_at") is not None and st["next_lot_at"] <= now:
            await _advance(tid, st)
        return
    if st.get("l_status") != "live":
        return
    if pres.seen_version != st["version"]:
        pres.seen_version = st["version"]
        pres.changed(int(ord_))
    left = (st["deadline_at"] - now).total_seconds()
    if left <= 0:
        result = await ar.settle(tid, int(ord_))
        if result:
            pres.result(int(ord_))
        return
    if int(st.get("bid_count") or 0) > 0:
        if left <= tc.GOING_TWICE_AT and not st.get("said_twice"):
            if await ar.mark_said(tid, int(ord_), "twice"):
                pres.changed(int(ord_), urgent=True)
        elif left <= tc.GOING_ONCE_AT and not st.get("said_once"):
            if await ar.mark_said(tid, int(ord_), "once"):
                pres.changed(int(ord_), urgent=True)


async def _worker() -> None:
    last_scan = 0.0
    while True:
        try:
            if time.monotonic() - last_scan > 20:
                last_scan = time.monotonic()
                for row in await ar.running_auctions():
                    _ACTIVE.add(int(row["tid"]))
            for tid in list(_ACTIVE):
                try:
                    await _tick(tid)
                except Exception as exc:
                    print(f"[tournament/auction] tick failed for #{tid}: {exc!r}")
                    traceback.print_exc()
        except Exception as exc:
            print(f"[tournament/auction] worker error: {exc!r}")
        try:
            await asyncio.wait_for(_WAKE.wait(), timeout=POLL_BUSY if _ACTIVE else POLL_IDLE)
        except asyncio.TimeoutError:
            pass
        _WAKE.clear()


def start_worker() -> asyncio.Task:
    """Called once from main() after the bot client is up."""
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


# ---------------------------------------------------------------------------
# /auction status card
# ---------------------------------------------------------------------------
async def status_card(tid: int, viewer_id: int | None = None) -> tuple[str, dict]:
    t = await tr.get(tid)
    if t is None:
        return "Tournament not found.", tx.markup()
    a = await ar.get_auction(tid)
    teams = await tr.teams(tid)
    lines = [f"🏛 <b>{tx.esc(t.get('name'))}</b> — auction"]
    if a is None or a["status"] == "idle":
        lines.append("<i>The auction has not started yet.</i>")
    else:
        cnt = await ar.counts(tid)
        label = {"running": "🟢 Live", "paused": "⏸ Paused", "ending": "🛑 Ending", "done": "🏁 Finished"}.get(a["status"], a["status"])
        lines.append(f"{label}  ·  ✅ {cnt['sold']} sold  ·  ❌ {cnt['unsold']} unsold  ·  ⏳ {cnt['pending'] + cnt['live']} to go")
        if a.get("cur_ord") is not None:
            lot = await ar.lot_view(tid, int(a["cur_ord"]))
            if lot:
                cur = (f"💰 {tc.fmt_money(lot['cur_bid_lakh'])} — {tx.team_short({'short': lot.get('leader_short'), 'base_code': lot.get('leader_base')})}"
                       if int(lot.get("bid_count") or 0) else f"base {tc.fmt_money(lot['base_lakh'])}")
                lines.append(f"<blockquote>{tx.player_heading(lot)}\n{cur}</blockquote>")
    rows = []
    for team in sorted(teams, key=lambda x: int(x["team_no"])):
        rows.append(f"<b>{tx.esc(tx.team_short(team))}</b> · {int(team.get('squad_count') or 0)}/{int(t.get('squad_max') or 0)} · {tc.fmt_money(team.get('purse_left'))}")
    if rows:
        lines.append("<blockquote>" + "\n".join(rows) + "</blockquote>")
    keyboard = [[tx.room_button(tid)]]
    if a and a["status"] in ("running", "paused") and can_control(t, viewer_id):
        keyboard.append(host_markup_rows(tid)[0])
    return "\n".join(lines), tx.markup(*keyboard)
