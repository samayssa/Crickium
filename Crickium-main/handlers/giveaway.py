"""Owner-only /giveaway: create a player-card giveaway and run it end to end.

Flow
----
/giveaway <player>        preview card + "Are you sure?"  [Yes, start] [Cancel]
  -> Yes                  step 1: when should it START   (now / later / custom)
  -> start chosen         step 2: how long should it RUN (buttons / custom)
  -> duration chosen      setup message is deleted, the giveaway goes live and
                          is sent to every group and every user DM with
                          [Participate] [Join Main Group]
  -> end time             worker draws a winner (activity-weighted, main-group
                          members only), credits the card to the winner's squad
                          and announces it in the main group + winner DM.

/giveaway (no name)       manager: end early / cancel live giveaways.

Nothing is kept in memory that matters: the whole state lives in the database,
so a restart never loses a giveaway (broadcast + draw both resume).
"""

from __future__ import annotations

print("giveaway.py loaded")

import asyncio
import html
import random
import re
import secrets
import traceback

from app import app
from config import ADMIN_USER_ID, MAIN_GROUP_ID
from database import giveaway_repo as repo
from database.broadcast_repo import get_broadcast_targets
from handlers.registry import register, register_callback
from services import giveaway as gw
from services.card_identity import card_identity_key
from utils.bounded_dict import BoundedDict

CB_ADMIN = "gwa"   # gwa:<action>:<id-or-token>[:<arg>]
CB_JOIN = "gwj"    # gwj:<giveaway_id>

START_CHOICES = [("🚀 Start now", 0), ("⏳ In 30 min", 1800), ("⏳ In 1 hour", 3600), ("⏳ In 3 hours", 10800),
                 ("⏳ In 6 hours", 21600), ("⏳ In 12 hours", 43200), ("⏳ In 24 hours", 86400)]
DURATION_CHOICES = [("30 min", 1800), ("1 hour", 3600), ("3 hours", 10800), ("6 hours", 21600),
                    ("12 hours", 43200), ("24 hours", 86400), ("2 days", 172800), ("3 days", 259200),
                    ("7 days", 604800)]

_PICKS: dict[str, dict] = BoundedDict(200)
_TASKS: set[asyncio.Task] = set()
_WAKE = asyncio.Event()
_worker_task: asyncio.Task | None = None


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _is_admin(user_id) -> bool:
    return int(user_id or 0) == int(ADMIN_USER_ID)


def _btn(text: str, data: str | None = None, *, url: str | None = None, style: str | None = None) -> dict:
    button: dict = {"text": text}
    if url:
        button["url"] = url
    else:
        button["callback_data"] = data
    if style:
        button["style"] = style
    return button


def _kb(rows: list[list[dict]]) -> dict:
    return {"inline_keyboard": rows}


def _bot_username() -> str:
    import os
    username = os.getenv("MINIAPP_BOT_USERNAME", "CrickiumBot").strip().lstrip("@")
    return username if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username) else "CrickiumBot"


def _visible_len(text: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", text or "")))


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def wake_worker() -> None:
    _WAKE.set()


async def _answer(callback_query: dict, text: str | None = None, alert: bool = False) -> None:
    try:
        await app.answer_callback_query(callback_query.get("id"), text, show_alert=alert)
    except Exception as exc:
        print(f"[giveaway] answer failed: {exc!r}")


async def _safe_delete(chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await app.delete_message(int(chat_id), int(message_id))
    except Exception as exc:
        print(f"[giveaway] delete skipped: {exc!r}")


async def _edit(chat_id: int, message_id: int, is_photo: bool, text: str, markup: dict | None) -> None:
    if is_photo:
        await app.edit_message_caption(chat_id, message_id, text, parse_mode="HTML", reply_markup=markup)
    else:
        await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=markup)


def _chance_text(chance: float | None) -> str:
    if chance is None:
        return "-"
    if chance < 0.1:
        return "<0.1%"
    return f"{chance:.1f}%"


def _prize_line(g: dict) -> str:
    i = gw.card_info(g["player"])
    return f"{i['full_name']} · OVR {i['ovr']}"


# ---------------------------------------------------------------------------
# texts
# ---------------------------------------------------------------------------
TITLE = "<b>╭━━━〔 🎁 CRICKIUM GIVEAWAY 〕━━━╮</b>"
FOOT = "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"


def _preview_text(card: dict) -> str:
    i = gw.card_info(card)
    return (
        "<b>╭━━━〔 🎁 GIVEAWAY SETUP 〕━━━╮</b>\n\n"
        f"{gw.prize_html(card)}\n\n"
        f"<b>❓ Are you sure you want to start a giveaway for {gw.esc(i['full_name'])}?</b>\n"
        "<i>It will be sent to every group and every user DM.</i>\n"
        f"{FOOT}"
    )


def _start_text(g: dict) -> str:
    return (
        "<b>╭━━〔 ⏰ GIVEAWAY START TIME 〕━━╮</b>\n\n"
        f"<blockquote>🎁 <b>Prize</b> ➤ {gw.esc(_prize_line(g))}</blockquote>\n"
        "<b>Step 1 of 2</b> - when should the giveaway <b>start</b>?\n"
        "<i>Now = it is sent to everyone immediately. Or schedule it for later.</i>\n"
        f"{FOOT}"
    )


def _duration_text(g: dict) -> str:
    delay = int(g.get("start_delay_s") or 0)
    if delay <= 0:
        starts = "🚀 Right now"
    else:
        starts = f"⏳ In {gw.fmt_duration(delay)}"
    return (
        "<b>╭━━〔 ⏳ GIVEAWAY DURATION 〕━━╮</b>\n\n"
        f"<blockquote>🎁 <b>Prize</b> ➤ {gw.esc(_prize_line(g))}\n"
        f"🟢 <b>Starts</b> ➤ {starts}</blockquote>\n"
        "<b>Step 2 of 2</b> - how long should it <b>run</b>?\n"
        "<i>Tap a duration and the giveaway goes live.</i>\n"
        f"{FOOT}"
    )


def _custom_text(g: dict, step: str) -> str:
    what = "START delay" if step == "custom_start" else "DURATION"
    limits = "from <b>now</b> to <b>30d</b>" if step == "custom_start" else "<b>5m</b> to <b>30d</b>"
    return (
        f"<b>╭━━〔 ✏️ CUSTOM {what} 〕━━╮</b>\n\n"
        f"<blockquote>🎁 <b>Prize</b> ➤ {gw.esc(_prize_line(g))}</blockquote>\n"
        "<b>↩️ Reply to THIS message</b> with the time.\n"
        "<blockquote>Examples: <code>90m</code> · <code>2h 30m</code> · <code>1d 12h</code> · <code>3d</code></blockquote>\n"
        f"<i>Allowed: {limits}.</i>\n"
        f"{FOOT}"
    )


def _public_caption(g: dict, *, with_rules: bool = True) -> str:
    start = gw._aware(g.get("start_at"))
    started_text = "Live now" if (start is None or start <= gw.now_utc()) else gw.fmt_ist(start)
    total = int(((gw._aware(g["end_at"]) - (start or gw.now_utc())).total_seconds())) if g.get("end_at") else 0
    parts = [
        TITLE, "",
        f"<b>🏆 PRIZE CARD · Giveaway #{int(g['giveaway_id'])}</b>",
        gw.prize_html(g["player"]), "",
        f"<blockquote>🟢 <b>Starts</b> ➤ {started_text}\n"
        f"⏳ <b>Ends</b> ➤ {gw.fmt_ist(g.get('end_at'))}\n"
        f"🕒 <b>Runs for</b> ➤ {gw.fmt_duration(total)}</blockquote>",
    ]
    if with_rules:
        parts += [
            "",
            "<b>📋 How to win</b>\n"
            "• Be a member of the <b>Main Group</b>\n"
            "• Tap <b>Participate</b> below\n"
            "• More activity (matches, wins, claims) = higher chance",
        ]
    parts.append(FOOT)
    text = "\n".join(parts)
    if _visible_len(text) > 1020 and with_rules:
        return _public_caption(g, with_rules=False)
    return text


def _ended_caption(g: dict) -> str:
    i = gw.card_info(g["player"])
    if g["status"] == "cancelled":
        result = "🛑 <b>This giveaway was cancelled.</b>"
    elif g.get("winner_id"):
        who = _winner_tag(g, with_link=False)
        result = f"🏆 <b>Winner</b> ➤ {who}"
    else:
        result = "😔 <b>No winner this time.</b>"
    return (
        "<b>╭━━━〔 🏁 GIVEAWAY ENDED 〕━━━╮</b>\n\n"
        f"<blockquote>🎁 <b>Prize</b> ➤ {gw.esc(i['full_name'])} · OVR {i['ovr']}\n{result}</blockquote>\n"
        f"<i>Giveaway #{int(g['giveaway_id'])} · {int(g.get('entries_final') or 0)} participants</i>\n"
        f"{FOOT}"
    )


def _winner_tag(g: dict, *, with_link: bool = True) -> str:
    uid = int(g.get("winner_id") or 0)
    name = gw.esc(g.get("winner_name") or "Player")
    username = g.get("winner_username")
    if with_link:
        tag = f'<a href="tg://user?id={uid}">{name}</a>'
        return f"{tag} (@{gw.esc(username)})" if username else tag
    return f"@{gw.esc(username)}" if username else name


def _winner_caption(g: dict, *, dm: bool = False) -> str:
    i = gw.card_info(g["player"])
    where = "Check it in /squad or the Mini App." if dm else "It is now part of their squad."
    text = (
        "<b>╭━━━〔 🏆 GIVEAWAY WINNER 🏆 〕━━━╮</b>\n\n"
        f"🎉 <b>Congratulations</b> {_winner_tag(g)}!\n\n"
        f"<blockquote>👤 <b>{gw.esc(i['full_name'])}</b> {i['flag']} · 💎 {gw.esc(i['rarity'])}\n"
        f"⭐ OVR <b>{i['ovr']}</b> · 🏏 Bat <b>{i['bat']}</b> · 🎯 Bowl <b>{i['bowl']}</b>\n"
        f"{i['role_icon']} {gw.esc(i['role'])}</blockquote>\n\n"
        f"<i>“{gw.esc(random.choice(gw.PUNCHLINES))}”</i>\n\n"
        f"✅ <b>{gw.esc(i['full_name'])} has been successfully credited to your career squad.</b>\n"
        f"<i>{where}</i>\n\n"
        f"📊 Giveaway #{int(g['giveaway_id'])} · {int(g.get('entries_final') or 0)} participants\n"
        f"{FOOT}"
    )
    if _visible_len(text) > 1020:
        text = text.replace(f"<i>{where}</i>\n\n", "\n")
    return text


def _live_row_text(g: dict, entries: int) -> str:
    state = "🟢 Active" if g["status"] == "active" else "🕒 Scheduled"
    when = f"ends {gw.fmt_ist(g['end_at'])}" if g["status"] == "active" else f"starts {gw.fmt_ist(g['start_at'])}"
    return (f"<b>#{int(g['giveaway_id'])}</b> · {gw.esc(_prize_line(g))}\n"
            f"   {state} · {when} · 👥 {entries}")


# ---------------------------------------------------------------------------
# keyboards
# ---------------------------------------------------------------------------
def _confirm_kb(gid: int) -> dict:
    return _kb([[
        _btn("✅ Yes, start giveaway", f"{CB_ADMIN}:yes:{gid}", style="success"),
        _btn("❌ Cancel", f"{CB_ADMIN}:no:{gid}", style="danger"),
    ]])


def _start_kb(gid: int) -> dict:
    rows = [[_btn(START_CHOICES[0][0], f"{CB_ADMIN}:st:{gid}:0", style="success")]]
    rest = [_btn(label, f"{CB_ADMIN}:st:{gid}:{secs}") for label, secs in START_CHOICES[1:]]
    rows += [rest[i:i + 3] for i in range(0, len(rest), 3)]
    rows.append([_btn("✏️ Custom time", f"{CB_ADMIN}:stc:{gid}", style="primary"),
                 _btn("❌ Cancel", f"{CB_ADMIN}:no:{gid}", style="danger")])
    return _kb(rows)


def _duration_kb(gid: int) -> dict:
    items = [_btn(label, f"{CB_ADMIN}:du:{gid}:{secs}", style="primary" if secs == 86400 else None)
             for label, secs in DURATION_CHOICES]
    rows = [items[i:i + 3] for i in range(0, len(items), 3)]
    rows.append([_btn("✏️ Custom", f"{CB_ADMIN}:duc:{gid}", style="primary")])
    rows.append([_btn("⬅️ Back", f"{CB_ADMIN}:bk:{gid}"), _btn("❌ Cancel", f"{CB_ADMIN}:no:{gid}", style="danger")])
    return _kb(rows)


def _custom_kb(gid: int, step: str) -> dict:
    return _kb([[_btn("⬅️ Back", f"{CB_ADMIN}:{'bk' if step == 'custom_duration' else 'bks'}:{gid}"),
                 _btn("❌ Cancel", f"{CB_ADMIN}:no:{gid}", style="danger")]])


async def _public_markup(g: dict) -> dict:
    rows = [[_btn("🎉 Participate", f"{CB_JOIN}:{int(g['giveaway_id'])}", style="success")]]
    link = await gw.main_group_link()
    if link:
        rows.append([_btn("👥 Join Main Group", url=link, style="primary")])
    rows.append([_btn("📱 Open in Mini App", url=f"https://t.me/{_bot_username()}?startapp=gw_{int(g['giveaway_id'])}&mode=fullscreen")])
    return _kb(rows)


# ---------------------------------------------------------------------------
# /giveaway
# ---------------------------------------------------------------------------
async def _find_cards(name: str) -> list[dict]:
    from database.showcase_players_repo import get_showcase_player_by_identity, parse_showcase_image_target
    from database.special_players_repo import get_special_player, search_player_variants, split_player_edition

    showcase_target = parse_showcase_image_target(name)
    if showcase_target:
        base_name, showcase_name = showcase_target
        player = await get_showcase_player_by_identity(base_name, showcase_name)
        return [player] if player else []
    base_name, edition = split_player_edition(name)
    if edition:
        player = await get_special_player(base_name, edition)
        return [player] if player else []
    players = await search_player_variants(name, limit=30)
    if len(players) > 1:
        exact = [p for p in players if str(p.get("name") or "").strip().lower() == name.strip().lower()]
        if len(exact) == 1:
            return exact
    return players


async def _send_manager(chat_id: int) -> None:
    live = await repo.list_live()
    counts = await repo.entry_counts([int(g["giveaway_id"]) for g in live])
    text, markup = _manager_view(live, counts)
    await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


def _manager_view(live: list[dict], counts: dict[int, int]) -> tuple[str, dict | None]:
    lines = [
        "<b>╭━━━〔 🎁 GIVEAWAY MANAGER 〕━━━╮</b>", "",
        "<b>Start one:</b> <code>/giveaway Player Name</code>",
        "<i>e.g. /giveaway Virat Kohli  or  /giveaway Virat Kohli (Legend Edition)</i>", "",
    ]
    rows: list[list[dict]] = []
    if live:
        lines.append("<b>Live giveaways</b>")
        for g in live:
            gid = int(g["giveaway_id"])
            lines.append(_live_row_text(g, counts.get(gid, 0)))
            buttons = []
            if g["status"] == "active":
                buttons.append(_btn(f"⏹ End #{gid} now", f"{CB_ADMIN}:end:{gid}", style="primary"))
            buttons.append(_btn(f"🛑 Cancel #{gid}", f"{CB_ADMIN}:can:{gid}", style="danger"))
            rows.append(buttons)
        rows.append([_btn("🔄 Refresh", f"{CB_ADMIN}:mg:0")])
    else:
        lines.append("<i>No live giveaway right now.</i>")
    lines += ["", FOOT]
    return "\n".join(lines), (_kb(rows) if rows else None)


@register("giveaway")
async def giveaway_command(message):
    chat = message["chat"]
    chat_id = int(chat["id"])
    user_id = int((message.get("from") or {}).get("id") or 0)
    if not _is_admin(user_id):
        await app.send_message(chat_id, "🚫 <b>This command is restricted to the bot owner only.</b>", parse_mode="HTML")
        return

    parts = (message.get("text") or "").split(maxsplit=1)
    name = parts[1].strip() if len(parts) > 1 else ""
    if not name:
        await _send_manager(chat_id)
        return

    cards = await _find_cards(name)
    if not cards:
        await app.send_message(
            chat_id,
            f"⚠️ No player named <b>{gw.esc(name)}</b> found. Check the spelling (use /player to look it up).",
            parse_mode="HTML",
        )
        return
    if len(cards) == 1:
        await _send_preview(chat_id, cards[0])
        return

    token = secrets.token_hex(4)
    shown = cards[:10]
    _PICKS[token] = {"cards": shown, "admin": user_id}
    lines = ["<b>🔎 Several cards match - pick one:</b>", ""]
    for index, card in enumerate(shown, 1):
        i = gw.card_info(card)
        lines.append(f"<b>{index}.</b> {gw.esc(i['full_name'])} {i['flag']} · OVR {i['ovr']} · {gw.esc(i['role'])}")
    if len(cards) > len(shown):
        lines.append(f"\n<i>Showing {len(shown)} of {len(cards)} - type a more exact name to narrow it down.</i>")
    buttons = [_btn(str(n), f"{CB_ADMIN}:pk:{token}:{n - 1}", style="primary") for n in range(1, len(shown) + 1)]
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    rows.append([_btn("❌ Cancel", f"{CB_ADMIN}:pkx:{token}", style="danger")])
    await app.send_message(chat_id, "\n".join(lines), parse_mode="HTML", reply_markup=_kb(rows))


async def _send_preview(chat_id: int, card: dict) -> None:
    key = card_identity_key(card)
    for g in await repo.list_live():
        if g["card_key"] == key:
            await app.send_message(
                chat_id,
                f"⚠️ A giveaway for <b>{gw.esc(_prize_line(g))}</b> is already live (<b>#{int(g['giveaway_id'])}</b>).\n"
                "End or cancel it first with /giveaway.",
                parse_mode="HTML",
            )
            return
    snapshot = gw.squad_entry(card)
    draft = await repo.create_draft(ADMIN_USER_ID, snapshot, key, MAIN_GROUP_ID, chat_id)
    gid = int(draft["giveaway_id"])
    text = _preview_text(snapshot)
    sent = None
    is_photo = True
    try:
        from services.card_provider import get_player_card_bytes
        image_bytes, _custom = await get_player_card_bytes(snapshot)
        sent = await app.send_photo(chat_id, photo=image_bytes, caption=text, parse_mode="HTML", reply_markup=_confirm_kb(gid))
    except Exception as exc:
        print(f"[giveaway] preview image failed ({exc!r}); falling back to text")
    if sent is None:
        is_photo = False
        try:
            sent = await app.send_message(chat_id, text, parse_mode="HTML", reply_markup=_confirm_kb(gid))
        except Exception as exc:
            await repo.cancel_draft(gid)
            print(f"[giveaway] preview send failed: {exc!r}")
            return
    await repo.set_setup_message(gid, chat_id, int(sent.get("message_id") or 0), is_photo)


# ---------------------------------------------------------------------------
# admin callbacks
# ---------------------------------------------------------------------------
async def _show_step(g: dict, step: str) -> None:
    text, markup = {
        "start": (_start_text(g), _start_kb(int(g["giveaway_id"]))),
        "duration": (_duration_text(g), _duration_kb(int(g["giveaway_id"]))),
        "custom_start": (_custom_text(g, "custom_start"), _custom_kb(int(g["giveaway_id"]), "custom_start")),
        "custom_duration": (_custom_text(g, "custom_duration"), _custom_kb(int(g["giveaway_id"]), "custom_duration")),
    }[step]
    await _edit(int(g["setup_chat_id"]), int(g["setup_message_id"]), bool(g.get("setup_photo", True)), text, markup)


async def _launch(g: dict, delay_s: int, duration_s: int) -> bool:
    row = await repo.launch(int(g["giveaway_id"]), delay_s, duration_s)
    if row is None:
        return False
    chat_id = int(g.get("setup_chat_id") or 0)
    await _safe_delete(chat_id, g.get("setup_message_id"))

    scheduled = row["status"] == "scheduled"
    member = await gw.is_main_member(ADMIN_USER_ID, use_cache=False)
    warn = ""
    if member is None:
        warn = ("\n\n⚠️ <b>I can't check members of the main group.</b> Make me an <b>admin</b> there, "
                "otherwise nobody will be able to participate.")
    if not await gw.main_group_link():
        warn += ("\n\n⚠️ <b>I couldn't get a join link for the main group</b>, so the Join Main Group button "
                 "will be missing. Set <code>MAIN_GROUP_LINK</code> in config.py (or make me admin with the "
                 "\"Invite users\" right).")
    text = (
        f"<b>✅ GIVEAWAY #{int(row['giveaway_id'])} {'SCHEDULED' if scheduled else 'IS LIVE'}</b>\n\n"
        f"<blockquote>🎁 <b>Prize</b> ➤ {gw.esc(_prize_line(row))}\n"
        f"🟢 <b>Starts</b> ➤ {'now' if not scheduled else gw.fmt_ist(row['start_at'])}\n"
        f"⏳ <b>Ends</b> ➤ {gw.fmt_ist(row['end_at'])}\n"
        f"🕒 <b>Runs for</b> ➤ {gw.fmt_duration(duration_s)}</blockquote>\n"
        + ("<i>I will send it to every group and DM automatically at the start time.</i>" if scheduled
           else "<i>Sending it to every group and DM now - I will DM you the delivery report.</i>")
        + warn
    )
    try:
        await app.send_message(chat_id or ADMIN_USER_ID, text, parse_mode="HTML")
    except Exception as exc:
        print(f"[giveaway] launch confirmation failed: {exc!r}")
    ensure_worker()
    wake_worker()
    return True


async def _admin_guard(cq: dict) -> bool:
    if not _is_admin((cq.get("from") or {}).get("id")):
        await _answer(cq, "Only the bot owner can use this.", True)
        return False
    return True


@register_callback(CB_ADMIN)
async def on_admin_callback(cq):
    if not await _admin_guard(cq):
        return
    parts = str(cq.get("data") or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    ref = parts[2] if len(parts) > 2 else ""
    arg = parts[3] if len(parts) > 3 else ""
    msg = cq.get("message") or {}
    chat_id = int((msg.get("chat") or {}).get("id") or 0)
    message_id = int(msg.get("message_id") or 0)

    # --- candidate picker (no draft yet) -----------------------------------
    if action in {"pk", "pkx"}:
        pick = _PICKS.get(ref)
        if action == "pkx":
            _PICKS.pop(ref, None)
            await _safe_delete(chat_id, message_id)
            await _answer(cq, "Cancelled.")
            return
        if not pick:
            await _answer(cq, "This list expired - send /giveaway again.", True)
            return
        try:
            card = pick["cards"][int(arg)]
        except (ValueError, IndexError):
            await _answer(cq, "Invalid choice.", True)
            return
        _PICKS.pop(ref, None)
        await _answer(cq)
        await _safe_delete(chat_id, message_id)
        await _send_preview(chat_id, card)
        return

    # --- manager actions (work on live giveaways) ---------------------------
    if action in {"mg", "end", "endy", "can", "cany"}:
        await _manager_action(cq, action, ref, chat_id, message_id)
        return

    # --- setup flow (draft) --------------------------------------------------
    try:
        gid = int(ref)
    except ValueError:
        await _answer(cq, "Invalid request.", True)
        return
    g = await repo.get(gid)
    if not g or g["status"] != "draft":
        await _answer(cq, "This setup is no longer active.", True)
        await _safe_delete(chat_id, message_id)
        return
    if int(g.get("setup_message_id") or 0) != message_id:
        # an old message of the same draft - refresh the real one instead of acting on stale buttons
        await _answer(cq, "Use the latest setup message.", True)
        return

    if action == "no":
        await repo.cancel_draft(gid)
        await _answer(cq, "Giveaway cancelled.")
        await _safe_delete(chat_id, message_id)
        return

    if action == "yes":
        row = await repo.set_step(gid, "start")
        await _answer(cq)
        if row:
            await _show_step(row, "start")
        return

    if action in {"stc", "duc"}:
        step = "custom_start" if action == "stc" else "custom_duration"
        row = await repo.set_step(gid, step)
        await _answer(cq, "Reply to this message with the time.")
        if row:
            await _show_step(row, step)
        return

    if action == "bks":      # back from custom start prompt -> start step
        row = await repo.set_step(gid, "start")
        await _answer(cq)
        if row:
            await _show_step(row, "start")
        return

    if action == "bk":       # back from duration / custom duration -> start step
        row = await repo.set_step(gid, "start", 0)
        await _answer(cq)
        if row:
            await _show_step(row, "start")
        return

    if action == "st":
        try:
            delay = int(arg)
        except ValueError:
            await _answer(cq, "Invalid time.", True)
            return
        if not 0 <= delay <= gw.MAX_START_DELAY_S:
            await _answer(cq, "Start time out of range.", True)
            return
        row = await repo.set_step(gid, "duration", delay)
        await _answer(cq)
        if row:
            await _show_step(row, "duration")
        return

    if action == "du":
        try:
            duration = int(arg)
        except ValueError:
            await _answer(cq, "Invalid duration.", True)
            return
        if not gw.MIN_DURATION_S <= duration <= gw.MAX_DURATION_S:
            await _answer(cq, "Duration out of range.", True)
            return
        if not await _launch(g, int(g.get("start_delay_s") or 0), duration):
            await _answer(cq, "Already launched.", True)
            return
        await _answer(cq, "🎁 Giveaway launched!")
        return

    await _answer(cq, "Unknown action.", True)


async def handle_setup_reply(message: dict) -> bool:
    """main.py hook: the owner replied to a 'custom time' prompt. True = consumed."""
    user_id = int((message.get("from") or {}).get("id") or 0)
    if not _is_admin(user_id):
        return False
    reply = message.get("reply_to_message") or {}
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    reply_id = int(reply.get("message_id") or 0)
    if not reply_id:
        return False
    g = await repo.find_draft_awaiting_reply(user_id, chat_id, reply_id)
    if not g:
        return False

    gid = int(g["giveaway_id"])
    seconds = gw.parse_duration(message.get("text") or "")
    step = g["setup_step"]
    if step == "custom_start":
        valid = seconds is not None and 0 <= seconds <= gw.MAX_START_DELAY_S
    else:
        valid = seconds is not None and gw.MIN_DURATION_S <= seconds <= gw.MAX_DURATION_S
    if not valid:
        await app.send_message(
            chat_id,
            "⚠️ I couldn't use that time. Try <code>90m</code>, <code>2h 30m</code> or <code>1d 12h</code>"
            + (" (5 minutes to 30 days)." if step == "custom_duration" else " (up to 30 days, or <code>now</code>)."),
            parse_mode="HTML",
        )
        return True

    await _safe_delete(chat_id, message.get("message_id"))
    if step == "custom_start":
        row = await repo.set_step(gid, "duration", int(seconds))
        if row:
            await _show_step(row, "duration")
    else:
        await _launch(g, int(g.get("start_delay_s") or 0), int(seconds))
    return True


async def _manager_action(cq: dict, action: str, ref: str, chat_id: int, message_id: int) -> None:
    async def refresh(note: str | None = None) -> None:
        live = await repo.list_live()
        counts = await repo.entry_counts([int(x["giveaway_id"]) for x in live])
        text, markup = _manager_view(live, counts)
        if note:
            text = f"{note}\n\n{text}"
        try:
            await app.edit_message_text(chat_id, message_id, text, parse_mode="HTML", reply_markup=markup)
        except Exception as exc:
            print(f"[giveaway] manager refresh failed: {exc!r}")

    if action == "mg":
        await _answer(cq)
        await refresh()
        return
    try:
        gid = int(ref)
    except ValueError:
        await _answer(cq, "Invalid request.", True)
        return
    g = await repo.get(gid)
    if not g or g["status"] not in {"scheduled", "active"}:
        await _answer(cq, "That giveaway is no longer live.", True)
        await refresh()
        return

    if action == "end":
        await _answer(cq)
        await app.edit_message_text(
            chat_id, message_id,
            f"<b>⏹ End giveaway #{gid} now?</b>\n\n<blockquote>{gw.esc(_prize_line(g))}</blockquote>\n"
            "A winner is drawn right away from everyone who entered.",
            parse_mode="HTML",
            reply_markup=_kb([[_btn("✅ Yes, end now", f"{CB_ADMIN}:endy:{gid}", style="success"),
                               _btn("⬅️ Back", f"{CB_ADMIN}:mg:0", style="danger")]]),
        )
    elif action == "endy":
        if g["status"] != "active":
            await _answer(cq, "Only a running giveaway can be ended.", True)
            await refresh()
            return
        await repo.end_now(gid)
        ensure_worker()
        wake_worker()
        await _answer(cq, "Ending now…")
        await refresh(f"✅ Giveaway #{gid} is being drawn.")
    elif action == "can":
        await _answer(cq)
        await app.edit_message_text(
            chat_id, message_id,
            f"<b>🛑 Cancel giveaway #{gid}?</b>\n\n<blockquote>{gw.esc(_prize_line(g))}</blockquote>\n"
            "No winner is drawn and the card is not given to anyone.",
            parse_mode="HTML",
            reply_markup=_kb([[_btn("🛑 Yes, cancel it", f"{CB_ADMIN}:cany:{gid}", style="danger"),
                               _btn("⬅️ Back", f"{CB_ADMIN}:mg:0")]]),
        )
    elif action == "cany":
        row = await repo.cancel_live(gid)
        if not row:
            await _answer(cq, "Already finished.", True)
            await refresh()
            return
        await _answer(cq, "Giveaway cancelled.")
        await refresh(f"🛑 Giveaway #{gid} cancelled.")
        _spawn(_announce(row))


# ---------------------------------------------------------------------------
# joining (public Participate button)
# ---------------------------------------------------------------------------
@register_callback(CB_JOIN)
async def on_join_callback(cq):
    parts = str(cq.get("data") or "").split(":")
    try:
        gid = int(parts[1])
    except (IndexError, ValueError):
        await _answer(cq, "Invalid giveaway.", True)
        return
    user = cq.get("from") or {}
    uid = int(user.get("id") or 0)
    if not uid or user.get("is_bot"):
        await _answer(cq, "Invalid user.", True)
        return
    try:
        result = await gw.join(gid, uid, user.get("username"), user.get("first_name"))
    except Exception as exc:
        print(f"[giveaway] join failed: {exc!r}")
        traceback.print_exc()
        await _answer(cq, "Something went wrong. Please try again in a moment.", True)
        return

    status = result.get("status")
    if status in {"ok", "already"}:
        head = "✅ You're in!" if status == "ok" else "✅ You're already in."
        text = (f"{head} Giveaway #{gid}\n"
                f"Activity score: {result.get('score', 0):,.0f}\n"
                f"Your chance: ~{_chance_text(result.get('chance'))} · {result.get('entries', 0)} entries")
    else:
        text = result.get("message") or "Could not join this giveaway."
        if status == "not_member":
            text = "🚫 " + text
    await _answer(cq, text[:195], True)


# ---------------------------------------------------------------------------
# broadcast
# ---------------------------------------------------------------------------
_PERMANENT = ("blocked", "deactivated", "chat not found", "forbidden", "kicked", "not enough rights",
              "have no rights", "peer_id_invalid", "user_is_blocked", "input_user_deactivated",
              "chat_write_forbidden", "channel_private", "bot was", "can't initiate")


async def _targets() -> list[dict]:
    rows = await get_broadcast_targets()
    seen: dict[int, str] = {}
    for row in rows:
        kind = str(row.get("target_type") or "").lower()
        kind = "group" if kind in {"group", "supergroup"} else "user" if kind in {"user", "private"} else ""
        cid = int(row.get("chat_id") or 0)
        if kind and cid and cid not in seen:
            seen[cid] = kind
    seen.setdefault(int(MAIN_GROUP_ID), "group")
    ordered = sorted(seen.items(), key=lambda kv: (0 if kv[0] == int(MAIN_GROUP_ID) else 1 if kv[1] == "group" else 2))
    return [{"chat_id": cid, "kind": kind} for cid, kind in ordered]


async def _card_image(card: dict) -> bytes | None:
    try:
        from services.card_provider import get_player_card_bytes
        data, _custom = await get_player_card_bytes(card)
        return data
    except Exception as exc:
        print(f"[giveaway] card image unavailable: {exc!r}")
        return None


async def _send_media(chat_id: int, caption: str, markup: dict | None, photo) -> dict:
    if photo is not None:
        return await app.send_photo(chat_id, photo=photo, caption=caption, parse_mode="HTML", reply_markup=markup)
    return await app.send_message(chat_id, caption, parse_mode="HTML", reply_markup=markup)


async def _is_still_active(gid: int) -> bool:
    g = await repo.get(gid)
    return bool(g and g["status"] == "active")


async def _broadcast(g: dict) -> None:
    gid = int(g["giveaway_id"])
    sent_total = failed_total = 0
    try:
        targets = await _targets()
        done = await repo.delivered_chat_ids(gid)
        todo = [t for t in targets if t["chat_id"] not in done]
        await repo.set_broadcast_total(gid, len(targets))
        caption = _public_caption(g)
        markup = await _public_markup(g)
        image = await _card_image(g["player"])
        photo_ref = image                      # bytes until Telegram gives us a reusable file_id
        index = 0

        # 1) upload the image once (first chat that accepts it), then reuse the file_id everywhere
        while index < len(todo) and photo_ref is not None and not isinstance(photo_ref, str):
            target = todo[index]
            index += 1
            try:
                msg = await _send_media(target["chat_id"], caption, markup, photo_ref)
                file_id = ((msg.get("photo") or {}).get("file_id"))
                if file_id:
                    photo_ref = file_id
                await repo.save_deliveries(gid, [(target["chat_id"], msg.get("message_id"), "photo", True, None)])
                sent_total += 1
            except Exception as exc:
                await repo.save_deliveries(gid, [(target["chat_id"], None, "photo", False, str(exc))])
                failed_total += 1

        # 2) everyone else, a few at a time
        while index < len(todo):
            if not await _is_still_active(gid):
                print(f"[giveaway] broadcast #{gid} stopped: giveaway is no longer active")
                return
            chunk = todo[index:index + 10]
            index += len(chunk)

            async def one(target):
                try:
                    msg = await _send_media(target["chat_id"], caption, markup, photo_ref)
                    return (target["chat_id"], msg.get("message_id"), "photo" if photo_ref is not None else "text", True, None)
                except Exception as exc:
                    return (target["chat_id"], None, "photo" if photo_ref is not None else "text", False, str(exc))

            results = await asyncio.gather(*[one(t) for t in chunk])
            await repo.save_deliveries(gid, results)
            sent_total += sum(1 for r in results if r[3])
            failed_total += sum(1 for r in results if not r[3])
            await asyncio.sleep(0.6)

        sent, failed = await repo.delivery_counts(gid)
        await repo.finish_broadcast(gid, sent, failed)
        try:
            await app.send_message(
                ADMIN_USER_ID,
                f"📡 <b>Giveaway #{gid} broadcast finished</b>\n"
                f"✅ Delivered: <b>{sent}</b>\n❌ Failed: <b>{failed}</b> <i>(blocked bots / removed groups)</i>",
                parse_mode="HTML",
            )
        except Exception as exc:
            print(f"[giveaway] could not DM the broadcast report: {exc!r}")
    except Exception:
        print(f"[giveaway] broadcast #{gid} crashed:")
        traceback.print_exc()
        await asyncio.sleep(30)
        try:
            from database.query import execute
            await execute("UPDATE giveaways SET broadcast_state='pending' WHERE giveaway_id=$1 AND status='active';", gid)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# finishing, announcing, closing
# ---------------------------------------------------------------------------
async def _finish(g: dict) -> None:
    gid = int(g["giveaway_id"])
    try:
        result = await gw.run_draw(g)
        if result["status"] == "stale":
            return
        await _announce(result["giveaway"])
    except Exception:
        print(f"[giveaway] draw #{gid} crashed:")
        traceback.print_exc()
        await asyncio.sleep(20)
        try:
            await repo.reopen_ending(gid)
        except Exception:
            pass


async def _announce(g: dict) -> None:
    """Post the result (winner / no winner / cancelled) and close the old Participate buttons."""
    gid = int(g["giveaway_id"])
    try:
        if g["status"] == "ended" and g.get("winner_id"):
            await _announce_winner(g)
        elif g["status"] == "ended":
            await _announce_no_winner(g)
        else:  # cancelled
            # A giveaway cancelled before its start time was never shown to anybody
            # (broadcast_state is still 'pending'), so there is nothing to retract in public.
            if str(g.get("broadcast_state") or "pending") != "pending":
                try:
                    await app.send_message(
                        int(g["main_chat_id"]),
                        f"🛑 <b>Giveaway #{gid} ({gw.esc(_prize_line(g))}) was cancelled.</b>",
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    print(f"[giveaway] cancel notice failed: {exc!r}")
        await repo.mark_announced(gid)
    except Exception:
        print(f"[giveaway] announce #{gid} crashed:")
        traceback.print_exc()
        return
    await _close_deliveries(g)


async def _announce_winner(g: dict) -> None:
    gid = int(g["giveaway_id"])
    image = await _card_image(g["player"])
    photo_ref = image
    caption = _winner_caption(g)
    dm_caption = _winner_caption(g, dm=True)

    main_ok = False
    for attempt in range(3):
        try:
            msg = await _send_media(int(g["main_chat_id"]), caption, None, photo_ref)
            file_id = (msg.get("photo") or {}).get("file_id")
            if file_id:
                photo_ref = file_id
            main_ok = True
            break
        except Exception as exc:
            print(f"[giveaway] winner post to main group failed (try {attempt + 1}): {exc!r}")
            await asyncio.sleep(3)
    try:
        await _send_media(int(g["winner_id"]), dm_caption, None, photo_ref)
    except Exception as exc:
        print(f"[giveaway] winner DM failed (user may not have started the bot): {exc!r}")
    try:
        await app.send_message(
            ADMIN_USER_ID,
            f"🏆 <b>Giveaway #{gid} finished</b>\n"
            f"Winner: {_winner_tag(g)}\nPrize: {gw.esc(_prize_line(g))}\n"
            f"Entries: {int(g.get('entries_final') or 0)}"
            + ("" if main_ok else "\n⚠️ <b>I could not post in the main group - check my permissions there.</b>"),
            parse_mode="HTML",
        )
    except Exception as exc:
        print(f"[giveaway] admin summary failed: {exc!r}")


async def _announce_no_winner(g: dict) -> None:
    gid = int(g["giveaway_id"])
    note = g.get("result_note") or "No eligible participants"
    try:
        await app.send_message(
            int(g["main_chat_id"]),
            f"<b>🏁 Giveaway #{gid} ended</b>\n\n<blockquote>🎁 {gw.esc(_prize_line(g))}\n"
            f"😔 No winner this time - {gw.esc(note)}.</blockquote>\n<i>Stay active - the next giveaway is coming!</i>",
            parse_mode="HTML",
        )
    except Exception as exc:
        print(f"[giveaway] no-winner notice failed: {exc!r}")
    try:
        await app.send_message(
            ADMIN_USER_ID,
            f"🏁 <b>Giveaway #{gid} ended without a winner</b>\n{gw.esc(note)}",
            parse_mode="HTML",
        )
    except Exception:
        pass


async def _close_deliveries(g: dict) -> None:
    """Replace every delivered 'Participate' message by the ended version (no more buttons)."""
    gid = int(g["giveaway_id"])
    try:
        rows = await repo.sent_deliveries(gid)
        caption = _ended_caption(g)
        for start in range(0, len(rows), 10):
            chunk = rows[start:start + 10]

            async def edit(row):
                payload = {"chat_id": int(row["chat_id"]), "message_id": int(row["message_id"]),
                           "parse_mode": "HTML", "reply_markup": {"inline_keyboard": []}}
                try:
                    if row.get("kind") == "text":
                        await gw.tg_call("editMessageText", {**payload, "text": caption})
                    else:
                        await gw.tg_call("editMessageCaption", {**payload, "caption": caption})
                except Exception as exc:
                    if "retry after" in str(exc).lower():
                        await asyncio.sleep(5)

            await asyncio.gather(*[edit(r) for r in chunk])
            await asyncio.sleep(0.6)
        await repo.mark_closed(gid)
    except Exception:
        print(f"[giveaway] closing #{gid} crashed:")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------
async def _startup_recovery() -> None:
    await repo.reset_after_restart()
    for g in await repo.unannounced():
        _spawn(_announce(g))
    for g in await repo.unclosed():
        _spawn(_close_deliveries(g))
    cleaned = await repo.stale_drafts_cleanup()
    if cleaned:
        print(f"[giveaway] cleaned {cleaned} stale draft(s)")


async def _tick() -> float:
    """One scheduling pass. Returns how many seconds the worker may sleep."""
    live = await repo.list_live()
    if not live:
        return 120.0
    await repo.claim_due_start()
    for g in await repo.claim_pending_broadcasts():
        _spawn(_broadcast(g))
    for g in await repo.claim_due_end():
        _spawn(_finish(g))
    wait = 15.0
    now = gw.now_utc()
    for g in live:
        edge = g.get("start_at") if g["status"] == "scheduled" else g.get("end_at")
        edge = gw._aware(edge)
        if edge:
            wait = min(wait, max(1.0, (edge - now).total_seconds()))
    return wait


async def _worker() -> None:
    try:
        await repo.ensure_schema()
        await _startup_recovery()
    except Exception:
        print("[giveaway] startup recovery failed:")
        traceback.print_exc()
    cycles = 0
    while True:
        wait = 30.0
        try:
            wait = await _tick()
            cycles += 1
            if cycles % 240 == 0:
                await repo.stale_drafts_cleanup()
        except Exception:
            print("[giveaway] worker tick failed:")
            traceback.print_exc()
        try:
            await asyncio.wait_for(_WAKE.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass
        _WAKE.clear()


def ensure_worker() -> None:
    """Start the background worker once (safe to call repeatedly)."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker())
        print("[giveaway] worker started")


def start_giveaway_worker() -> None:
    ensure_worker()
