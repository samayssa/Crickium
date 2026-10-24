from __future__ import annotations

import asyncio
from collections import OrderedDict

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID
from database.broadcast_repo import get_broadcast_targets, upsert_chat


def _chat_key(target: dict) -> int:
    return int(target.get("chat_id") or 0)


def _target_type(target: dict) -> str:
    value = str(target.get("target_type") or "").lower()
    if value in {"private", "user"}:
        return "user"
    if value in {"group", "supergroup"}:
        return "group"
    if value == "channel":
        return "channel"
    return value or "unknown"


def _merge_targets(*target_lists: list[dict]) -> list[dict]:
    """Merge DB and live-Telegram targets without requiring DB membership."""
    merged: OrderedDict[int, dict] = OrderedDict()
    for target_list in target_lists:
        for raw in target_list or []:
            target = dict(raw or {})
            chat_id = _chat_key(target)
            if not chat_id:
                continue
            target["chat_id"] = chat_id
            target["target_type"] = _target_type(target)
            existing = merged.get(chat_id)
            if existing is None:
                merged[chat_id] = target
            else:
                if existing.get("target_type") in {None, "", "unknown"} and target.get("target_type") not in {None, "", "unknown"}:
                    existing["target_type"] = target["target_type"]
                if not existing.get("title") and target.get("title"):
                    existing["title"] = target["title"]
                if target.get("source") == "telegram_dialogs":
                    existing["source"] = "db+telegram_dialogs"
    return list(merged.values())


def _dashboard(total, users, groups, channels, sent, failed, skipped, failures, discovered_db, discovered_live):
    lines = [
        "<b>╭━━━〔 📡 BROADCAST DASHBOARD 〕━━━╮</b>",
        "",
        f"📦 <b>Targets discovered:</b> {total}",
        f"🗃️ DB targets: {discovered_db}",
        f"📲 Live Telegram targets: {discovered_live}",
        "",
        f"👤 <b>Users:</b> {users['sent']} sent / {users['failed']} failed",
        f"👥 <b>Groups:</b> {groups['sent']} sent / {groups['failed']} failed",
        f"📢 <b>Channels:</b> {channels['sent']} sent / {channels['failed']} failed",
        "",
        f"✅ <b>Total forwarded:</b> {sent}",
        f"❌ <b>Total failed:</b> {failed}",
        f"⏭️ <b>Skipped:</b> {skipped}",
    ]
    if failures:
        lines += ["", "<b>Failure samples:</b>"]
        lines.extend(f"• <code>{cid}</code> [{kind}] {error}" for cid, kind, error in failures[:12])
    lines.append("\n<b>╰━━━━━━━━━━━━━━━━━━━━━━╯</b>")
    return "\n".join(lines)


@register("broad")
async def broad_command(message):
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    user_id = int((message.get("from") or {}).get("id") or 0)

    if user_id != int(ADMIN_USER_ID):
        await app.send_message(
            chat_id,
            "🚫 <b>This command is restricted to the bot owner only.</b>",
            parse_mode="HTML",
        )
        return

    reply = message.get("reply_to_message") or {}
    source_chat_id = int((reply.get("chat") or {}).get("id") or 0)
    source_message_id = int(reply.get("message_id") or 0)
    if not source_chat_id or not source_message_id:
        await app.send_message(
            chat_id,
            "⚠️ <b>Reply to the message you want to broadcast, then send /broad.</b>",
            parse_mode="HTML",
        )
        return

    # First use the persistent registry, but do not depend on it.
    try:
        db_targets = await get_broadcast_targets()
    except Exception as exc:
        print(f"[broad] DB target discovery failed; continuing with live dialogs: {exc!r}")
        db_targets = []

    # Then ask Telegram itself what chats are visible to the bot session.
    try:
        live_targets = await app.get_dialog_targets()
    except Exception as exc:
        print(f"[broad] Live Telegram dialog discovery failed; continuing with DB targets: {exc!r}")
        live_targets = []

    targets = _merge_targets(db_targets, live_targets)
    # Broadcast only to users and groups. A source chat is not re-forwarded
    # to itself because the owner is already replying to that message there.
    targets = [
        t for t in targets
        if _target_type(t) in {"user", "group"}
        and int(t["chat_id"]) != source_chat_id
    ]

    # Best-effort persistence of live discoveries. Broadcasting itself never
    # depends on this succeeding.
    for target in live_targets:
        try:
            await upsert_chat({
                "id": target.get("chat_id"),
                "type": "private" if target.get("target_type") == "user" else target.get("target_type"),
                "title": target.get("title"),
            })
        except Exception as exc:
            print(f"[broad] Non-fatal persistence failure for {target.get('chat_id')}: {exc!r}")

    progress = await app.send_message(
        chat_id,
        _dashboard(
            len(targets),
            {"sent": 0, "failed": 0},
            {"sent": 0, "failed": 0},
            {"sent": 0, "failed": 0},
            0, 0, 0, [], len(db_targets), len(live_targets),
        ),
        parse_mode="HTML",
    )
    progress_id = int(progress.get("message_id") or 0)

    counts = {
        "user": {"sent": 0, "failed": 0},
        "group": {"sent": 0, "failed": 0},
        "channel": {"sent": 0, "failed": 0},
    }
    sent = failed = skipped = 0
    failures: list[tuple[int, str, str]] = []

    for index, target in enumerate(targets, 1):
        target_chat_id = int(target["chat_id"])
        kind = _target_type(target)
        if kind not in counts:
            skipped += 1
            continue

        try:
            await app.forward_message(target_chat_id, source_chat_id, source_message_id)
            counts[kind]["sent"] += 1
            sent += 1
        except Exception as exc:
            counts[kind]["failed"] += 1
            failed += 1
            if len(failures) < 25:
                failures.append((target_chat_id, kind, str(exc).replace("\n", " ")[:180]))
            print(f"[broad] Forward failed chat_id={target_chat_id} type={kind}: {exc!r}")

        if progress_id and (index % 10 == 0 or index == len(targets)):
            body = _dashboard(
                len(targets),
                counts["user"],
                counts["group"],
                counts["channel"],
                sent, failed, skipped, failures, len(db_targets), len(live_targets),
            )
            try:
                await app.edit_message_text(chat_id, progress_id, body, parse_mode="HTML")
            except Exception as exc:
                print(f"[broad] Progress dashboard update failed: {exc!r}")

        # Keep enough spacing to reduce Telegram flood-wait risk without making
        # the broadcast unbearably slow. App.forward_message also retries native
        # FloodWait exceptions internally.
        await asyncio.sleep(0.08)

    final_body = _dashboard(
        len(targets), counts["user"], counts["group"], counts["channel"],
        sent, failed, skipped, failures, len(db_targets), len(live_targets),
    )
    if progress_id:
        try:
            await app.edit_message_text(chat_id, progress_id, final_body, parse_mode="HTML")
            return
        except Exception as exc:
            print(f"[broad] Final dashboard edit failed: {exc!r}")

    await app.send_message(chat_id, final_body, parse_mode="HTML")
