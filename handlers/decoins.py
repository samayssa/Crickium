from __future__ import annotations

import re

from handlers.registry import register
from config import ADMIN_USER_ID
from database.query import fetchrow
from utils.admin_command_utils import (
    is_bot_owner,
    owner_private_response,
    resolve_user_reference,
    user_label,
)

_MAX_BIGINT = 9_223_372_036_854_775_807


def _parse_amount(token: str) -> int | None:
    value = str(token or "").replace(",", "").strip()
    if not re.fullmatch(r"[0-9]+", value):
        return None
    amount = int(value)
    return amount if 0 < amount <= _MAX_BIGINT else None


def _parse_target_and_amount(args: list[str], reply_user: dict | None):
    tokens = [token.strip() for token in args if token.strip()]
    target_token = None
    amount_token = None

    username_tokens = [token for token in tokens if token.startswith("@")]
    numeric_tokens = [token for token in tokens if re.fullmatch(r"[0-9][0-9,]*", token)]

    if username_tokens:
        if len(tokens) != 2 or len(username_tokens) != 1 or len(numeric_tokens) != 1:
            return None, None
        target_token, amount_token = username_tokens[0], numeric_tokens[0]
    elif reply_user and reply_user.get("id"):
        if len(tokens) == 1 and len(numeric_tokens) == 1:
            # Replying to a user's message: /decoins 500
            target_token, amount_token = None, numeric_tokens[0]
        elif len(tokens) == 2 and len(numeric_tokens) == 2:
            # Explicit ID + amount is also accepted even when replying.
            target_token, amount_token = numeric_tokens[0], numeric_tokens[1]
        else:
            return None, None
    else:
        if len(tokens) != 2 or len(numeric_tokens) != 2:
            return None, None
        target_token, amount_token = numeric_tokens[0], numeric_tokens[1]

    amount = _parse_amount(amount_token)
    if amount is None:
        return None, None
    return target_token, amount


@register("decoins")
async def decoins_command(message):
    """Owner-only, silent coin deduction.

    /decoins USER_ID AMOUNT
    /decoins @username AMOUNT
    Reply to a user's message with /decoins AMOUNT
    """
    if not is_bot_owner(message):
        return

    parts = str(message.get("text") or "").split()
    args = parts[1:]
    reply_user = (message.get("reply_to_message") or {}).get("from") or {}
    if reply_user.get("is_bot"):
        await owner_private_response(message, "⚠️ Coins cannot be deducted from a bot account.")
        return

    target_ref, amount = _parse_target_and_amount(args, reply_user)
    if amount is None:
        await owner_private_response(
            message,
            "🪙 DECOINS COMMAND\n\n"
            "By ID: /decoins USER_ID AMOUNT\n"
            "By username: /decoins @username AMOUNT\n"
            "By reply: reply to the user's message with /decoins AMOUNT\n\n"
            "Only a positive whole-number amount is accepted.",
        )
        return

    target = None
    if target_ref is not None:
        target = await resolve_user_reference(target_ref, allow_unknown_id=False)
    elif reply_user.get("id"):
        target = await resolve_user_reference(str(int(reply_user["id"])), allow_unknown_id=False)
        if target is not None:
            target["username"] = target.get("username") or reply_user.get("username")
            target["first_name"] = target.get("first_name") or reply_user.get("first_name")

    if target is None:
        label = target_ref or (f"user ID {reply_user.get('id')}" if reply_user.get("id") else "the supplied user")
        await owner_private_response(
            message,
            f"⚠️ USER NOT FOUND\n\nCould not find {label} in the Crickium users database. No coins were changed.",
        )
        return

    target_id = int(target["user_id"])
    if target_id == int(ADMIN_USER_ID):
        await owner_private_response(message, "⚠️ Owner balance is protected from /decoins.")
        return

    # Conditional UPDATE makes the deduction atomic and prevents negative balances.
    updated = await fetchrow(
        """UPDATE users
           SET balance = COALESCE(balance, 0) - $1
           WHERE user_id = $2 AND COALESCE(balance, 0) >= $1
           RETURNING balance;""",
        int(amount), target_id,
    )
    if updated is None:
        current = await fetchrow(
            "SELECT COALESCE(balance, 0) AS balance FROM users WHERE user_id = $1;",
            target_id,
        )
        if current is None:
            await owner_private_response(message, "⚠️ User record disappeared before deduction. No coins were changed.")
        else:
            balance = int(current["balance"] or 0)
            await owner_private_response(
                message,
                f"⚠️ INSUFFICIENT COINS\n\n{user_label(target)}\n"
                f"Requested deduction: {amount:,}\nAvailable balance: {balance:,}\n\nNo coins were changed.",
            )
        return

    new_balance = int(updated["balance"] or 0)
    await owner_private_response(
        message,
        "✅ COINS DEDUCTED\n\n"
        f"User: {user_label(target)}\n"
        f"Deducted: {amount:,} coins\n"
        f"New balance: {new_balance:,} coins\n\n"
        "No message or notification was sent to the affected user.",
    )
