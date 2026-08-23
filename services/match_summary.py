"""Image match-summary renderer.

The 1024x576 template and every drawing coordinate live here so the card can
be tuned without touching any game engine.  Both /play and /playint feed the
same renderer; the database number is deliberately read at send time.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from database.query import fetchval

TEMPLATE_PATH = Path(__file__).parent / "assets" / "match_summary_template.png"
FONT_PATH = Path(__file__).parent / "fonts" / "Poppins-Medium.ttf"
BOLD_FONT_PATH = Path(__file__).parent / "fonts" / "Poppins-Bold.ttf"

# Edit these values to move or resize any part of the card. Coordinates are
# based on the bundled 1024x576 template and scale automatically.
CANVAS = (1024, 576)
COORDINATES = {
    "title": (300, 8, 730, 58),
    "innings_1_header": (40, 61, 984, 109),
    "innings_1_overs": (735, 61, 850, 109),
    "innings_1_score": (855, 61, 975, 109),
    "innings_1_batting": (57, 112, 506, 266),
    "innings_1_bowling": (518, 112, 967, 266),
    "innings_2_header": (40, 278, 984, 326),
    "innings_2_overs": (735, 278, 850, 326),
    "innings_2_score": (855, 278, 975, 326),
    "innings_2_batting": (57, 329, 506, 463),
    "innings_2_bowling": (518, 329, 967, 463),
    "result": (40, 468, 984, 510),
    "potm": (40, 518, 984, 566),
}
COLORS = {
    "white": (245, 245, 245),
    "blue": (0, 105, 225),
    "green": (0, 177, 105),
    "gold": (241, 176, 8),
    "muted": (190, 190, 190),
}
FONT_SIZES = {
    "header": 24, "section": 16, "name": 17, "value": 17,
    "result": 18, "potm": 18, "title": 35,
}


def _font(size: int, bold: bool = False):
    return ImageFont.truetype(str(BOLD_FONT_PATH if bold else FONT_PATH), size)


def _fit(draw, text: str, box, size: int, bold: bool = False, minimum: int = 10):
    while size > minimum:
        f = _font(size, bold)
        b = draw.textbbox((0, 0), text, font=f)
        if b[2] - b[0] <= box[2] - box[0] and b[3] - b[1] <= box[3] - box[1]:
            return f
        size -= 1
    return _font(minimum, bold)


def _text(draw, text, box, color, size, *, bold=False, align="left"):
    f = _fit(draw, str(text), box, size, bold)
    b = draw.textbbox((0, 0), str(text), font=f)
    if align == "right":
        x = box[2] - (b[2] - b[0])
    elif align == "center":
        x = box[0] + ((box[2] - box[0]) - (b[2] - b[0])) // 2 - b[0]
    else:
        x = box[0]
    y = box[1] + ((box[3] - box[1]) - (b[3] - b[1])) // 2 - b[1]
    draw.text((x, y), str(text), font=f, fill=color)


def _team_name(value: Any) -> str:
    """Return a clean plain-text team name without unsupported emoji/symbols."""
    raw = str(value or "TEAM").replace("\n", " ").strip()
    cleaned = []
    for ch in raw:
        # Regional-indicator flags, pictographs, dingbats and symbol fonts can
        # render as tofu in the bundled Poppins font. Keep ordinary text and
        # punctuation, drop Unicode symbol characters entirely.
        import unicodedata
        if unicodedata.category(ch).startswith("So") or unicodedata.category(ch) in {"Sk", "Sm"}:
            continue
        if ch in {"\ufe0f", "\u200d"}:
            continue
        cleaned.append(ch)
    return "".join(cleaned).strip().upper() or "TEAM"


def _score(snap: dict) -> str:
    return f"{int(snap.get('runs') or 0)}/{int(snap.get('wickets') or 0)}"


def _figure(player: dict) -> str:
    return f"{int(player.get('wickets') or 0)}/{int(player.get('runs') or 0)}"


def _rows(draw, box, snap, kind, color):
    x0, y0, x1, y1 = box
    # These labels are already part of the supplied artwork. Redrawing them
    # here creates the doubled labels visible in the faulty output.
    rows = (snap.get("batters") if kind == "bat" else snap.get("bowlers")) or []
    rows = sorted(rows, key=(lambda p: int(p.get("runs") or 0)) if kind == "bat"
                  else (lambda p: (int(p.get("wickets") or 0), -int(p.get("runs") or 0))),
                  reverse=True)[:3]
    row_h = max(24, (y1 - y0 - 30) // 3)
    for i in range(3):
        y = y0 + 29 + i * row_h
        if i < len(rows):
            p = rows[i]
            name = str(p.get("name") or "Player")
            value = (f"{int(p.get('runs') or 0)} ({int(p.get('balls') or 0)})"
                     if kind == "bat" else f"{_figure(p)} ({int(p.get('balls') or 0) // 6}.{int(p.get('balls') or 0) % 6})")
            _text(draw, name, (x0, y, x1 - 105, y + row_h), COLORS["white"], FONT_SIZES["name"], bold=True)
            _text(draw, value, (x1 - 105, y, x1, y + row_h), COLORS["white"], FONT_SIZES["value"], align="right")


def render_match_summary(innings: list[dict], *, winner: str = "MATCH TIED",
                         margin: str = "", potm: dict | None = None,
                         match_number: int = 0) -> bytes:
    image = Image.open(TEMPLATE_PATH).convert("RGB")
    if image.size != CANVAS:
        image = image.resize(CANVAS)
    draw = ImageDraw.Draw(image)
    scale_x, scale_y = image.width / CANVAS[0], image.height / CANVAS[1]

    def box(key):
        x0, y0, x1, y1 = COORDINATES[key]
        return (round(x0 * scale_x), round(y0 * scale_y), round(x1 * scale_x), round(y1 * scale_y))

    # The bundled template already contains the static MATCH SUMMARY title and
    # 1ST/2ND INNINGS header labels. Do not draw them again here; doing so
    # creates the duplicated/overlapping text seen on the generated card.
    for idx in range(2):
        snap = innings[idx] if idx < len(innings) else {}
        accent = COLORS["blue"] if idx == 0 else COLORS["green"]
        header = box(f"innings_{idx+1}_header")
        _text(draw, _team_name(snap.get("batting_team_display")),
              (header[0], header[1], header[0] + 375, header[3]),
              COLORS["white"], FONT_SIZES["header"], bold=True)
        # Static innings labels are already part of the template. Only dynamic
        # team/over values are rendered by code so coordinates remain unchanged.
        _text(draw, f"{snap.get('over_text') or '0.0'} OVERS",
              box(f"innings_{idx+1}_overs"),
              COLORS["white"], FONT_SIZES["section"], bold=True, align="center")
        _text(draw, _score(snap),
              box(f"innings_{idx+1}_score"),
              COLORS["white"], FONT_SIZES["header"], bold=True, align="center")
        _rows(draw, box(f"innings_{idx+1}_batting"), snap, "bat", accent)
        _rows(draw, box(f"innings_{idx+1}_bowling"), snap, "bowl", accent)

    winner_text = f"{_team_name(winner)}  {margin}".strip().upper()
    _text(draw, winner_text, box("result"), COLORS["gold"], FONT_SIZES["result"], bold=True, align="center")
    potm = potm or {}
    details = f"{int(potm.get('runs') or 0)} ({int(potm.get('balls') or 0)})"
    if potm.get("wickets") is not None:
        details += f"  |  {_figure(potm)}"
    footer = f"# {int(match_number or 0)}     |     POTM     |     {potm.get('name') or '—'}     |     {details}"
    _text(draw, footer, box("potm"), COLORS["white"], FONT_SIZES["potm"], bold=True, align="center")
    out = BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


async def completed_match_count() -> int:
    """Count completed matches across every supported game engine."""
    total = 0
    for table in ("matches", "match_challenges", "play_matches", "playint_matches"):
        try:
            total += int(await fetchval(f"SELECT COUNT(*) FROM {table} WHERE status='completed';") or 0)
        except Exception:
            # Older installations may not have every optional engine table.
            continue
    return total


async def send_match_summary(app, chat_id: int, innings: list[dict], *,
                             winner: str, margin: str, potm: dict | None = None):
    card = render_match_summary(innings, winner=winner, margin=margin,
                                potm=potm, match_number=await completed_match_count() + 1)
    return await app.send_photo(chat_id, photo=card, caption="MATCH SUMMARY", parse_mode="HTML")


def player_details(innings: list[dict], name: str) -> dict:
    """Return the POTM's batting and bowling figures for the footer."""
    found = {"name": name or "—", "runs": 0, "balls": 0, "wickets": 0}
    for snap in innings:
        for player in (snap.get("batters") or []) + (snap.get("bowlers") or []):
            if str(player.get("name")) == str(name):
                found["runs"] = max(found["runs"], int(player.get("runs") or 0))
                found["balls"] = max(found["balls"], int(player.get("balls") or 0))
                found["wickets"] = max(found["wickets"], int(player.get("wickets") or 0))
    return found


def snapshot_normal_session(session) -> dict:
    """Adapt the legacy /match session's dictionaries to the shared schema."""
    innings = session.innings
    batters = [
        {"name": p.get("name"), "runs": int(v.get("runs") or 0),
         "balls": int(v.get("balls") or 0), "fours": int(v.get("fours") or 0),
         "sixes": int(v.get("sixes") or 0)}
        for p in (session.meta.get("batting_squad") or [])
        for v in [session.meta.get("batter_stats", {}).get(p.get("name"), {})]
        if p.get("name") in session.meta.get("batter_stats", {})
    ]
    bowlers = [
        {"name": name, "balls": int(v.get("balls") or 0),
         "runs": int(v.get("runs") or 0), "wickets": int(v.get("wickets") or 0)}
        for name, v in (session.meta.get("bowler_stats") or {}).items()
    ]
    return {
        "innings_number": innings.innings_number,
        "batting_team_id": session.meta.get("batting_team_id"),
        "bowling_team_id": session.meta.get("bowling_team_id"),
        "batting_team_display": session.meta.get("batting_display"),
        "bowling_team_display": session.meta.get("bowling_display"),
        "runs": int(innings.score.runs or 0),
        "wickets": int(innings.score.wickets or 0),
        "legal_balls": int(innings.score.legal_balls or 0),
        "over_text": innings.score.over_text,
        "batters": batters,
        "bowlers": bowlers,
    }


def best_player(innings: list[dict]) -> str:
    candidates = []
    for snap in innings:
        candidates.extend((p.get("name"), int(p.get("runs") or 0))
                          for p in snap.get("batters") or [])
        candidates.extend((p.get("name"), int(p.get("wickets") or 0) * 25 - int(p.get("runs") or 0) * 0.2)
                          for p in snap.get("bowlers") or [])
    return max(candidates, key=lambda item: item[1])[0] if candidates else "—"