from app import app
from handlers.registry import register
from services.lineup_rich import send_lineup
from services.pitch_report_view import PITCH_INFO, build_pitch_menu, build_pitch_view
from services.pitch_stats import load_pitch


def _pitch_from(message) -> str | None:
    parts = str(message.get("text") or "").split(maxsplit=2)
    if len(parts) < 2:
        return None
    word = parts[1].strip().lower().replace("pitch", "").strip()
    return word if word in PITCH_INFO else ""


@register("pitchreport")
async def pitchreport_command(message):
    chat_id = message["chat"]["id"]
    thread_id = message.get("message_thread_id")
    pitch = _pitch_from(message)
    if pitch is None or pitch == "":
        view = build_pitch_menu()
        if pitch == "":
            await app.send_message(chat_id, "<b>⚠️ Unknown pitch.</b> Choose one from the list below.", parse_mode="HTML")
        await send_lineup(chat_id, view.rich(), view.plain(), message_thread_id=thread_id)
        return
    try:
        innings, results = await load_pitch(pitch)
    except Exception as exc:
        print(f"[pitchreport] load failed: {exc!r}")
        await app.send_message(chat_id, "<b>⚠️ Pitch data is unavailable right now. Please try again shortly.</b>", parse_mode="HTML")
        return
    view = build_pitch_view(pitch, innings, results)
    await send_lineup(chat_id, view.rich(), view.plain(), message_thread_id=thread_id)
