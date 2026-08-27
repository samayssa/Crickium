from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any

from pyrogram import Client, idle
from pyrogram.errors import FloodWait
from pyrogram.handlers import CallbackQueryHandler, ChatMemberUpdatedHandler, MessageHandler

from config import API_HASH, API_ID, BOT_TOKEN
from app import app
import handlers  # noqa: F401 - import side effects register commands/callbacks
from handlers.registry import COMMANDS, CALLBACKS
from config import ADMIN_USER_ID, NOTIFICATION_GROUP_ID
from database.query import fetchval
from utils.group_notification import format_group_notification
from utils.group_added_response import format_group_added_response
from database.connection import connect, disconnect
from database.broadcast_repo import upsert_chat
from database.runtime_repo import clear_bot_session, get_bot_session, save_bot_session
from database.migrate import migrate
from engines.probability_engine import reload_probability_profile_cache

MAX_CONCURRENT_UPDATES = 12
MAX_PENDING_UPDATES = 100


def parse_command(text: str) -> str | None:
    if not text or not text.startswith("/"):
        return None
    first_word = text.split()[0]
    command = first_word[1:].split("@")[0]
    return command.lower()


def _pyro_user_to_dict(user: Any | None) -> dict:
    if user is None:
        return {}
    return {
        "id": int(getattr(user, "id", 0) or 0),
        "is_bot": bool(getattr(user, "is_bot", False)),
        "first_name": getattr(user, "first_name", None),
        "last_name": getattr(user, "last_name", None),
        "username": getattr(user, "username", None),
        "language_code": getattr(user, "language_code", None),
    }


def _pyro_chat_to_dict(chat: Any | None) -> dict:
    if chat is None:
        return {}
    return {
        "id": int(getattr(chat, "id", 0) or 0),
        "type": str(getattr(chat, "type", "")),
        "title": getattr(chat, "title", None),
        "first_name": getattr(chat, "first_name", None),
        "username": getattr(chat, "username", None),
    }


def _pyro_photo_to_dict(photo: Any | None) -> dict | None:
    if photo is None:
        return None
    return {
        "file_id": getattr(photo, "file_id", None),
        "file_unique_id": getattr(photo, "file_unique_id", None),
        "width": getattr(photo, "width", None),
        "height": getattr(photo, "height", None),
        "file_size": getattr(photo, "file_size", None),
    }


def _pyro_document_to_dict(document: Any | None) -> dict | None:
    if document is None:
        return None
    return {
        "file_id": getattr(document, "file_id", None),
        "file_unique_id": getattr(document, "file_unique_id", None),
        "file_name": getattr(document, "file_name", None),
        "mime_type": getattr(document, "mime_type", None),
        "file_size": getattr(document, "file_size", None),
    }


def _pyro_message_to_dict(message: Any | None) -> dict:
    if message is None:
        return {}
    data = {
        "message_id": int(getattr(message, "id", 0) or 0),
        "date": int(getattr(message, "date", 0).timestamp()) if getattr(message, "date", None) else None,
        "chat": _pyro_chat_to_dict(getattr(message, "chat", None)),
        "from": _pyro_user_to_dict(getattr(message, "from_user", None)),
        "text": getattr(message, "text", None) or getattr(message, "caption", None) or "",
        "photo": _pyro_photo_to_dict(getattr(message, "photo", None)),
        "document": _pyro_document_to_dict(getattr(message, "document", None)),
        "new_chat_members": [_pyro_user_to_dict(u) for u in (getattr(message, "new_chat_members", None) or [])],
        "entities": [],
    }
    reply = getattr(message, "reply_to_message", None)
    if reply is not None:
        data["reply_to_message"] = _pyro_message_to_dict(reply)
    return data


def _pyro_callback_to_dict(callback_query: Any) -> dict:
    payload = {
        "id": getattr(callback_query, "id", None),
        "data": getattr(callback_query, "data", None) or "",
        "from": _pyro_user_to_dict(getattr(callback_query, "from_user", None)),
        "message": _pyro_message_to_dict(getattr(callback_query, "message", None)),
    }
    return payload


# In-process guard so the SAME physical "bot added to group" event never
# fires the thank-you/notification twice, even though Telegram can report
# it to us through two different update types at once - a regular message
# with new_chat_members (which some groups suppress entirely via their
# "hide service messages" setting), and the my_chat_member status update
# (which Telegram guarantees fires regardless of that setting - the
# ChatMemberUpdatedHandler below is the reliable one; new_chat_members is
# kept only as a fallback for older Pyrogram/Telegram edge cases).
_RECENTLY_HANDLED_GROUP_ADDS: dict[int, float] = {}
_DEDUP_WINDOW_SECONDS = 15.0


async def _notify_admin_of_failure(context: str, exc: Exception) -> None:
    """Failures inside these notification flows used to only print() to
    the server console, which is easy to miss. This puts the same error
    directly in the admin's DM so a broken NOTIFICATION_GROUP_ID (wrong
    ID, bot not a member there, no send permission, etc.) is impossible
    to miss."""
    print(f"[main.py] {context}: {exc!r}")
    try:
        await app.send_message(
            int(ADMIN_USER_ID),
            f"⚠️ <b>Notification failed</b>\n<b>Where:</b> {context}\n<b>Error:</b> <code>{exc}</code>",
            parse_mode="HTML",
        )
    except Exception as inner_exc:
        # If even the admin DM fails, the admin has never started the
        # bot's DM, or ADMIN_USER_ID itself is wrong - print is all
        # that's left.
        print(f"[main.py] Could ALSO not reach admin DM to report the above failure: {inner_exc!r}")


async def _handle_bot_added_to_group(group_id: int, group_name: str, group_username: str | None, actor: dict) -> None:
    now = time.monotonic()
    last_seen = _RECENTLY_HANDLED_GROUP_ADDS.get(group_id)
    if last_seen is not None and (now - last_seen) < _DEDUP_WINDOW_SECONDS:
        print(f"[main.py] Duplicate bot-added-to-group event for group_id={group_id} ignored (already handled {now - last_seen:.1f}s ago).")
        return
    _RECENTLY_HANDLED_GROUP_ADDS[group_id] = now

    print(f"[main.py] Bot added to group_id={group_id} ({group_name!r}) by user_id={actor.get('id')}")

    already_known = await fetchval("SELECT 1 FROM broadcast_targets WHERE chat_id = $1 LIMIT 1;", group_id)
    group_link = f"https://t.me/{group_username}" if group_username else "Not available"

    try:
        await app.send_message(
            group_id,
            format_group_added_response(username=actor.get("username"), group_name=group_name),
        )
    except Exception as exc:
        await _notify_admin_of_failure(f"group-added thank-you message (group_id={group_id})", exc)

    if already_known is None:
        try:
            await app.send_message(
                NOTIFICATION_GROUP_ID,
                format_group_notification(
                    group_name=group_name,
                    group_link=group_link,
                    username=actor.get("username"),
                    user_id=int(actor.get("id", 0) or 0),
                    group_id=group_id,
                ),
            )
        except Exception as exc:
            await _notify_admin_of_failure(f"group notification to NOTIFICATION_GROUP_ID (new group_id={group_id})", exc)

    try:
        await upsert_chat({"id": group_id, "type": "group", "title": group_name})
    except Exception as exc:
        print(f"[main.py] Non-fatal broadcast target registration failure for group_id={group_id}: {exc!r}")


async def handle_my_chat_member(_, chat_member_updated):
    """Telegram's reliable 'the bot's own membership status changed'
    update - fires even in groups that hide 'user joined' service
    messages, unlike the new_chat_members path below."""
    try:
        new_status = getattr(getattr(chat_member_updated, "new_chat_member", None), "status", None)
        old_status = getattr(getattr(chat_member_updated, "old_chat_member", None), "status", None)
        status_text = str(new_status).lower()
        was_member_before = str(old_status).lower() in {"member", "administrator", "owner", "chatmemberstatus.member", "chatmemberstatus.administrator", "chatmemberstatus.owner"}

        if was_member_before or ("member" not in status_text and "administrator" not in status_text and "owner" not in status_text):
            return  # Not a "bot freshly added" transition (e.g. it's a promotion, or the bot left/was kicked).

        chat = getattr(chat_member_updated, "chat", None)
        if chat is None or str(getattr(chat, "type", "")).lower() not in {"chattype.group", "chattype.supergroup", "group", "supergroup"}:
            return

        # ChatMemberUpdated is emitted for ordinary user joins/promotions too.
        # Only handle the event when the member whose status changed is the
        # actual Crickium bot account.
        changed_member = getattr(chat_member_updated, "new_chat_member", None)
        changed_user = getattr(changed_member, "user", None)
        me = await app.get_me()
        if not changed_user or int(getattr(changed_user, "id", 0) or 0) != int(me.get("id", 0) or 0):
            return

        actor_user = getattr(chat_member_updated, "from_user", None)
        actor = _pyro_user_to_dict(actor_user)
        group_id = int(getattr(chat, "id", 0) or 0)
        group_name = getattr(chat, "title", None) or "this group"
        group_username = getattr(chat, "username", None)

        await _handle_bot_added_to_group(group_id, group_name, group_username, actor)
    except Exception as exc:
        await _notify_admin_of_failure("handle_my_chat_member", exc)


async def handle_message(_, message):
    payload = _pyro_message_to_dict(message)
    text = payload.get("text", "")
    chat = payload.get("chat", {})

    print(f"[main.py] Message from chat_id={chat.get('id')} type={chat.get('type')} text={text!r}")

    # Fallback path: some Telegram/Pyrogram versions still deliver "bot
    # added" as a regular message with new_chat_members. The dedup guard
    # in _handle_bot_added_to_group makes it safe for this to run
    # alongside handle_my_chat_member above without double-sending.
    new_members = payload.get("new_chat_members") or []
    if new_members and chat.get("type") in {"group", "supergroup"}:
        try:
            me = await app.get_me()
            bot_was_added = any(int(member.get("id", 0) or 0) == int(me.get("id", 0) or 0) for member in new_members)
            if bot_was_added:
                await _handle_bot_added_to_group(
                    int(chat.get("id")),
                    chat.get("title") or "this group",
                    chat.get("username"),
                    payload.get("from") or {},
                )
        except Exception as exc:
            print(f"[main.py] Group-add detection (new_chat_members path) failed: {exc!r}")

    try:
        await upsert_chat(chat)
    except Exception as exc:
        print(f"[main.py] Non-fatal broadcast target registration failure: {exc!r}")

    command = parse_command(text)
    if command is None:
        print("[main.py] Not a command, ignoring.")
        return

    handler = COMMANDS.get(command)
    if handler is None:
        print(f"[main.py] No handler registered for command '/{command}'.")
        try:
            await app.send_message(
                chat.get("id"),
                f"⚠️ Unknown command: /{command}\nTry /start, /app, /debut, /team, /pxl, /match, or /mybank.",
            )
        except Exception:
            traceback.print_exc()
        return

    print(f"[main.py] Dispatching to handler for '/{command}'...")
    try:
        await handler(payload)
    except Exception:
        print(f"[main.py] !! Handler for '/{command}' raised an exception:")
        traceback.print_exc()


async def handle_callback_query(_, callback_query):
    payload = _pyro_callback_to_dict(callback_query)
    data = payload.get("data", "")
    action = data.split(":")[0] if data else ""
    presser = payload.get("from", {})

    print(f"[main.py] Callback query from user_id={presser.get('id')} username=@{presser.get('username')} data={data!r}")

    handler = CALLBACKS.get(action)
    if handler is None:
        print(f"[main.py] No callback handler registered for action '{action}'.")
        try:
            await app.answer_callback_query(payload["id"], "This action is unavailable.", show_alert=True)
        except Exception:
            traceback.print_exc()
        return

    print(f"[main.py] Dispatching callback to handler for action '{action}'...")
    try:
        await handler(payload)
    except Exception:
        print(f"[main.py] !! Callback handler for '{action}' raised an exception:")
        traceback.print_exc()


async def _build_client(session_string: str | None = None) -> Client:
    """Build the single Telegram client.

    Railway deployments are stateless between releases.  When a previously
    exported session string exists in PostgreSQL, reuse it so Telegram does
    not have to authorize the bot again on every deployment.  The first boot
    still uses the normal bot-token authorization path and stores the session
    for subsequent boots.
    """
    kwargs = {
        "api_id": API_ID,
        "api_hash": API_HASH,
    }
    if session_string:
        kwargs.update({"session_string": session_string, "in_memory": True})
    else:
        kwargs.update({"bot_token": BOT_TOKEN, "in_memory": False})
    return Client("cricket_bot", **kwargs)


async def _start_client_with_persistent_session() -> Client:
    stored_session: str | None = None
    try:
        stored_session = await get_bot_session()
        if stored_session:
            print("[main.py] Reusing persisted Telegram authorization session.")
    except Exception as exc:
        print(f"[main.py] Could not read persisted Telegram session; falling back to bot-token authorization: {exc!r}")

    # Prefer the persisted session. If it is invalid/revoked, remove it once
    # and fall back to a fresh bot-token authorization.
    if stored_session:
        client = await _build_client(stored_session)
        try:
            await client.start()
            return client
        except Exception as exc:
            text = str(exc).lower()
            invalid_session_markers = (
                "auth key unregistered",
                "session revoked",
                "session expired",
                "unauthorized",
                "not authorized",
                "authorization key",
                "auth key",
            )
            if any(marker in text for marker in invalid_session_markers):
                print("[main.py] Persisted Telegram session is invalid/revoked; clearing it and authorizing once with BOT_TOKEN.")
                try:
                    await client.stop()
                except Exception:
                    pass
                try:
                    await clear_bot_session()
                except Exception as clear_exc:
                    print(f"[main.py] Could not clear invalid persisted session: {clear_exc!r}")
            else:
                raise

    # Fresh authorization. Telegram may temporarily rate-limit repeated bot
    # authorizations with FLOOD_WAIT.  Wait for Telegram's exact server value
    # instead of crashing Railway into a restart loop.
    while True:
        client = await _build_client(None)
        try:
            await client.start()
            break
        except FloodWait as exc:
            wait_for = int(getattr(exc, "value", None) or getattr(exc, "x", 0) or 0)
            wait_for = max(wait_for, 1)
            print(f"[main.py] Telegram authorization is rate-limited (FLOOD_WAIT_{wait_for}). Waiting before retrying instead of crashing the service.")
            try:
                await client.stop()
            except Exception:
                pass
            await asyncio.sleep(wait_for + 1)

    # Export and persist the session so later Railway restarts/deployments do
    # not trigger another Telegram bot authorization attempt.
    try:
        exporter = getattr(client, "export_session_string", None)
        if exporter is not None:
            exported = exporter()
            if hasattr(exported, "__await__"):
                exported = await exported
            if exported:
                try:
                    await save_bot_session(str(exported))
                    print("[main.py] Telegram authorization session persisted successfully.")
                except Exception as exc:
                    print(f"[main.py] Could not persist Telegram session; bot will still run: {exc!r}")
    except Exception as exc:
        print(f"[main.py] Session export unavailable/failed; bot will still run: {exc!r}")

    return client


async def main():
    app_client = None
    try:
        print("Connecting Database...")
        await connect()

        print("Running Database Migration...")
        await migrate()
        await reload_probability_profile_cache()
        print("[main.py] Probability profile cache loaded.")
    except Exception:
        print("!! Database setup failed. Bot will still start, but DB-dependent features won't work until this is fixed. !!")
        traceback.print_exc()

    app_client = await _start_client_with_persistent_session()
    app.bind_client(app_client)
    me = await app.get_me()
    print(f"[main.py] Logged in as: id={me['id']} username=@{me.get('username')} is_bot={me.get('is_bot')}")

    # Pyrogram (unlike the raw Bot API) can only address a chat by a bare
    # numeric ID if it already knows that chat's access_hash. For a channel
    # where the bot was added as admin but has never received a message/
    # update from it, ID-based sends can fail. This warm-up is intentionally
    # skipped here because it can create an unnecessary network burst on
    # startup; normal event traffic populates peers as they are encountered.
    print("[main.py] Skipping get_dialogs() peer cache warm-up.")

    print(f"[main.py] Registered commands: {list(COMMANDS.keys())}")
    print(f"[main.py] Registered callback actions: {list(CALLBACKS.keys())}")

    app_client.add_handler(MessageHandler(handle_message))
    app_client.add_handler(CallbackQueryHandler(handle_callback_query))
    app_client.add_handler(ChatMemberUpdatedHandler(handle_my_chat_member))

    print("[main.py] Entering Pyrogram event loop. Waiting for updates...")
    try:
        await idle()
    finally:
        await app_client.stop()
        try:
            await disconnect()
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[main.py] Stopped by user.")
