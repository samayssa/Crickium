"""Auction tournaments: /myteam (squad, purse, progress), /cobidder (bid on behalf of a team), and the
join → team name → team logo flow of Custom tournaments.

Everything is stored in the tournament tables (see database/tournament_repo.py); this module only turns
commands / buttons / the DM picture into calls to the repo and renders the answers.
"""
from __future__ import annotations

print("tournament_team.py loaded")

import html
import traceback
from typing import Any

from app import app
from database import tournament_auction_repo as ar
from database import tournament_repo as tr
from handlers.registry import register, register_callback
from services import tournament_core as tc
from services import tournament_text as tx
from utils.bounded_dict import BoundedDict

NAME_MAX = 18
_COB: dict[str, dict] = BoundedDict(200)





def _t():
    from handlers import tournament as ht
    return ht


def link(user_id: Any, name: Any) -> str:
    """A tappable mention that always shows the person's name (and notifies them)."""
    return f'<a href="tg://user?id={int(user_id)}">{html.escape(str(name or "Player"), quote=False)}</a>'


def owner_link(team: dict) -> str:
    return link(team["owner_id"], team.get("owner_name")) if team.get("owner_id") else "—"


def _bar(have: int, total: int, width: int = 10) -> str:
    total = max(1, int(total))
    filled = max(0, min(width, round(width * int(have) / total)))
    return "▰" * filled + "▱" * (width - filled)


async def _delete(chat_id: int, message_id: int) -> None:
    try:
        await app.delete_message(int(chat_id), int(message_id))
    except Exception:
        pass


async def _send_photo(chat_id: int, file_id: str, caption: str, markup: dict | None = None) -> int | None:
    msg = await app.send_photo(chat_id, photo=file_id, caption=caption, parse_mode="HTML", reply_markup=markup)
    return int((msg or {}).get("message_id") or 0) or None


async def _edit_any(cq: dict, text: str, markup: dict | None = None) -> None:
    """Edit the callback's message whether it is a text message or a photo with a caption."""
    m = cq.get("message") or {}
    chat_id, mid = int((m.get("chat") or {}).get("id") or 0), int(m.get("message_id") or 0)
    try:
        if m.get("photo"):
            await app.edit_message_caption(chat_id, mid, text, parse_mode="HTML", reply_markup=markup)
        else:
            await app.edit_message_text(chat_id, mid, text, parse_mode="HTML", reply_markup=markup)
    except Exception as exc:
        if "not modified" not in str(exc).lower():
            print(f"[tournament_team] edit failed: {exc!r}")





def logo_question(tid: int, team_no: int) -> dict:
    return tx.markup([tx.button("🖼 Yes, I want", f"trt:{tid}:{team_no}:ly", style="success"), tx.button("✖ Cancel", f"trt:{tid}:{team_no}:ln")])


async def ask_name(t: dict, team: dict, user: dict) -> None:
    """Right after Join: ask the new owner whether they want a custom team name."""
    tid, tn = int(t["tid"]), int(team["team_no"])
    text = (f"{link(user['id'], user['first_name'])}, you joined <b>{tx.esc(t['name'])}</b> 🎉\n"
            f"<blockquote>🏷 Do you want to add your <b>custom team name</b>?\n"
            f"Default: <b>{tx.esc(team['name'])}</b></blockquote>")
    kb = tx.markup([tx.button("✍️ Yes, I want", f"trt:{tid}:{tn}:ny", style="success"), tx.button("🏷 No, default", f"trt:{tid}:{tn}:nd")])
    await _t()._send(int(t["host_chat_id"]), text, kb)


def _registered_text(t: dict, team: dict, user_id: Any, user_name: Any) -> str:
    return (f"✅ <b>Registration completed!</b>\n<blockquote>🏆 {tx.esc(t['name'])}\n🎽 Team ➤ <b>{tx.esc(team['name'])}</b>\n"
            f"👤 Owner ➤ {link(user_id, user_name)}</blockquote>"
            f"🖼 Do you want to add your <b>team logo image</b>?")


async def after_registration(t: dict, team: dict, user: dict) -> None:
    """The name is final (typed with /teamname): confirm and ask for the logo."""
    await _t()._send(int(t["host_chat_id"]), _registered_text(t, team, user["id"], user["first_name"]), logo_question(int(t["tid"]), int(team["team_no"])))
    await _t()._refresh_lobby(int(t["tid"]))


@register_callback("trt")
async def on_team_flow(cq):
    ht = _t()
    p = ht._parts(cq)
    try:
        tid, tn, act = int(p[1]), int(p[2]), p[3]
    except (IndexError, ValueError):
        await ht._answer(cq, "Unknown action.", True)
        return
    t, team = await tr.get(tid), await tr.team(tid, tn)
    u = ht._user(cq)
    if t is None or team is None:
        await ht._answer(cq, "This team no longer exists.", True)
        return
    if not team.get("owner_id") or int(team["owner_id"]) != u["id"]:
        await ht._answer(cq, "Only the team owner can use these buttons.", True)
        return
    chat_id = int((cq.get("message") or {}).get("chat", {}).get("id") or 0)
    in_dm = chat_id == u["id"]

    if act == "ny":
        await ht._answer(cq)
        await ht._edit(cq, (f"✍️ {link(u['id'], u['first_name'])}, send your team name with this command:\n"
                            f"<blockquote><code>/teamname Your Team Name</code>\nMax {NAME_MAX} characters · example: <code>/teamname Royal Strikers</code></blockquote>"),
                       tx.markup([tx.button("🏷 Keep default name", f"trt:{tid}:{tn}:nd")]))
        return
    if act == "nd":
        await tr.set_pending(tid, tn, False)
        await ht._answer(cq, "✅ Registered!")
        await ht._edit(cq, _registered_text(t, team, u["id"], u["first_name"]), logo_question(tid, tn))
        await ht._refresh_lobby(tid)
        return
    if act == "ly":
        await ht._answer(cq)
        await tr.set_pending(tid, tn, False)
        await tr.set_logo_wait(tid, tn, True)
        await _delete(chat_id, int((cq.get("message") or {}).get("message_id") or 0))
        dm = f"https://t.me/{tx.bot_username()}?start=teamlogo"
        await ht._send(int(t["host_chat_id"]),
                       f"🖼 {link(u['id'], u['first_name'])}, send me your <b>team image</b> in DM.\n"
                       f"<blockquote>Team ➤ <b>{tx.esc(team['name'])}</b>\nTap the button, then send the picture as a normal photo.</blockquote>",
                       tx.markup([tx.button("📩 Open DM", url=dm, style="primary")]))
        await ht._refresh_lobby(tid)
        return
    if act == "ln":
        await tr.set_logo_wait(tid, tn, False)
        await ht._answer(cq, "Okay, no logo.")
        await ht._edit(cq, f"✅ <b>{tx.esc(team['name'])}</b> is registered — {link(u['id'], u['first_name'])}. You can add a logo later with /teamlogo.")
        await ht._refresh_lobby(tid)
        return
    if act in ("ok", "ch", "no") and in_dm:
        if act == "ok":
            row = await tr.commit_logo(tid, tn)
            if row is None:
                await ht._answer(cq, "Send the picture again, please.", True)
                return
            await ht._answer(cq, "✅ Logo saved!")
            await _edit_any(cq, f"✅ <b>Team logo saved</b> for <b>{tx.esc(team['name'])}</b> — it now shows in /myteam, the auction and the Mini App.")
            try:
                await ht._send(int(t["host_chat_id"]), f"🖼 {link(u['id'], u['first_name'])} added the team logo of <b>{tx.esc(team['name'])}</b>.")
            except Exception:
                pass
        elif act == "ch":
            await tr.set_logo_wait(tid, tn, True)
            await ht._answer(cq, "Send a new picture.")
            await _edit_any(cq, "🔄 Okay — send me a different image for your team logo.")
        else:
            await tr.set_logo_wait(tid, tn, False)
            await ht._answer(cq, "Cancelled.")
            await _edit_any(cq, "✖ Logo cancelled. You can try again with /teamlogo.")
        return
    await ht._answer(cq, "That button belongs to another chat.", True)


@register("teamlogo")
async def teamlogo_command(message):
    """Add / change the team logo later (Custom tournaments)."""
    ht = _t()
    chat_id, _ = ht._chat(message)
    u = ht._user(message)
    t = await ht._find(chat_id, u["id"], statuses=("registration", "auction", "ready", "live"))
    team = await tr.team_of_user(int(t["tid"]), u["id"]) if t else None
    if not t or not team or t.get("engine") != "PLAY":
        await ht._send(chat_id, "Team logos are for Custom tournaments — you need a team in one.")
        return
    await tr.set_logo_wait(int(t["tid"]), int(team["team_no"]), True)
    dm = f"https://t.me/{tx.bot_username()}?start=teamlogo"
    await ht._send(chat_id, f"🖼 {link(u['id'], u['first_name'])}, send me your <b>team image</b> in DM.",
                   tx.markup([tx.button("📩 Open DM", url=dm, style="primary")]))


async def handle_dm_photo(message: dict) -> bool:
    """A photo sent to the bot in DM: if the sender is waiting to give a team logo, ask for confirmation."""
    chat = message.get("chat") or {}
    if str(chat.get("type") or "").lower().replace("chattype.", "") != "private":
        return False
    photo = message.get("photo")
    if not photo or str(message.get("text") or "").startswith("/"):
        return False
    uid = int((message.get("from") or {}).get("id") or 0)
    file_id = (photo or {}).get("file_id")
    if not uid or not file_id:
        return False
    team = await tr.team_waiting_for_logo(uid)
    if team is None:
        return False
    tid, tn = int(team["tid"]), int(team["team_no"])
    await tr.set_logo_pending(tid, tn, str(file_id))
    caption = (f"❓ <b>Are you sure you want to use this image as your team logo?</b>\n"
               f"<blockquote>🎽 Team ➤ <b>{tx.esc(team['name'])}</b>\n🏆 Tournament ➤ {tx.esc(team.get('tour_name'))}\n"
               f"👥 Group ➤ {tx.esc(team.get('group_name') or '—')}</blockquote>")
    kb = tx.markup([tx.button("✅ Yes, I want", f"trt:{tid}:{tn}:ok", style="success"), tx.button("🔄 Change", f"trt:{tid}:{tn}:ch")],
                   [tx.button("✖ Cancel", f"trt:{tid}:{tn}:no", style="danger")])
    await _send_photo(int(chat["id"]), str(file_id), caption, kb)
    return True


async def dm_prompt(chat_id: int, user_id: int) -> None:
    """/start teamlogo (the Open DM button)."""
    team = await tr.team_waiting_for_logo(int(user_id))
    if team is None:
        await _t()._send(chat_id, "🖼 No team logo is waiting. Join a Custom tournament and choose “Yes, I want” for the logo, or send /teamlogo in your group.")
        return
    await _t()._send(chat_id, f"🖼 Send me the picture for <b>{tx.esc(team['name'])}</b> now — as a normal photo (not a file).")





async def _target_team(message: dict, t: dict, words: list[str]) -> tuple[dict | None, str | None]:
    """(team, error) of the person / team asked for; no argument = the sender's own team."""
    ht = _t()
    tid = int(t["tid"])
    u = ht._user(message)
    reply_from = (message.get("reply_to_message") or {}).get("from") or None
    if reply_from and not reply_from.get("is_bot"):
        team = await tr.team_of_bidder(tid, int(reply_from["id"]))
        return (team, None) if team else (None, f"{tx.esc(reply_from.get('first_name') or 'That user')} has no team in this tournament.")
    if words:
        tok = " ".join(words)
        user_tok = next((w for w in words if ht._is_user_token(w)), None)
        if user_tok:
            target = await ht._lookup_user(user_tok)
            if target is None:
                return None, f"I do not know {tx.esc(user_tok)} yet."
            team = await tr.team_of_bidder(tid, int(target["id"]))
            return (team, None) if team else (None, f"{tx.esc(target.get('first_name') or user_tok)} has no team in this tournament.")
        no = tc._resolve_team(tok, await tr.teams(tid))
        team = await tr.team(tid, no) if no is not None else None
        return (team, None) if team else (None, f"No team called “{tx.esc(tok)}” here. Use its short name, e.g. <code>/myteam CSK</code>.")
    team = await tr.team_of_bidder(tid, u["id"])
    if team is None:
        return None, "You do not have a team in this tournament. Reply to a player or send <code>/myteam TEAM</code> to see another team."
    return team, None


async def team_card(t: dict, team: dict, viewer_id: int) -> str:
    tid, tn = int(t["tid"]), int(team["team_no"])
    squad = await ar.squad(tid, tn)
    cob = await tr.cobidder_of_team(tid, tn)
    rules = tc.tournament_rules(t)
    purse_total = int(t.get("purse_lakh") or 0)
    left, spent = int(team.get("purse_left") or 0), int(team.get("spent") or 0)
    n, mx = len(squad), int(rules["squad_max"])
    counts = tc.role_counts(squad)
    os_n = sum(1 for p in squad if p.get("overseas"))
    need = tc.squad_needs(counts, os_n, rules)
    need_txt = tc.needs_text({k: v for k, v in need.items() if k != "squad"})
    if need.get("squad"):
        need_txt = (need_txt + ", " if need_txt else "") + f"{need['squad']} more for the minimum"
    mix = " · ".join(f"{tc.ROLE_ICON[r]} {counts[r]}" for r in tc.ROLES)
    mine = int(team.get("owner_id") or 0) == int(viewer_id) or (cob and int(cob["user_id"]) == int(viewer_id))
    head = [f"🎽 <b>{tx.esc(team['name'])}</b>" + (f" · <i>{tx.esc(tx.team_short(team))}</i>" if tx.team_short(team).casefold() != str(team['name']).casefold() else ""),
            f"🏆 {tx.esc(t['name'])}" + (" · <i>your team</i>" if mine else ""),
            f"👤 <b>Owner</b> ➤ {owner_link(team)}"]
    if cob:
        head.append(f"🤝 <b>Co-bidder</b> ➤ {link(cob['user_id'], cob.get('first_name'))}")
    stat = [f"💰 <b>Purse left</b> ➤ {tx.esc(tc.fmt_money(left))} <i>of {tx.esc(tc.fmt_money(purse_total))}</i>",
            f"💸 <b>Spent</b> ➤ {tx.esc(tc.fmt_money(spent))}",
            f"👥 <b>Squad</b> ➤ {n}/{mx} {_bar(n, mx)}  <i>(min {rules['squad_min']})</i>",
            f"📊 {mix}"]
    if rules["overseas_squad_max"] or rules["overseas_squad_min"]:
        stat.append(f"✈️ <b>Overseas</b> ➤ {os_n}" + (f"/{rules['overseas_squad_max']}" if rules["overseas_squad_max"] else ""))
    if need_txt:
        stat.append(f"⚠️ <i>Still must sign: {tx.esc(need_txt)}</i>")
    out = ["\n".join(head), "<blockquote>" + "\n".join(stat) + "</blockquote>"]
    if not squad:
        out.append("<i>No player bought yet.</i>")
    for role in tc.ROLES:
        rows = sorted((p for p in squad if tc.norm_role(p.get("role")) == role), key=lambda p: -int(p.get("price_lakh") or 0))
        if not rows:
            continue
        lines = [f"{i}. {tx.esc(p['name'])}{' ✈️' if p.get('overseas') else ''} — <b>{tx.esc(tc.fmt_money(p.get('price_lakh')))}</b>" for i, p in enumerate(rows, start=1)]
        out.append(f"<b>{tc.ROLE_ICON[role]} {tc.ROLE_PLURAL[role]} ({len(rows)})</b>\n<blockquote>" + "\n".join(lines) + "</blockquote>")
    return "\n".join(out)


@register("myteam")
async def myteam_command(message):
    """/myteam — your squad, what each player cost, purse and squad progress.
    /myteam CSK · /myteam @user · /myteam 123456 · reply to a player — see another team."""
    ht = _t()
    chat_id, _ = ht._chat(message)
    u = ht._user(message)
    t = await ht._find(chat_id, u["id"])
    if not t or t.get("mode") != "auction":
        await ht._send(chat_id, "No auction tournament found here.")
        return
    team, err = await _target_team(message, t, ht._args(message).split())
    if team is None:
        await ht._send(chat_id, f"⚠️ {err}")
        return
    text = await team_card(t, team, u["id"])
    tid = int(t["tid"])
    rows = [[tx.room_button(tid)] if t["status"] == "auction" else [tx.hub_button(tid)]]
    kb = tx.markup(*rows)
    logo = team.get("logo_file_id")
    if logo:
        try:
            if len(text) <= 1000:
                await _send_photo(chat_id, logo, text, kb)
                return
            await _send_photo(chat_id, logo, f"🎽 <b>{tx.esc(team['name'])}</b>")
        except Exception:
            traceback.print_exc()
    await ht._send(chat_id, text, kb)





COBID_HELP = ("🤝 <b>Co-bidder</b>\n<blockquote>Let a friend bid for your team. Everything they win joins <b>your</b> squad and purse.\n\n"
              "<b>Add</b> (the friend must not be registered in this tournament)\n"
              "1️⃣ Reply to their message: <code>/cobidder</code>\n"
              "2️⃣ <code>/cobidder @username</code>\n"
              "3️⃣ <code>/cobidder 123456789</code>\n\n"
              "<b>Remove</b>: <code>/cobidder remove</code></blockquote>")


@register("cobidder")
@register("coowner")
async def cobidder_command(message):
    ht = _t()
    chat_id, _ = ht._chat(message)
    u = ht._user(message)
    t = await ht._find(chat_id, u["id"], statuses=("registration", "auction", "ready"))
    if not t or t.get("mode") != "auction":
        await ht._send(chat_id, "No auction tournament is open for co-bidders here.")
        return
    tid = int(t["tid"])
    mine = await tr.team_of_user(tid, u["id"])
    as_cob = None if mine else await tr.team_of_bidder(tid, u["id"])
    words = ht._args(message).split()
    if as_cob:
        await ht._send(chat_id, f"🤝 You are the co-bidder of <b>{tx.esc(as_cob['name'])}</b>. Bid with /bid for the team.\n<i>Only the owner can change co-bidders, but you may leave.</i>",
                       tx.markup([tx.button("🚪 Leave co-bidder role", f"trk:{tid}:{int(as_cob['team_no'])}:{u['id']}:r", style="danger")]))
        return
    if mine is None:
        await ht._send(chat_id, "🚫 Only registered team owners can add a co-bidder. Join the tournament first.")
        return
    tn = int(mine["team_no"])
    existing = await tr.cobidder_of_team(tid, tn)
    async def ask_remove(row: dict) -> None:
        cid = int(row["user_id"])
        await ht._send(chat_id, (f"🗑 {link(u['id'], u['first_name'])}, remove {link(cid, row.get('first_name'))} as co-bidder of <b>{tx.esc(mine['name'])}</b>?\n"
                                 "<blockquote>They will no longer be able to bid for your team. Players already won stay in your squad.</blockquote>"),
                       tx.markup([tx.button("✅ Yes, remove", f"trk:{tid}:{tn}:{cid}:r", style="danger"), tx.button("✖ Cancel", f"trk:{tid}:{tn}:{cid}:n")]))

    if words and words[0].lower() in ("remove", "rm", "del", "delete"):
        if existing is None:
            await ht._send(chat_id, "Your team has no co-bidder.")
            return
        await ask_remove(existing)
        return
    reply_from = (message.get("reply_to_message") or {}).get("from") or None
    target = None
    user_tok = next((w for w in words if ht._is_user_token(w)), None)
    if user_tok:
        target = await ht._lookup_user(user_tok)
        if target is None:
            await ht._send(chat_id, f"⚠️ I do not know {tx.esc(user_tok)} yet — they must have used this bot once. Reply to their message instead, or use their user ID.")
            return
    elif reply_from:
        if reply_from.get("is_bot"):
            await ht._send(chat_id, "🤖 A bot cannot be a co-bidder.")
            return
        target = {"id": int(reply_from["id"]), "username": reply_from.get("username"), "first_name": reply_from.get("first_name") or "Player"}
    if target is None:
        if existing:
            await ht._send(
                chat_id, COBID_HELP + f"\n\n🤝 Current co-bidder: {link(existing['user_id'], existing.get('first_name'))}",
                tx.markup([tx.button("🗑 Remove co-bidder", f"trk:{tid}:{tn}:{int(existing['user_id'])}:r", style="danger")]))
        else:
            await ht._send(chat_id, COBID_HELP)
        return
    if existing is not None and int(target["id"]) == int(existing["user_id"]):
        await ask_remove(existing)          # same command on the current co-bidder = remove him
        return
    if int(target["id"]) == u["id"]:
        await ht._send(chat_id, "😄 You already bid for your own team.")
        return
    if existing is not None:
        await ht._send(chat_id, f"⚠️ <b>{tx.esc(mine['name'])}</b> already has a co-bidder ({link(existing['user_id'], existing.get('first_name'))}). Remove them first with <code>/cobidder remove</code>.")
        return
    if await tr.is_registered_user(tid, int(target["id"])):
        await ht._send(chat_id, f"⚠️ {link(target['id'], target['first_name'])} is already registered in this tournament, so they cannot be your co-bidder.")
        return
    _COB[f"{tid}:{tn}:{int(target['id'])}"] = {"first_name": target.get("first_name"), "username": target.get("username")}
    text = (f"🤝 {link(u['id'], u['first_name'])}, are you sure you want to add {link(target['id'], target['first_name'])} as your <b>co-bidder</b>?\n"
            f"<blockquote>🎽 Team ➤ <b>{tx.esc(mine['name'])}</b>\n"
            f"• {link(target['id'], target['first_name'])} can bid with /bid on your behalf\n"
            f"• every player they win goes to <b>your</b> squad and is paid from <b>your</b> purse\n"
            f"• you can remove them any time with <code>/cobidder remove</code></blockquote>")
    kb = tx.markup([tx.button("✅ Yes, add", f"trk:{tid}:{tn}:{int(target['id'])}:y", style="success"), tx.button("✖ Cancel", f"trk:{tid}:{tn}:{int(target['id'])}:n", style="danger")])
    await ht._send(chat_id, text, kb)


@register_callback("trk")
async def on_cobidder(cq):
    ht = _t()
    p = ht._parts(cq)
    try:
        tid, tn, target_id, act = int(p[1]), int(p[2]), int(p[3]), p[4]
    except (IndexError, ValueError):
        await ht._answer(cq, "Unknown action.", True)
        return
    u = ht._user(cq)
    team = await tr.team(tid, tn)
    if team is None:
        await ht._answer(cq, "This team no longer exists.", True)
        return
    if act == "r":                                                       # remove: the owner, or the co-bidder leaving
        if u["id"] != int(team.get("owner_id") or 0) and u["id"] != target_id:
            await ht._answer(cq, "Only the team owner (or the co-bidder) can do this.", True)
            return
        cur = await tr.cobidder_of_team(tid, tn)
        if cur is None or int(cur["user_id"]) != target_id:
            await ht._answer(cq, "That person is not the co-bidder any more.", True)
            await ht._edit(cq, "ℹ️ Nothing to remove — the co-bidder was already changed.")
            return
        await tr.remove_cobidder(tid, tn)
        await ht._answer(cq, "✅ Removed.")
        await ht._edit(cq, f"✅ {link(target_id, cur.get('first_name'))} is no longer co-bidder of <b>{tx.esc(team['name'])}</b>. Owner: {owner_link(team)}")
        return
    if not team.get("owner_id") or int(team["owner_id"]) != u["id"]:
        await ht._answer(cq, "Only the team owner can answer this.", True)
        return
    info = _COB.pop(f"{tid}:{tn}:{target_id}", None) or {}
    name = info.get("first_name")
    if not name:
        looked = await ht._lookup_user(str(target_id))
        name = (looked or {}).get("first_name") or "Player"
        info = {"first_name": name, "username": (looked or {}).get("username")}
    if act == "n":
        await ht._answer(cq, "Cancelled.")
        await ht._edit(cq, f"✖ Cancelled — {link(target_id, name)} was not added.")
        return
    try:
        await tr.add_cobidder(tid, tn, {"id": target_id, "first_name": name, "username": info.get("username")}, u["id"])
    except tr.TourError as exc:
        await ht._answer(cq, exc.message[:190], True)
        await ht._edit(cq, f"⚠️ {tx.esc(exc.message)}")
        return
    await ht._answer(cq, "✅ Co-bidder added!")
    await ht._edit(cq, f"✅ {link(target_id, name)} is now the <b>co-bidder</b> of <b>{tx.esc(team['name'])}</b>.\n"
                       f"<blockquote>They can bid with /bid for this team. Owner: {owner_link(team)}</blockquote>")
