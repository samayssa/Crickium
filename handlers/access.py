print("access.py loaded")

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID
from database.query import fetchrow
from database.access_repo import grant_upload_access, has_upload_access
from utils.mentions import mention


def _parse_target_arg(text: str) -> str | None:
    parts = text.split()
    for part in parts[1:]:
        if part:
            return part
    return None


@register("access")
async def access_command(message):
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_id = from_user.get("id")

    print(f"[access] /access invoked by user_id={user_id}")

    # ---- Owner-only check (bot owner, not group admin) ----
    if user_id != ADMIN_USER_ID:
        print(f"[access] REJECTED: user_id={user_id} is not the owner (ADMIN_USER_ID={ADMIN_USER_ID}).")
        await app.send_message(chat_id, "*🚫 This command is restricted to the bot owner only.*", parse_mode="Markdown")
        return

    reply_to = message.get("reply_to_message")
    target_id = None
    target_username = None
    target_name = None

    if reply_to and "from" in reply_to:
        target = reply_to["from"]
        if target.get("is_bot"):
            await app.send_message(chat_id, "*🚫 You can't grant access to a bot.*", parse_mode="Markdown")
            return
        target_id = target.get("id")
        target_username = target.get("username")
        target_name = target.get("first_name", "User")
        print(f"[access] Target resolved via reply: id={target_id} username=@{target_username}")
    else:
        arg = _parse_target_arg(message.get("text", ""))
        if not arg:
            await app.send_message(
                chat_id,
                "*⚠️ To grant /upload_pl and /upload_prob access, either:*\n"
                "• Reply to their message with /access, or\n"
                "• Use /access @username, or\n"
                "• Use /access <user_id>",
                parse_mode="Markdown",
            )
            return

        if arg.startswith("@"):
            username = arg[1:]
            row = await fetchrow("SELECT user_id, first_name FROM users WHERE username = $1;", username)
            if not row:
                await app.send_message(
                    chat_id,
                    f"*⚠️ @{username} hasn't interacted with this bot yet, so I can't resolve their user ID.*\n"
                    f"Ask them to send any command to the bot first, or reply to one of their messages with /access instead.",
                    parse_mode="Markdown",
                )
                return
            target_id = row["user_id"]
            target_username = username
            target_name = row["first_name"]
            print(f"[access] Target resolved via username lookup in DB: id={target_id}")
        elif arg.lstrip("-").isdigit():
            target_id = int(arg)
            row = await fetchrow("SELECT username, first_name FROM users WHERE user_id = $1;", target_id)
            if row:
                target_username = row["username"]
                target_name = row["first_name"]
            print(f"[access] Target resolved via raw user_id: id={target_id}")
        else:
            await app.send_message(
                chat_id,
                "*⚠️ Couldn't understand that. Use /access @username, /access <user_id>, or reply to their message.*",
                parse_mode="Markdown",
            )
            return

    if target_id == ADMIN_USER_ID:
        await app.send_message(chat_id, "*ℹ️ You're already the owner - you always have access.*", parse_mode="Markdown")
        return

    target_display = mention(target_id, target_username, target_name)

    if await has_upload_access(target_id):
        await app.send_message(
            chat_id,
            f"*ℹ️ {target_display} already has access to /upload_pl and /upload_prob.*",
            parse_mode="Markdown",
        )
        return

    await grant_upload_access(target_id, granted_by=user_id)
    await app.send_message(
        chat_id,
        f"*✅ Access Granted*\n\n"
        f"{target_display} can now use /upload_pl and /upload_prob.",
        parse_mode="Markdown",
    )
    print(f"[access] Granted upload access to user_id={target_id}, granted_by={user_id}")
