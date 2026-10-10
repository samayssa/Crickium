print("miniapp.py loaded")

from handlers.registry import register
from app import app
from utils.miniapp_url import get_launch_keyboard, resolve_miniapp_url


def _normalize_chat_type(value) -> str:
    """Accept both ``private`` and enum-style ``ChatType.PRIVATE`` values."""
    enum_name = getattr(value, "name", None)
    enum_value = getattr(value, "value", None)
    candidate = enum_name or enum_value or value or ""
    normalized = str(candidate).strip().lower()
    if "." in normalized:
        normalized = normalized.rsplit(".", 1)[-1]
    return normalized


def _miniapp_keyboard(chat_type: str | None = None) -> dict | None:
    if _normalize_chat_type(chat_type) != "private":
        return None

    return get_launch_keyboard(resolve_miniapp_url())


@register("app")
@register("miniapp")
async def app_command(message):
    chat = message.get("chat", {})
    chat_id = chat["id"]
    chat_type = chat.get("type")
    from_user = message.get("from", {})
    first_name = from_user.get("first_name", "player")

    keyboard = _miniapp_keyboard(chat_type)
    if keyboard is None:
        await app.send_message(
            chat_id,
            "🎮 Please open Crickium in a private chat to launch the app.",
        )
    else:
        await app.send_message(
            chat_id,
            f"🎮 Hi {first_name}! Open the Crickium app from here.",
            reply_markup=keyboard,
        )
