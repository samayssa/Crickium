print("miniapp.py loaded")

from handlers.registry import register
from app import app
from utils.miniapp_url import get_launch_keyboard, resolve_miniapp_url


def _miniapp_keyboard(chat_type: str | None = None) -> dict | None:
    if chat_type != "private":
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
