from __future__ import annotations

print("access.py loaded")

from handlers.registry import register
from config import ADMIN_USER_ID
from database.access_repo import (
    grant_upload_access,
    has_upload_access,
    list_upload_access,
    revoke_upload_access,
)
from utils.admin_command_utils import (
    is_bot_owner,
    owner_private_response,
    resolve_user_reference,
    user_label,
)


@register("access")
async def access_command(message):
    """Owner-only upload permission manager.

    /access @username or /access USER_ID                 -> grant
    /access @username remove (also accepts revoke)       -> remove
    /access all (also accepts list)                      -> list all grants
    Reply with /access remove to revoke the replied user.
    """
    if not is_bot_owner(message):
        return

    parts = (str(message.get("text") or "").split())
    args = parts[1:]
    lowered = {part.lower() for part in args}

    if lowered.intersection({"all", "list"}):
        rows = await list_upload_access()
        if not rows:
            await owner_private_response(message, "🔐 UPLOAD ACCESS LIST\n\nNo users currently have granted access.")
            return

        lines = [f"🔐 UPLOAD ACCESS LIST — {len(rows)} user(s)"]
        for index, row in enumerate(rows, start=1):
            user = {
                "user_id": row.get("user_id"),
                "username": row.get("username"),
                "first_name": row.get("first_name"),
            }
            granted_at = row.get("granted_at")
            if hasattr(granted_at, "strftime"):
                granted_at = granted_at.strftime("%Y-%m-%d %H:%M")
            lines.append(f"{index}. {user_label(user)} | Granted: {granted_at or 'Unknown'}")

        # Keep each outgoing message comfortably below Telegram's message limit.
        chunks: list[str] = []
        current = lines[0]
        for line in lines[1:]:
            if len(current) + len(line) + 1 > 3500:
                chunks.append(current)
                current = "🔐 UPLOAD ACCESS LIST (continued)\n" + line
            else:
                current += "\n" + line
        if current:
            chunks.append(current)
        await owner_private_response(message, chunks)
        return

    remove = bool(lowered.intersection({"remove", "revoke"}))
    # Ignore action words wherever the owner places them in the command.
    target_tokens = [
        token for token in args
        if token.lower() not in {"remove", "revoke", "add", "grant", "all", "list"}
    ]

    reply_user = (message.get("reply_to_message") or {}).get("from") or {}
    target: dict | None = None
    if reply_user and reply_user.get("id") and not reply_user.get("is_bot"):
        target_id = int(reply_user["id"])
        target = await resolve_user_reference(str(target_id), allow_unknown_id=True)
        if target is not None:
            target["username"] = target.get("username") or reply_user.get("username")
            target["first_name"] = target.get("first_name") or reply_user.get("first_name")

    if target_tokens:
        # The first reference token wins; action words may appear before or after it.
        target = await resolve_user_reference(target_tokens[0], allow_unknown_id=True)
        if target is None:
            token = target_tokens[0]
            if token.startswith("@"):
                result = (
                    f"⚠️ Cannot resolve {token}. The user must have interacted with the bot, "
                    "or you must reply to one of their group messages."
                )
            else:
                result = "⚠️ Invalid user. Use /access @username, /access USER_ID, or reply to their message."
            await owner_private_response(message, result)
            return

    if target is None:
        await owner_private_response(
            message,
            "🔐 ACCESS COMMAND\n\n"
            "Grant: /access @username or /access USER_ID\n"
            "Remove: /access @username remove or /access USER_ID remove\n"
            "Reply: /access remove\n"
            "List: /access all",
        )
        return

    target_id = int(target["user_id"])
    if target_id == int(ADMIN_USER_ID):
        await owner_private_response(message, "ℹ️ The owner always has access; this permission cannot be changed.")
        return

    label = user_label(target)
    if remove:
        removed = await revoke_upload_access(target_id)
        if removed:
            result = f"✅ ACCESS REMOVED\n\n{label}\nUpload access has been revoked."
        else:
            result = f"ℹ️ NO ACCESS CHANGE\n\n{label}\nThis user did not have granted upload access."
        await owner_private_response(message, result)
        return

    if await has_upload_access(target_id):
        await owner_private_response(message, f"ℹ️ ALREADY AUTHORIZED\n\n{label}\nThis user already has upload access.")
        return

    granted = await grant_upload_access(target_id, granted_by=int((message.get("from") or {}).get("id") or 0))
    if granted:
        result = f"✅ ACCESS GRANTED\n\n{label}\nThe user can now use the authorized player-upload commands."
    else:
        result = f"ℹ️ ALREADY AUTHORIZED\n\n{label}\nThis user already has upload access."
    await owner_private_response(message, result)
