print("start_dm.py loaded")

import html

from handlers.registry import register
from app import app
from config import ADMIN_USER_ID, NOTIFICATION_GROUP_ID
from database.query import execute, fetchval
from utils.user_notification import format_user_notification

CRICKIUM_GROUP_URL = "https://t.me/CrickiumHub"
SUPPORT_URL = "https://t.me/CrickiumUpdates"


async def _notify_admin_of_failure(context: str, exc: Exception) -> None:
    """Mirrors main.py's helper of the same name - a failed send to
    NOTIFICATION_GROUP_ID (wrong ID, bot not a member there, no send
    permission, etc.) used to only print() to the server console. This
    puts the same error directly in the admin's DM so it's impossible
    to miss."""
    print(f"[start_dm] {context}: {exc!r}")
    try:
        await app.send_message(
            int(ADMIN_USER_ID),
            f"⚠️ <b>Notification failed</b>\n<b>Where:</b> {context}\n<b>Error:</b> <code>{exc}</code>",
            parse_mode="HTML",
        )
    except Exception as inner_exc:
        print(f"[start_dm] Could ALSO not reach admin DM to report the above failure: {inner_exc!r}")


def _start_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Add me to group",
                    "url": "https://t.me/CrickiumBot?startgroup=true",
                    "style": "success",
                }
            ],
            [
                {
                    "text": "𝗖𝗿𝗶𝗰𝗸𝗶𝘂𝗺 GC",
                    "url": CRICKIUM_GROUP_URL,
                    "style": "primary",
                },
                {
                    "text": "Support",
                    "url": SUPPORT_URL,
                    "style": "primary",
                },
            ],
        ]
    }


async def save_user(user_id, username, first_name):
    await execute(
        """
        INSERT INTO users (
            user_id,
            username,
            first_name,
            last_seen_at
        )
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_seen_at = NOW();
        """,
        user_id,
        username,
        first_name,
    )


def _is_private_chat(chat_type) -> bool:
    """
    main.py converts Pyrogram chat.type to a string.

    Depending on the Pyrogram/runtime representation it may arrive as:
        private
    or:
        ChatType.PRIVATE

    Normalize both forms so /start works reliably in bot DMs.
    """
    value = str(chat_type or "").strip().lower()

    if value.startswith("chattype."):
        value = value.split(".", 1)[1]

    return value == "private"


@register("start")
async def start_command(message):
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    chat_type = chat.get("type")

    user = message.get("from") or {}
    user_id = user.get("id")
    first_name = user.get("first_name") or "there"

    print(
        f"[start_dm] /start invoked "
        f"user_id={user_id}, chat_id={chat_id}, raw_chat_type={chat_type!r}"
    )

    # This handler is ONLY for the bot's private DM.
    # Group/channel /start is intentionally ignored here so another
    # group-specific handler can be added later without this one interfering.
    if not _is_private_chat(chat_type):
        print(
            f"[start_dm] Ignoring /start outside private DM "
            f"(normalized_chat_type={str(chat_type).lower()!r})"
        )
        return

    if not user_id or not chat_id:
        print("[start_dm] Missing user_id or chat_id; ignoring /start.")
        return

    # Same "only genuinely first time" pattern used for the group-added
    # notification: check BEFORE saving, so a returning user re-running
    # /start never re-fires the notification.
    already_known = await fetchval("SELECT 1 FROM users WHERE user_id = $1 LIMIT 1;", int(user_id))

    await save_user(
        user_id,
        user.get("username"),
        first_name,
    )

    if already_known is None:
        try:
            await app.send_message(
                NOTIFICATION_GROUP_ID,
                format_user_notification(
                    full_name=str(first_name),
                    username=user.get("username"),
                    user_id=int(user_id),
                ),
            )
        except Exception as exc:
            await _notify_admin_of_failure(f"user notification (user_id={user_id})", exc)

    mention = (
        f'<a href="tg://user?id={int(user_id)}">'
        f"{html.escape(str(first_name))}"
        f"</a>"
    )

    text = (
        "<b>╭━━━ SYSTEM ONLINE ━━━╮</b>\n"
        f"<blockquote>Welcome, {mention}! ⚡️</blockquote>\n"
        "<b>╰━━━━━━━━━━━━━━━━━╯</b>\n\n"
        "Greeting from 𝗖𝗿𝗶𝗰𝗸𝗶𝘂𝗺 — Your Ultimate Telegram Cricket Universe! "
        "We're super excited to have you onboard! 🚀\n\n"
        "<b>─── ✦ WHAT CAN YOU DO? ✦ ───</b>\n\n"
        "<blockquote>"
        "<b>┌── 🎴 COLLECT &amp; CLAIM</b>\n"
        "┊ Discover &amp; collect player cards from\n"
        "┊ <b>Bronze</b> to ultra-rare Legendary!\n"
        "└───────────────"
        "</blockquote>\n\n"
        "<blockquote>"
        "<b>┌── 🏏 STRATEGIZE &amp; PLAY</b>\n"
        "┊ Form your dream XI, analyze pitches,\n"
        "┊ and trigger real-time tactics!\n"
        "└───────────────"
        "</blockquote>\n\n"
        "<blockquote>"
        "<b>┌── ⚔️ LIVE SIMULATION</b>\n"
        "┊ Challenge friends to intense ball-by-ball\n"
        "┊ cricket showdowns!\n"
        "└───────────────"
        "</blockquote>\n\n"
        "<blockquote>"
        "<b>┌── 📈 MARKET &amp; BANK</b>\n"
        "┊ Buy, sell, and trade cards in a\n"
        "┊ dynamic player-driven economy!\n"
        "└───────────────"
        "</blockquote>\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "💥 <b>READY TO OWN THE ARENA?</b>\n"
        "Add <b>Crickium</b> to your groups and ignite the ultimate rivalry "
        "with your friends! 🎮\n\n"
        "👇 <b>Tap below to add me &amp; start the fun!</b>"
    )

    start_image_file_id = (
        "AgACAgUAAyEGAATr-CecAAMKan__4EUULCuvsmccoD8z7vrvtOcAAlEZaxuMpvlXZ5UMHO7u71sACAEAAwIAA3kABx4E"
    )

    keyboard = _start_keyboard()

    try:
        if not start_image_file_id:
            raise ValueError("no start_image_file_id set")

        await app.send_photo(
            chat_id,
            photo=start_image_file_id,
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        print(f"[start_dm] Welcome image sent successfully to user_id={user_id}")

    except Exception as exc:
        # If the image file_id is stale/invalid, /start must still work.
        print(
            f"[start_dm] send_photo failed ({exc!r}), "
            "falling back to text-only welcome message."
        )

        await app.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        print(f"[start_dm] Text welcome sent successfully to user_id={user_id}")