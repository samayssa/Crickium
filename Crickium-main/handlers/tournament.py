"""Auction Tournaments: /createtour wizard, lobby, host panel, table, records, /tplay, /tsched, /auction.

All state lives in the database (see database/tournament_repo.py); this module only turns commands and buttons
into calls to services.tournament_runtime / services.tournament_auction and renders the answers.
"""
from __future__ import annotations

print("tournament.py loaded")

import re
import traceback
from typing import Any

from app import app
from config import ADMIN_USER_ID
from database import tournament_repo as tr
from handlers.registry import register, register_callback
from services import tournament_auction as auc
from services import tournament_core as tc
from services import tournament_runtime as rt
from services import tournament_text as tx
from services import tournament_views as tv
from services import tournament_virtual as tvirt

LIVE = ("registration", "auction", "ready", "live")
_TEAM_LISTS = {
    "PLAYIPL": ("database.playipl_teams_repo", "TEAM_ORDER"),
    "PLAYWPL": ("database.playwpl_teams_repo", "TEAM_ORDER"),
    "PLAYINT": ("database.playint_teams_repo", None),
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _is_admin(uid: Any) -> bool:
    return int(uid or 0) == int(ADMIN_USER_ID)


def _user(obj: dict) -> dict:
    u = (obj or {}).get("from") or {}
    return {"id": int(u.get("id") or 0), "username": u.get("username"), "first_name": u.get("first_name") or u.get("username") or "Player"}


async def _answer(cq: dict, text: str | None = None, alert: bool = False) -> None:
    try:
        await app.answer_callback_query(cq.get("id"), text=(text or "")[:195] or None, show_alert=alert)
    except Exception:
        pass


async def _send(chat_id: int, text: str, markup: dict | None = None) -> int | None:
    msg = await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
    return int((msg or {}).get("message_id") or 0) or None


async def _edit(cq: dict, text: str, markup: dict | None = None) -> None:
    m = cq.get("message") or {}
    try:
        await app.edit_message_text(int((m.get("chat") or {}).get("id")), int(m.get("message_id")), text, parse_mode="HTML", reply_markup=markup)
    except Exception as exc:
        if "not modified" not in str(exc).lower():
            print(f"[tournament] edit failed: {exc!r}")


def _chat(obj: dict) -> tuple[int, str]:
    c = (obj.get("message") or obj).get("chat") or {}
    return int(c.get("id") or 0), str(c.get("type") or "")


def _is_group(kind: str) -> bool:
    return kind in ("group", "supergroup")


def _parts(cq: dict) -> list[str]:
    return str(cq.get("data") or "").split(":")


async def _find(chat_id: int, user_id: int, arg: str = "", statuses=LIVE) -> dict | None:
    arg = (arg or "").strip().lstrip("#")
    if arg.isdigit():
        return await tr.get(int(arg))
    rows = await tr.for_chat(chat_id, statuses)
    if rows:
        live = [r for r in rows if r["status"] == "live"]
        return (live or rows)[0]
    mine = [r for r in await tr.list_for_user(user_id, 20) if r["status"] in statuses]
    return mine[0] if mine else None


def _args(message: dict) -> str:
    parts = (message.get("text") or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def _need_host(cq: dict, t: dict | None) -> bool:
    if t is None:
        await _answer(cq, "Tournament not found.", True)
        return False
    if not rt.can_manage(t, _user(cq)["id"]):
        await _answer(cq, "Only the tournament host can do this.", True)
        return False
    return True


# ---------------------------------------------------------------------------
# /createtour wizard:  engine -> name -> mode -> teams -> window -> lobby
# ---------------------------------------------------------------------------
def _wiz_head(t: dict) -> str:
    prof = tc.profile(t["engine"]) if t.get("setup_step") != "engine" else None
    bits = [f"🏆 <b>{tx.esc(t['name'])}</b>"] if t.get("setup_step") not in ("engine", "name") else []
    if prof:
        bits.append(f"{prof['icon']} {tx.esc(prof['title'])}")
    return "\n".join(bits)


def _wiz_card(t: dict) -> tuple[str, dict]:
    tid, step = int(t["tid"]), t["setup_step"]
    cancel = tx.button("✖ Cancel", f"trc:{tid}:x", style="danger")
    off = "🎖 <b>Official tournament</b>\n" if t.get("kind") == "official" else ""
    if step == "engine":
        text = (f"{off}🏆 <b>Create a tournament</b> — step 1/5\n<blockquote>What are you playing?\n"
                "🏏 IPL — 10 franchises\n🌍 T20 World Cup — nations\n👑 WPL — women's franchises\n🎮 Custom — your own squads, regular /play game</blockquote>")
        kb = tx.markup([tx.button("🏏 IPL", f"trc:{tid}:e:ipl", style="primary"), tx.button("🌍 T20 World Cup", f"trc:{tid}:e:t20wc", style="primary")],
                       [tx.button("👑 WPL", f"trc:{tid}:e:wpl", style="primary"), tx.button("🎮 Custom", f"trc:{tid}:e:custom", style="primary")], [cancel])
    elif step == "name":
        prof = tc.profile(t["engine"])
        text = (f"{off}{_wiz_head(t)}\n\n<b>Step 2/5 — name</b>\n<blockquote>Send the tournament name as a message now.\n"
                f"Example: <code>Samay Premier League</code>\nOr use the default: <b>{tx.esc(prof['short'])} Cup</b></blockquote>")
        kb = tx.markup([tx.button(f"✅ Use “{prof['short']} Cup”", f"trc:{tid}:n:d", style="success")], [cancel])
    elif step == "mode":
        text = (f"{off}{_wiz_head(t)}\n\n<b>Step 3/5 — squads</b>\n<blockquote>🏛 <b>With auction</b> — owners bid live for players (30 s timer, random sets).\n"
                "⚡ <b>Without auction</b> — uses the saved squads (for Custom: each owner's own squad).</blockquote>")
        kb = tx.markup([tx.button("🏛 With auction", f"trc:{tid}:m:a", style="primary"), tx.button("⚡ Without auction", f"trc:{tid}:m:d", style="primary")], [cancel])
    elif step == "teams":
        prof = tc.profile(t["engine"])
        nums = list(range(int(prof["min_teams"]), int(prof["max_teams"]) + 1))
        text = f"{off}{_wiz_head(t)}\n\n<b>Step 4/5 — how many teams?</b>\n<blockquote>Example: 4 teams → 12 league matches + final.</blockquote>"
        rows = [[tx.button(str(n), f"trc:{tid}:t:{n}") for n in nums[i:i + 5]] for i in range(0, len(nums), 5)]
        kb = tx.markup(*rows, [cancel])
    else:   # window
        text = (f"{off}{_wiz_head(t)}\n\n<b>Step 5/5 — daily match time</b>\n<blockquote>Players may play any time inside this window "
                "(or earlier with /tplay). One hour before the end the bot tags everyone with a pending match.</blockquote>")
        rows = [[tx.button(tc.fmt_window(a, b), f"trc:{tid}:w:{i}", style="primary")] for i, (a, b) in enumerate(tc.WINDOW_PRESETS)]
        kb = tx.markup(*rows, [cancel])
    return text, kb


async def _draft_show(t: dict, cq: dict | None = None) -> None:
    text, kb = _wiz_card(t)
    if cq is not None:
        await _edit(cq, text, kb)
        return
    mid = t.get("setup_msg_id")
    try:
        if mid:
            await app.edit_message_text(int(t["host_chat_id"]), int(mid), text, parse_mode="HTML", reply_markup=kb)
            return
    except Exception:
        pass
    new = await _send(int(t["host_chat_id"]), text, kb)
    await tr.update_fields(int(t["tid"]), setup_msg_id=new)


async def _start_wizard(message: dict, kind: str) -> None:
    chat_id, ctype = _chat(message)
    u = _user(message)
    if not _is_group(ctype):
        await _send(chat_id, "🏆 Tournaments run inside a <b>group</b>. Add me to your group and send /createtour there.")
        return
    if kind == "official" and not _is_admin(u["id"]):
        await _send(chat_id, "🚫 Only the bot owner can create official tournaments.")
        return
    old = await tr.draft_of(u["id"])
    if old and old.get("kind") != kind:
        await tr.delete_draft(int(old["tid"]))
    t = await tr.create_draft(u["id"], u["first_name"], chat_id, (message.get("chat") or {}).get("title"), kind)
    if not t.get("_created"):
        await tr.update_fields(int(t["tid"]), host_chat_id=chat_id, host_chat_title=(message.get("chat") or {}).get("title"), setup_msg_id=None)
        t = await tr.get(int(t["tid"]))
    await _draft_show(t)


@register("createtour")
async def createtour_command(message):
    await _start_wizard(message, "user")


@register("officialtour")
async def officialtour_command(message):
    await _start_wizard(message, "official")


async def _finish_wizard(t: dict, window: tuple[int, int], cq: dict) -> None:
    tid = int(t["tid"])
    prof = tc.profile(t["engine"])
    row = await tr.cas_status(tid, ["setup"], "registration", window_start_min=window[0], window_end_min=window[1], setup_step="done",
                              purse_lakh=int(prof["purse_lakh"]), squad_min=int(prof["squad_min"]) if t["mode"] == "auction" else 11,
                              squad_max=int(prof["squad_max"]), overseas_squad_max=int(prof["overseas_squad_max"]),
                              xi_overseas_max=int(prof["xi_overseas_max"]))
    if row is None:
        await _answer(cq, "Already created.")
        return
    await _post_lobby(row, replace=cq)


def _lobby_markup(t: dict) -> dict:
    tid = int(t["tid"])
    return tx.markup([tx.button("🙋 Join", f"trj:{tid}:j", style="success"), tx.button("🚪 Leave", f"trj:{tid}:l")],
                     [tx.button("⚙️ Host panel", f"trm:{tid}:menu", style="primary"), tx.hub_button(tid)])


async def _post_lobby(t: dict, replace: dict | None = None) -> None:
    teams = await tr.teams(int(t["tid"]))
    text, kb = tv.lobby_text(t, teams), _lobby_markup(t)
    if replace is not None:
        await _edit(replace, text, kb)
        mid = int((replace.get("message") or {}).get("message_id") or 0)
    else:
        mid = await _send(int(t["host_chat_id"]), text, kb)
    if mid:
        await tr.update_fields(int(t["tid"]), lobby_msg_id=mid)


async def _refresh_lobby(tid: int) -> None:
    t = await tr.get(tid)
    if not t or t["status"] != "registration" or not t.get("lobby_msg_id"):
        return
    try:
        await app.edit_message_text(int(t["host_chat_id"]), int(t["lobby_msg_id"]), tv.lobby_text(t, await tr.teams(tid)), parse_mode="HTML", reply_markup=_lobby_markup(t))
    except Exception:
        pass


@register_callback("trc")
async def on_wizard(cq):
    p = _parts(cq)
    tid, act = int(p[1]), p[2]
    arg = p[3] if len(p) > 3 else ""
    t = await tr.get(tid)
    if t is None or t["status"] != "setup":
        await _answer(cq, "This setup is finished or expired. Send /createtour again.", True)
        return
    if _user(cq)["id"] != int(t["creator_id"]):
        await _answer(cq, "Only the creator can use these buttons.", True)
        return
    await _answer(cq)
    if act == "x":
        await tr.delete_draft(tid)
        await _edit(cq, "✖ Tournament setup cancelled.")
        return
    if act == "e" and arg in tc.PRESET_ENGINE:
        engine = tc.PRESET_ENGINE[arg]
        prof = tc.profile(engine)
        t = await tr.update_fields(tid, engine=engine, preset=arg, setup_step="name", name=f"{prof['short']} Cup", max_teams=int(prof["max_teams"]))
    elif act == "n" and t["setup_step"] == "name":
        t = await tr.update_fields(tid, setup_step="mode")
    elif act == "m" and t["setup_step"] == "mode":
        t = await tr.update_fields(tid, mode="auction" if arg == "a" else "direct", setup_step="teams")
    elif act == "t" and t["setup_step"] == "teams" and arg.isdigit():
        prof = tc.profile(t["engine"])
        n = max(int(prof["min_teams"]), min(int(prof["max_teams"]), int(arg)))
        t = await tr.update_fields(tid, max_teams=n, setup_step="window")
    elif act == "w" and t["setup_step"] == "window" and arg.isdigit() and int(arg) < len(tc.WINDOW_PRESETS):
        await _finish_wizard(t, tc.WINDOW_PRESETS[int(arg)], cq)
        return
    await _draft_show(t, cq)


async def handle_non_command_message(message) -> bool:
    """The creator types the tournament name during step 2 of the wizard."""
    text = str(message.get("text") or "").strip()
    if not text or text.startswith("/"):
        return False
    u = _user(message)
    if not u["id"]:
        return False
    t = await tr.draft_of(u["id"])
    if t is None or t.get("setup_step") != "name" or int(t.get("host_chat_id") or 0) != _chat(message)[0]:
        return False
    name = tc.clean_name(text)
    if not name:
        await _send(_chat(message)[0], "⚠️ Please send a name (max 36 characters, letters/numbers).")
        return True
    t = await tr.update_fields(int(t["tid"]), name=name, setup_step="mode")
    await _draft_show(t)
    return True


# ---------------------------------------------------------------------------
# lobby: join / leave
# ---------------------------------------------------------------------------
def _franchise_codes(engine: str) -> list[str]:
    import importlib
    mod_name, order = _TEAM_LISTS[engine]
    mod = importlib.import_module(mod_name)
    return list(getattr(mod, order)) if order else list(mod.TEAMS_PAGE_1) + list(mod.TEAMS_PAGE_2)


def _franchise_name(engine: str, base: str) -> str:
    import importlib
    return importlib.import_module(_TEAM_LISTS[engine][0]).TEAM_MAP[base]


async def _join_team(t: dict, user: dict, base: str, name: str, short: str | None) -> str | None:
    """Create a team owned by ``user``. Returns an error text or None."""
    try:
        await tr.add_team(int(t["tid"]), base, tvirt.make_code(base, int(t["tid"])) if t["engine"] != "PLAY" else base, name, short, user, int(t["purse_lakh"] or 0))
    except tr.TourError as exc:
        return str(exc)
    return None


@register_callback("trj")
async def on_lobby(cq):
    p = _parts(cq)
    tid, act = int(p[1]), p[2]
    t = await tr.get(tid)
    u = _user(cq)
    if t is None or t["status"] != "registration":
        await _answer(cq, "Registration is closed.", True)
        return
    if act == "j":
        if await tr.team_of_user(tid, u["id"]):
            await _answer(cq, "You already own a team here.", True)
            return
        teams = await tr.teams(tid)
        if len(teams) >= int(t["max_teams"]):
            await _answer(cq, "All slots are taken.", True)
            return
        if t["engine"] == "PLAY":
            base = re.sub(r"[^A-Z]", "", u["first_name"].upper())[:4] or "TEAM"
            taken = {x["base_code"] for x in teams}
            while base in taken or len(base) < 2:
                base = (base + "X")[:6]
            err = await _join_team(t, u, base, f"{u['first_name']} XI", base)
            await _answer(cq, err or "✅ You joined! Rename your team any time with /teamname <name>", bool(err))
            await _refresh_lobby(tid)
            return
        taken = {x["base_code"] for x in teams}
        free = [c for c in _franchise_codes(t["engine"]) if c not in taken]
        rows = [[tx.button(c, f"trj:{tid}:p:{c}", style="primary") for c in free[i:i + 4]] for i in range(0, len(free), 4)]
        await _answer(cq)
        await _send(int(t["host_chat_id"]), f"{tv.e(u['first_name'])}, pick your team 👇", tx.markup(*rows))
        return
    if act == "p":
        base = p[3]
        if base not in _franchise_codes(t["engine"]):
            await _answer(cq, "Unknown team.", True)
            return
        err = await _join_team(t, u, base, _franchise_name(t["engine"], base), base)
        await _answer(cq, err or f"✅ You are {base}!", bool(err))
        if not err:
            try:
                m = cq.get("message") or {}
                await app.delete_message(int(m["chat"]["id"]), int(m["message_id"]))
            except Exception:
                pass
            await _refresh_lobby(tid)
        return
    if act == "l":
        team = await tr.team_of_user(tid, u["id"])
        if not team:
            await _answer(cq, "You have no team here.", True)
            return
        await tr.remove_owner(tid, int(team["team_no"]))
        await _answer(cq, "You left the tournament.")
        await _refresh_lobby(tid)


@register("teamname")
async def teamname_command(message):
    chat_id, _ = _chat(message)
    u = _user(message)
    name = tc.clean_name(_args(message))
    t = await _find(chat_id, u["id"])
    team = await tr.team_of_user(int(t["tid"]), u["id"]) if t else None
    if not t or not team or not name:
        await _send(chat_id, "Usage: <code>/teamname Mighty Kings</code> (you need a team in a tournament).")
        return
    if t["engine"] != "PLAY":
        await _send(chat_id, "Franchise teams keep their real name. Only Custom tournaments allow renaming.")
        return
    await tr.rename_team(int(t["tid"]), int(team["team_no"]), name)
    await _send(chat_id, f"✅ Team renamed to <b>{tx.esc(name)}</b>.")
    await _refresh_lobby(int(t["tid"]))


# ---------------------------------------------------------------------------
# host panel
# ---------------------------------------------------------------------------
def _panel(t: dict, teams: list[dict]) -> tuple[str, dict]:
    tid = int(t["tid"])
    auction = t["mode"] == "auction"
    lines = [f"⚙️ <b>Host panel</b> · {tx.esc(t['name'])}", f"<blockquote>👥 {len(teams)}/{t['max_teams']} teams"
             + (f" · 💰 purse {tx.esc(tc.fmt_money(t['purse_lakh']))}\n📈 steps: " + ", ".join(f"{tx.esc(a)} {tx.esc(b)}" for a, b in tc.ladder_rows(t.get('ladder'))[:4]) if auction else "")
             + f"\n🕔 {tx.esc(tc.fmt_window(int(t['window_start_min']), int(t['window_end_min'])))}</blockquote>"]
    rows: list[list[dict]] = []
    if t["status"] == "registration":
        if auction:
            prof = tc.profile(t["engine"])
            rows.append([tx.button(tc.fmt_money(v), f"trm:{tid}:pu:{v}") for v in prof["purse_presets"][:4]])
            rows.append([tx.button("📈 IPL steps", f"trm:{tid}:ld:ipl"), tx.button("⚡ Fast steps", f"trm:{tid}:ld:fast")])
            rows.append([tx.button("🏛 Start auction", f"trm:{tid}:sa", style="success")])
            lines.append("<i>Custom purse: /setpurse 120cr · custom steps (lakh): /setsteps 5 10 20 25 (below 1cr, 1–2cr, 2–5cr, above 5cr) or /setsteps 10 for flat</i>")
        else:
            rows.append([tx.button("🚀 Start tournament", f"trm:{tid}:st", style="success")])
        rows.append([tx.button("📄 Manual schedule", f"trm:{tid}:sch")])
    elif t["status"] == "ready":
        rows.append([tx.button("🚀 Start tournament", f"trm:{tid}:st", style="success"), tx.button("📄 Manual schedule", f"trm:{tid}:sch")])
    elif t["status"] == "auction":
        rows.extend(auc.host_markup_rows(tid))
    if t["status"] in LIVE:
        rows.append([tx.button("🛑 Cancel tournament", f"trm:{tid}:cx", style="danger")])
    rows.append([tx.button("↩ Close", f"trm:{tid}:close")])
    return "\n".join(lines), tx.markup(*rows)


@register_callback("trm")
async def on_manage(cq):
    p = _parts(cq)
    tid, act = int(p[1]), p[2]
    arg = p[3] if len(p) > 3 else ""
    t = await tr.get(tid)
    if not await _need_host(cq, t):
        return
    u = _user(cq)
    if act == "menu":
        await _answer(cq)
        text, kb = _panel(t, await tr.teams(tid))
        await _send(int(t["host_chat_id"]), text, kb)
        return
    if act == "close":
        await _answer(cq)
        m = cq.get("message") or {}
        try:
            await app.delete_message(int(m["chat"]["id"]), int(m["message_id"]))
        except Exception:
            pass
        return
    if act == "pu" and t["status"] == "registration" and arg.isdigit():
        await tr.update_fields(tid, purse_lakh=int(arg))
        await tr.set_all_purses(tid, int(arg))
        await _answer(cq, f"Purse set to {tc.fmt_money(int(arg))}")
    elif act == "ld" and t["status"] == "registration" and arg in tc.LADDER_PRESETS:
        await tr.update_fields(tid, ladder=tc.ladder_to_json(tc.LADDER_PRESETS[arg][1]))
        await _answer(cq, f"{tc.LADDER_PRESETS[arg][0]} steps set")
    elif act == "sa":
        teams = await tr.teams(tid)
        await _answer(cq)
        rows = "\n".join(f"• {tx.team_label(x)}" for x in teams) or "—"
        await _edit(cq, f"🏛 <b>Start the auction?</b>\n<blockquote>{rows}\n\n💰 Purse {tx.esc(tc.fmt_money(t['purse_lakh']))} each\n📈 "
                        + ", ".join(f"{tx.esc(a)} {tx.esc(b)}" for a, b in tc.ladder_rows(t.get('ladder'))[:4]) + "</blockquote>",
                    tx.markup([tx.button("✅ Yes, start", f"trm:{tid}:sy", style="success"), tx.button("↩ Back", f"trm:{tid}:menu")]))
        return
    elif act == "sy":
        res = await auc.start_auction(tid, u["id"])
        await _answer(cq, "🏛 Auction starting!" if res.get("ok") else res.get("error"), not res.get("ok"))
        return
    elif act == "st":
        res = await rt.start_tournament(tid, u["id"])
        await _answer(cq, "🚀 Started!" if res.get("ok") else res.get("error"), not res.get("ok"))
        return
    elif act == "sch":
        await _answer(cq)
        await _send(int(t["host_chat_id"]), tc.SCHEDULE_HELP + "\n\nSend a .txt file with the caption <code>/tsched</code> to apply it.")
        return
    elif act == "cx":
        await _answer(cq)
        await _edit(cq, f"🛑 <b>Cancel “{tx.esc(t['name'])}”?</b> This cannot be undone.",
                    tx.markup([tx.button("Yes, cancel", f"trm:{tid}:cy", style="danger"), tx.button("No", f"trm:{tid}:close")]))
        return
    elif act == "cy":
        await _cancel(t)
        await _answer(cq, "Cancelled.")
        await _edit(cq, f"🛑 <b>{tx.esc(t['name'])}</b> was cancelled.")
        return
    t = await tr.get(tid)
    text, kb = _panel(t, await tr.teams(tid))
    await _edit(cq, text, kb)


async def _cancel(t: dict) -> None:
    tid = int(t["tid"])
    if t["status"] == "auction":
        await auc.control(tid, int(t["creator_id"]), "end")
    await tr.cas_status(tid, list(LIVE), "cancelled")
    rt.wake()


@register("endtour")
async def endtour_command(message):
    chat_id, _ = _chat(message)
    u = _user(message)
    t = await _find(chat_id, u["id"], _args(message))
    if not t or not rt.can_manage(t, u["id"]):
        await _send(chat_id, "Only the tournament host can cancel a tournament.")
        return
    await _send(chat_id, f"🛑 <b>Cancel “{tx.esc(t['name'])}”?</b>", tx.markup([tx.button("Yes, cancel", f"trm:{int(t['tid'])}:cy", style="danger"), tx.button("No", f"trm:{int(t['tid'])}:close")]))


@register("setpurse")
async def setpurse_command(message):
    chat_id, _ = _chat(message)
    u = _user(message)
    t = await _find(chat_id, u["id"], statuses=("registration",))
    if not t or not rt.can_manage(t, u["id"]):
        await _send(chat_id, "No tournament of yours is in registration here.")
        return
    lakh = tc.parse_money(_args(message))
    if not lakh or lakh < 100:
        await _send(chat_id, "Usage: <code>/setpurse 120cr</code> (or <code>/setpurse 9000L</code>)")
        return
    await tr.update_fields(int(t["tid"]), purse_lakh=int(lakh))
    await tr.set_all_purses(int(t["tid"]), int(lakh))
    await _send(chat_id, f"💰 Purse for every team: <b>{tx.esc(tc.fmt_money(lakh))}</b>")


@register("setsteps")
async def setsteps_command(message):
    chat_id, _ = _chat(message)
    u = _user(message)
    t = await _find(chat_id, u["id"], statuses=("registration",))
    if not t or not rt.can_manage(t, u["id"]):
        await _send(chat_id, "No tournament of yours is in registration here.")
        return
    ladder = tc.parse_ladder_text(_args(message))
    if not ladder:
        await _send(chat_id, "Usage: <code>/setsteps 5 10 20 25</code> (lakh: below 1cr · 1–2cr · 2–5cr · above 5cr) or <code>/setsteps 10</code> for one flat step.\nOfficial IPL default: " + tx.esc(tc.ladder_text(tc.OFFICIAL_LADDER)))
        return
    await tr.update_fields(int(t["tid"]), ladder=tc.ladder_to_json(ladder))
    await _send(chat_id, "📈 Bid steps saved:\n" + "\n".join(f"• {tx.esc(a)} → +{tx.esc(b)}" for a, b in tc.ladder_rows(ladder)))


# ---------------------------------------------------------------------------
# assign / remove owners
# ---------------------------------------------------------------------------
@register("teamown")
async def teamown_command(message):
    chat_id, _ = _chat(message)
    u = _user(message)
    t = await _find(chat_id, u["id"])
    if not t or not rt.can_manage(t, u["id"]):
        await _send(chat_id, "Only the tournament host can assign teams.")
        return
    tid = int(t["tid"])
    words = _args(message).split()
    remove = bool(words) and words[0].lower() in ("remove", "rm", "del")
    if remove:
        words = words[1:]
    teams = await tr.teams(tid)
    team_no = tc._resolve_team(" ".join(words), teams) if words else None
    target = ((message.get("reply_to_message") or {}).get("from")) or None
    if remove:
        if team_no is None and target:
            hit = await tr.team_of_user(tid, int(target["id"]))
            team_no = int(hit["team_no"]) if hit else None
        if team_no is None:
            await _send(chat_id, "Usage: <code>/teamown remove CSK</code> (or reply to the user's message with /teamown remove)")
            return
        await tr.remove_owner(tid, team_no, delete_if_registration=False)
        await _send(chat_id, "✅ Owner removed from the team.")
        await _refresh_lobby(tid)
        return
    if team_no is None or not target:
        await _send(chat_id, "Reply to the user's message: <code>/teamown CSK</code>\nRemove: <code>/teamown remove CSK</code>")
        return
    try:
        await tr.assign_owner(tid, team_no, {"id": int(target["id"]), "username": target.get("username"), "first_name": target.get("first_name") or "Player"})
    except tr.TourError as exc:
        await _send(chat_id, f"⚠️ {tx.esc(str(exc))}")
        return
    await _send(chat_id, f"✅ {tv.e(target.get('first_name'))} now owns <b>{tx.esc(tx.team_short(await tr.team(tid, team_no)))}</b>.")
    await _refresh_lobby(tid)


# ---------------------------------------------------------------------------
# auction card + bidding + host controls
# ---------------------------------------------------------------------------
@register("auction")
async def auction_command(message):
    chat_id, _ = _chat(message)
    u = _user(message)
    t = await _find(chat_id, u["id"], _args(message), statuses=("registration", "auction", "ready", "live"))
    if not t:
        await _send(chat_id, "No tournament here yet. Create one with /createtour")
        return
    text, kb = await auc.status_card(int(t["tid"]), u["id"])
    await _send(chat_id, text, kb)


@register_callback("trb")
async def on_bid(cq):
    p = _parts(cq)
    try:
        tid, ord_, price = int(p[1]), int(p[2]), int(p[3])
    except (IndexError, ValueError):
        await _answer(cq, "Invalid bid.", True)
        return
    try:
        res = await auc.bid(tid, ord_, _user(cq)["id"], expected_lakh=price)
    except Exception:
        traceback.print_exc()
        await _answer(cq, "Something went wrong, try again.", True)
        return
    await _answer(cq, tx.bid_feedback(res), not res.get("ok"))


@register_callback("trh")
async def on_host_control(cq):
    p = _parts(cq)
    tid, act = int(p[1]), p[2]
    t = await tr.get(tid)
    if not await _need_host(cq, t):
        return
    if act == "e":
        await _answer(cq)
        await _send(int(t["host_chat_id"]), "🛑 <b>End the auction now?</b>\nUnsold lots stop and squads are completed automatically.",
                    tx.markup([tx.button("Yes, end", f"trh:{tid}:ey", style="danger"), tx.button("No", f"trh:{tid}:en")]))
        return
    if act in ("en",):
        await _answer(cq)
        m = cq.get("message") or {}
        try:
            await app.delete_message(int(m["chat"]["id"]), int(m["message_id"]))
        except Exception:
            pass
        return
    action = {"p": "pause", "r": "resume", "ey": "end"}.get(act)
    if not action:
        await _answer(cq, "Unknown action.", True)
        return
    res = await auc.control(tid, _user(cq)["id"], action)
    await _answer(cq, res.get("message"), not res.get("ok"))
    if act == "ey":
        m = cq.get("message") or {}
        try:
            await app.delete_message(int(m["chat"]["id"]), int(m["message_id"]))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# match days: ready / present / /tplay
# ---------------------------------------------------------------------------
@register("tplay")
async def tplay_command(message):
    chat_id, _ = _chat(message)
    u = _user(message)
    t = await rt.find_for_user(chat_id, u["id"])
    if t is None:
        await _send(chat_id, "You have no running tournament match. Use /tour to see the tournaments here.")
        return
    res = await rt.ready(int(t["tid"]), u["id"])
    who = tv.owner_mention({"owner_id": u["id"], "owner_username": u["username"], "owner_name": u["first_name"]})
    await _send(chat_id, f"{who} — {res['message']}")
    rt.wake()


@register_callback("trr")
async def on_ready(cq):
    tid = int(_parts(cq)[1])
    res = await rt.ready(tid, _user(cq)["id"])
    await _answer(cq, res["message"], not res.get("ok"))
    if res.get("ok") and res.get("state") == "waiting":
        t = await tr.get(tid)
        if t:
            u = _user(cq)
            await _send(int(t["host_chat_id"]), f"✅ {tv.e(u['first_name'])} is ready — waiting for the opponent. They can tap Ready or send /tplay.")
    rt.wake()


@register_callback("trp")
async def on_present(cq):
    p = _parts(cq)
    res = await rt.present(int(p[1]), int(p[2]), p[3], _user(cq)["id"])
    await _answer(cq, res["message"], not res.get("ok"))
    rt.wake()


# ---------------------------------------------------------------------------
# table, caps, records
# ---------------------------------------------------------------------------
def _caps_markup(tid: int, records: bool = False) -> dict:
    rows = [[tx.button("📊 Table", f"trs:{tid}:table"), tx.button("🟠 Orange Cap", f"trs:{tid}:orange"),
             tx.button("🟣 Purple Cap", f"trs:{tid}:purple"), tx.button("⭐ MVP", f"trs:{tid}:mvp")]]
    if records:
        names = [tv.BOARD_TITLES[b] for b in tv.RECORD_BOARDS]
        row: list[dict] = []
        for b, (icon, title) in zip(tv.RECORD_BOARDS, names):
            row.append(tx.button(f"{icon} {title.replace('Most ', '').replace('Best ', '')}"[:20], f"trs:{tid}:{b}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    rows.append([tx.hub_button(tid)])
    return tx.markup(*rows)


async def _board_text(t: dict, board: str) -> str:
    tid = int(t["tid"])
    teams_l = await tr.teams(tid)
    if board == "table":
        fixtures = await tr.fixtures(tid)
        league = [f for f in fixtures if f["stage"] == "league"]
        table = tc.table_from_rows(teams_l, fixtures)
        return tv.table_text(t, table, sum(1 for f in league if f["status"] == "done"), len(league))
    return tv.leaders_text(t, board, await tr.leaders(tid, board, 10), {int(x["team_no"]): x for x in teams_l})


async def _show_board(chat_id: int, message: dict, board: str, records: bool, arg: str = "") -> None:
    t = await _find(chat_id, _user(message)["id"], arg, statuses=("live", "completed", "ready", "registration", "auction"))
    if not t:
        await _send(chat_id, "No tournament found here. Start one with /createtour")
        return
    await _send(chat_id, await _board_text(t, board), _caps_markup(int(t["tid"]), records))


@register("tour")
async def tour_command(message):
    await _show_board(_chat(message)[0], message, "table", False, _args(message))


@register("tourstats")
async def tourstats_command(message):
    await _show_board(_chat(message)[0], message, "orange", True, _args(message))


@register_callback("trs")
async def on_board(cq):
    p = _parts(cq)
    tid, board = int(p[1]), p[2]
    t = await tr.get(tid)
    if t is None or (board != "table" and board not in tv.BOARD_TITLES):
        await _answer(cq, "Not available.", True)
        return
    await _answer(cq)
    await _edit(cq, await _board_text(t, board), _caps_markup(tid, board in tv.RECORD_BOARDS or board in ("orange", "purple", "mvp")))


# ---------------------------------------------------------------------------
# /tsched  (.txt in, current schedule .txt out)
# ---------------------------------------------------------------------------
@register("tsched")
async def tsched_command(message):
    chat_id, _ = _chat(message)
    u = _user(message)
    t = await _find(chat_id, u["id"], _args(message).split()[0] if _args(message) else "",
                    statuses=("registration", "ready", "live"))
    if not t or not rt.can_manage(t, u["id"]):
        await _send(chat_id, "Only the host of a tournament in this chat can manage the schedule.")
        return
    tid = int(t["tid"])
    doc = message.get("document") or ((message.get("reply_to_message") or {}).get("document"))
    if not doc:
        name, text = await rt.schedule_file(tid)
        await _send(chat_id, tc.SCHEDULE_HELP + "\n\n📎 Send your own .txt file with the caption <code>/tsched</code> (or reply to a .txt with /tsched).")
        if text.strip():
            await app.send_document(chat_id, text.encode("utf-8"), filename=name, caption="📄 Current schedule — edit it and send it back with /tsched")
        return
    if int(doc.get("file_size") or 0) > 200_000:
        await _send(chat_id, "⚠️ File too large (max 200 KB).")
        return
    try:
        raw = await app.download_media(doc["file_id"])
        text = raw.decode("utf-8-sig", errors="replace")
    except Exception:
        traceback.print_exc()
        await _send(chat_id, "⚠️ Could not read that file. Send it as a plain .txt document.")
        return
    res = await rt.apply_schedule(tid, u["id"], text)
    if not res["ok"]:
        await _send(chat_id, "⚠️ <b>Schedule not applied</b>\n<blockquote>" + tx.esc(res["error"]) + "</blockquote>")
        return
    await _send(chat_id, f"✅ <b>Schedule saved</b> — {res['matches']} matches over {res['days']} match days.\nPlayers can also play earlier any time with /tplay.",
                tx.markup([tx.hub_button(tid)]))
