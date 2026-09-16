from __future__ import annotations

from urllib.parse import quote_plus

from app import app
from handlers.registry import register
from services.referrals import (
    build_referral_message,
    get_my_referrals,
    build_my_referrals_message,
    share_url,
)


def _chat_is_private(message) -> bool:
    chat = message.get("chat") or {}
    value = str(chat.get("type") or "").strip().lower()
    # Handle strings such as "private", "chattype.private",
    # and enum-style representations from different Kurigram builds.
    return value.rsplit(".", 1)[-1] == "private"


def _user_id(message) -> int:
    user = message.get("from") or {}
    try:
        return int(user.get("id") or 0)
    except (TypeError, ValueError):
        return 0


async def _send_referral_message(chat_id: int, referral_url: str, first_name: str) -> None:
    """Send the referral card.

    The primary-blue style is attempted first. If the installed Telegram
    client/API combination rejects the newer button style or HTML formatting,
    fall back to a standard inline URL button so /refer never becomes silent.
    """
    text = build_referral_message(referral_url)
    styled_keyboard = {
        "inline_keyboard": [[
            {
                "text": "📤 Share referral link",
                "url": share_url(referral_url, first_name),
                "style": "primary",
            }
        ]]
    }
    plain_keyboard = {
        "inline_keyboard": [[
            {
                "text": "📤 Share referral link",
                "url": share_url(referral_url, first_name),
            }
        ]]
    }

    try:
        await app.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=styled_keyboard,
            disable_web_page_preview=True,
        )
        return
    except Exception as exc:
        print(f"[refer] Styled referral message failed, retrying without button style: {exc!r}")

    try:
        await app.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=plain_keyboard,
            disable_web_page_preview=True,
        )
        return
    except Exception as exc:
        print(f"[refer] HTML referral message failed, retrying with plain text: {exc!r}")

    # Last-resort path. This is deliberately plain text so a Telegram HTML
    # parser/version difference cannot make the command appear dead.
    plain_text = (
        "🎁 REFERRAL REWARDS\n\n"
        "How to earn:\n"
        "1. Friend opens your referral link and presses Start.\n"
        "2. Completes /debut.\n"
        "3. Completes at least one /claim.\n"
        "4. Completes at least one regular game. Super Over does not count.\n\n"
        f"Your referral link:\n{referral_url}\n\n"
        "Tap the button below to share it."
    )
    await app.send_message(
        chat_id,
        plain_text,
        reply_markup=plain_keyboard,
        disable_web_page_preview=True,
    )


@register("refer")
@register("referral")
async def refer_command(message):
    if not _chat_is_private(message):
        return

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    user = message.get("from") or {}
    user_id = _user_id(message)
    if not chat_id or not user_id:
        return

    first_name = str(user.get("first_name") or "User")

    try:
        me = await app.get_me()
        username = (
            me.get("username")
            if isinstance(me, dict)
            else getattr(me, "username", None)
        )
        username = str(username or "").lstrip("@").strip()
    except Exception as exc:
        print(f"[refer] get_me failed: {exc!r}")
        await app.send_message(
            chat_id,
            "⚠️ I could not generate the referral link right now. Please try /refer again.",
        )
        return

    if not username:
        await app.send_message(
            chat_id,
            "⚠️ Referral links are temporarily unavailable.",
        )
        return

    referral_url = f"https://t.me/{username}?start=ref_{user_id}"
    await _send_referral_message(chat_id, referral_url, first_name)


@register("myreferrals")
@register("myreferees")
@register("myreferral")
async def my_referrals_command(message):
    if not _chat_is_private(message):
        return

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    user_id = _user_id(message)
    if not chat_id or not user_id:
        return

    try:
        rows = await get_my_referrals(user_id)

        # Refresh incomplete records so the display reflects a newly finished
        # debut/claim/game without requiring another referral-link click.
        if rows:
            from services.referrals import refresh_referral_progress
            for row in rows:
                if row.get("status") != "completed":
                    try:
                        await refresh_referral_progress(int(row["referred_id"]))
                    except Exception as exc:
                        print(
                            f"[myreferrals] Could not refresh referral "
                            f"{row.get('referred_id')}: {exc!r}"
                        )
            rows = await get_my_referrals(user_id)

        await app.send_message(
            chat_id,
            build_my_referrals_message(rows),
            parse_mode="HTML",
        )
    except Exception as exc:
        print(f"[myreferrals] command failed for user_id={user_id}: {exc!r}")
        # Never leave the DM silent if the referral table/database is
        # temporarily unavailable.
        await app.send_message(
            chat_id,
            "⚠️ My Referrals is temporarily unavailable. Please try again in a moment.",
        )
