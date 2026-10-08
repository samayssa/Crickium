from __future__ import annotations

import html
from io import BytesIO
from pathlib import Path
from typing import Any

from database.match_analysis_repo import mark_sent, reserve_report
from services.match_analysis.renderer import render_report
from utils.mentions import mention_name_only_html

ROOT = Path(__file__).resolve().parent
GENERATED = ROOT / "generated"


def _mention(user: dict[str, Any]) -> str:
    return mention_name_only_html(int(user.get("id") or 0), str(user.get("first_name") or "Player"))


def build_caption(payload: dict[str, Any], match_number: int) -> str:
    players = payload.get("players") or [{}, {}]
    one = _mention(players[0]) if len(players) > 0 else "Player 1"
    two = _mention(players[1]) if len(players) > 1 else "Player 2"
    status = str(payload.get("termination") or "completed").replace("_", " ").title()
    return (
        "<blockquote>"
        "<b>🏏 Full Game Analysis</b>\n\n"
        f"{one} <b>vs</b> {two}\n"
        f"📊 Match #{int(match_number)} • {html.escape(str(payload.get('game_name') or 'Game'))}\n"
        f"🧾 Status: {html.escape(status)}\n"
        "🧠 Complete score, momentum, strategy and player analysis."
        "</blockquote>"
    )


async def send_report(app: Any, payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        reserved, should_send = await reserve_report(
            engine=str(payload.get("engine") or "UNKNOWN"),
            source_match_id=int(payload.get("match_id") or 0),
            filename_prefix="Crickium",
            termination=str(payload.get("termination") or "completed"),
        )
        payload = dict(payload)
        payload["match_number"] = int(reserved["match_number"])
        payload["filename"] = str(reserved["filename"])
        if not should_send:
            return reserved

        content = render_report(payload)
        GENERATED.mkdir(parents=True, exist_ok=True)
        local_path = GENERATED / payload["filename"]
        local_path.write_text(content, encoding="utf-8")
        document = BytesIO(content.encode("utf-8"))
        document.name = payload["filename"]
        message = await app.send_document(
            int(payload.get("chat_id") or _chat_id_from_payload(payload)),
            document=document,
            filename=payload["filename"],
            caption=build_caption(payload, int(payload["match_number"])),
            parse_mode="HTML",
        )
        message_id = message.get("message_id") if isinstance(message, dict) else getattr(message, "message_id", None)
        await mark_sent(int(reserved["report_id"]), int(message_id) if message_id else None)
        _prune_generated()
        return {**reserved, "filename": payload["filename"], "message_id": message_id}
    except Exception as exc:
        print(f"[match_analysis] report send failed: {exc!r}")
        return None


def _chat_id_from_payload(payload: dict[str, Any]) -> int:
    return int(payload.get("chat_id") or 0)


def _prune_generated(limit: int = 20) -> None:
    try:
        files = sorted((p for p in GENERATED.glob("*.html") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[limit:]:
            try:
                path.unlink()
            except Exception:
                pass
    except Exception:
        pass
