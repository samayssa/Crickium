print("achievements.py loaded")

from handlers.registry import register
from app import app
from utils.miniapp_url import get_launch_keyboard, resolve_miniapp_url


def _bar(pct: int, width: int = 10) -> str:
    filled = max(0, min(width, round(width * pct / 100)))
    return "▰" * filled + "▱" * (width - filled)


@register("achievements")
@register("achievement")
async def achievements_command(message):
    chat = message["chat"]
    user = message.get("from", {})
    uid = int(user.get("id") or 0)
    from services import achievements
    data = await achievements.build_response(uid)

    lines = [
        "<b>╭━━〔 🏅 ACHIEVEMENTS 〕━━╮</b>",
        "",
        f"<blockquote><b>🎖 Rank ➤ {data['rank']}\n"
        f"✅ Unlocked ➤ {data['done']}/{data['total']}\n"
        f"⭐ Points ➤ {data['points']}/{data['total_points']}\n"
        f"{_bar(data['pct'])} {data['pct']}%</b></blockquote>",
    ]
    nxt = data.get("next")
    if nxt:
        lines += ["", f"<b>🎯 Next ➤ {nxt['icon']} {nxt['title']}</b>",
                  f"<b>{_bar(nxt['pct'])} {nxt['current']}/{nxt['target']}</b>"]
    for ch in data["chapters"]:
        lines += ["", f"<b>{ch['icon']} {ch['name']} · {ch['done']}/{ch['total']}</b>"]
        row = []
        for it in ch["items"]:
            mark = "✅" if it["done"] else "🔒"
            tail = "" if it["done"] else f" <i>({it['current']}/{it['target']})</i>"
            row.append(f"{mark} {it['icon']} {it['title']}{tail}")
        lines.append("\n".join(row))
    text = "\n".join(lines)
    # Telegram limit: keep it comfortably below 4096 characters.
    if len(text) > 3900:
        lines = lines[:6]
        for ch in data["chapters"]:
            lines += ["", f"<b>{ch['icon']} {ch['name']} · {ch['done']}/{ch['total']}</b>"]
            lines.append("\n".join(("✅ " if it["done"] else "🔒 ") + it["title"] for it in ch["items"]))
        text = "\n".join(lines)[:4000]
    markup = None
    if str(chat.get("type", "")).lower().endswith("private"):
        markup = get_launch_keyboard(resolve_miniapp_url())
    await app.send_message(chat["id"], text, parse_mode="HTML", reply_markup=markup)
