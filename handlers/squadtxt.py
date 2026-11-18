"""Owner-only export of player / squad data into one TXT file.

    /squadtxt int-aus        -> Australia squad of the INT (T20I) engine
    /squadtxt int-all        -> every INT national team
    /squadtxt ipl-csk wpl-mi -> several scopes in one file
    /squadtxt ipl-all        -> every IPL franchise
    /squadtxt global         -> the global players table (Play engine / /player /claim pool)
    /squadtxt special        -> special edition players
    /squadtxt showcase       -> showcase cards
    /squadtxt all            -> everything above

Add ``raw`` to get the same data in the ``/upload_pl`` line format instead of the readable table,
and ``byname`` / ``bybat`` / ``bybowl`` / ``byrole`` to change the sort order.

Read-only: it only runs SELECT statements, nothing is written, nothing is cached.
"""
from __future__ import annotations

print("squadtxt.py loaded")

import html
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from app import app
from config import ADMIN_USER_ID
from database.players_repo import BOWLING_STYLE_LABELS
from database.playint_teams_repo import TEAM_MAP as INT_TEAMS, TEAMS_PAGE_1, TEAMS_PAGE_2
from database.playipl_teams_repo import TEAM_MAP as IPL_TEAMS, TEAM_ORDER as IPL_ORDER
from database.playwpl_teams_repo import TEAM_MAP as WPL_TEAMS, TEAM_ORDER as WPL_ORDER
from database.query import fetch
from handlers.registry import register
from services.tournament_virtual import is_virtual


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def _ordered(order, teams) -> list[str]:
    codes = [c for c in order if c in teams]
    return codes + [c for c in teams if c not in codes]


ENGINES = {
    "T20I": {"title": "INT (T20I)", "teams": INT_TEAMS, "order": _ordered(list(TEAMS_PAGE_1) + list(TEAMS_PAGE_2), INT_TEAMS), "nations": True},
    "IPL": {"title": "IPL", "teams": IPL_TEAMS, "order": _ordered(list(IPL_ORDER), IPL_TEAMS), "nations": False},
    "WPL": {"title": "WPL", "teams": WPL_TEAMS, "order": _ordered(list(WPL_ORDER), WPL_TEAMS), "nations": False},
}

ENGINE_WORDS = {
    "int": "T20I", "t20i": "T20I", "playint": "T20I", "intl": "T20I", "international": "T20I",
    "ipl": "IPL", "playipl": "IPL",
    "wpl": "WPL", "playwpl": "WPL",
}
KIND_WORDS = {
    "global": "global", "players": "global", "play": "global", "pl": "global", "gl": "global",
    "special": "special", "specials": "special", "sp": "special",
    "showcase": "showcase", "sc": "showcase",
}
ALL_WORDS = {"all", "everything", "full"}
MOD_WORDS = {"raw": "raw", "byname": "name", "bybat": "bat", "bybowl": "bowl", "byrole": "role"}
KIND_TITLES = {"global": "GLOBAL players", "special": "SPECIAL edition players", "showcase": "SHOWCASE cards"}

ROLE_ORDER = {"Wicketkeeper": 0, "Batsman": 1, "AllRounder": 2, "Bowler": 3}
BAT_ROLES = {"Batsman", "Wicketkeeper", "AllRounder"}
BOWL_ROLES = {"Bowler", "AllRounder"}
HAND_LABELS = {"RH": "Right-hand", "LH": "Left-hand"}
MAX_HINTS_PER_SECTION = 80
MAX_FILE_BYTES = 45 * 1024 * 1024

USAGE = (
    "📄 <b>/squadtxt</b> — owner-only player &amp; squad export (TXT file)\n\n"
    "<b>Engine</b> + <b>team</b> (use <code>-</code> or a space):\n"
    "• <code>/squadtxt int-aus</code> → Australia (INT)\n"
    "• <code>/squadtxt int-all</code> → all INT teams\n"
    "• <code>/squadtxt ipl-csk</code> · <code>/squadtxt ipl-all</code>\n"
    "• <code>/squadtxt wpl-mi</code> · <code>/squadtxt wpl-all</code>\n"
    "• <code>/squadtxt global</code> → global players\n"
    "• <code>/squadtxt special</code> · <code>/squadtxt showcase</code>\n"
    "• <code>/squadtxt all</code> → everything\n"
    "• Several at once: <code>/squadtxt int-ind int-aus ipl-all</code>\n\n"
    "<b>Extras</b>: <code>raw</code> = /upload_pl line format · "
    "<code>byname</code> <code>bybat</code> <code>bybowl</code> <code>byrole</code> = sort order\n"
    "Example: <code>/squadtxt ipl-all bybat</code>"
)


# ---------------------------------------------------------------------------------------------- parsing
def _team_lookup(engine: str) -> dict[str, str]:
    """token -> team code. Accepts the code (AUS) and a one-word full name (australia)."""
    out: dict[str, str] = {}
    for code, name in ENGINES[engine]["teams"].items():
        out[code.lower()] = code
        squashed = re.sub(r"[^a-z0-9]", "", str(name).lower())
        out.setdefault(squashed, code)
    return out


def _engines_with_team(token: str) -> list[tuple[str, str]]:
    found = []
    for engine in ENGINES:
        code = _team_lookup(engine).get(token)
        if code:
            found.append((engine, code))
    return found


def parse_request(text: str) -> dict:
    """Turn the text after the command into scopes. Never raises."""
    parts = str(text or "").split(None, 1)
    body = parts[1] if len(parts) > 1 else ""
    body = re.sub(r"\bminus\b", "-", body, flags=re.IGNORECASE)
    tokens = [t for t in re.split(r"[\s\-_:/,;+|]+", body.strip().lower()) if t]

    engines: dict[str, list[str] | None] = {}      # None = every team
    named: dict[str, list[str]] = {}
    kinds: list[str] = []
    mods: set[str] = set()
    errors: list[str] = []
    current: str | None = None

    def add_kind(kind: str) -> None:
        if kind not in kinds:
            kinds.append(kind)

    for tok in tokens:
        if tok in MOD_WORDS:
            mods.add(MOD_WORDS[tok])
            continue
        if tok in ENGINE_WORDS:
            current = ENGINE_WORDS[tok]
            engines.setdefault(current, [])
            named.setdefault(current, [])
            continue
        if tok in KIND_WORDS:
            add_kind(KIND_WORDS[tok])
            current = None
            continue
        if tok in ALL_WORDS:
            if current:
                engines[current] = None
            else:
                for engine in ENGINES:
                    engines[engine] = None
                    named.setdefault(engine, [])
                for kind in ("global", "special", "showcase"):
                    add_kind(kind)
            continue
        if current:
            code = _team_lookup(current).get(tok)
            if code:
                if engines.get(current) is not None and code not in named[current]:
                    named[current].append(code)
                    engines[current] = named[current]
            else:
                errors.append(f"'{tok}' is not a team of {ENGINES[current]['title']} (valid: {', '.join(ENGINES[current]['order'])})")
            continue
        guess = _engines_with_team(tok)
        if len(guess) == 1:
            engine, code = guess[0]
            current = engine
            named.setdefault(engine, [])
            if engines.get(engine, []) is not None and code not in named[engine]:
                named[engine].append(code)
                engines[engine] = named[engine]
        elif len(guess) > 1:
            errors.append(f"'{tok}' exists in {' and '.join(ENGINES[e]['title'] for e, _ in guess)} - write e.g. {guess[0][0].lower().replace('t20i', 'int')}-{tok}")
        else:
            errors.append(f"don't understand '{tok}'")

    for engine in list(engines):
        if engines[engine] == []:
            engines[engine] = None            # "ipl" alone = every IPL team
    return {"engines": engines, "kinds": kinds, "mods": mods, "errors": errors, "empty": not tokens}


def describe_request(req: dict) -> str:
    bits = []
    for engine, codes in req["engines"].items():
        key = "int" if engine == "T20I" else engine.lower()
        bits.append(f"{key}-all" if codes is None else " ".join(f"{key}-{c.lower()}" for c in codes))
    bits.extend(req["kinds"])
    bits.extend(sorted(req["mods"]))
    return " ".join(bits)


# ---------------------------------------------------------------------------------------------- data
def _player(row: dict, **extra) -> dict:
    def num(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    out = {
        "id": row.get("player_id"),
        "name": str(row.get("name") or "").strip(),
        "country": str(row.get("country") or "").strip(),
        "role": str(row.get("role") or "").strip(),
        "bat": num(row.get("bat_level")),
        "bowl": num(row.get("bowl_level")),
        "hand": str(row.get("batting_hand") or "").strip().upper(),
        "style": str(row.get("bowling_hand") or "").strip().upper(),
    }
    out.update(extra)
    return out


_COLS = "player_id, name, country, role, bat_level, bowl_level, batting_hand, bowling_hand"


async def load_sections(req: dict) -> tuple[list[dict], list[str]]:
    """Read everything requested (SELECT only). Returns (sections, notes)."""
    sections: list[dict] = []
    notes: list[str] = []

    for engine, codes in req["engines"].items():
        info = ENGINES[engine]
        wanted = list(info["order"]) if codes is None else list(codes)
        rows = await fetch(
            f"SELECT {_COLS}, team_code FROM playint_players WHERE engine_key=$1 AND team_code = ANY($2::text[]) "
            "ORDER BY team_code, player_id;",
            engine, wanted,
        )
        by_team: dict[str, list[dict]] = {}
        for row in rows:
            row = dict(row)
            by_team.setdefault(str(row.get("team_code") or ""), []).append(_player(row))
        for code in wanted:
            sections.append({
                "kind": "team", "engine": engine, "code": code,
                "heading": f"[{engine}] {code} - {info['teams'][code]}",
                "team_name": info["teams"][code], "nations": info["nations"],
                "players": by_team.get(code, []),
            })
        if codes is None:
            extra = await fetch(
                "SELECT team_code, COUNT(*) AS n FROM playint_players WHERE engine_key=$1 AND team_code <> ALL($2::text[]) "
                "GROUP BY team_code ORDER BY team_code;",
                engine, list(info["teams"]),
            )
            virtual = sum(int(dict(r)["n"]) for r in extra if is_virtual(dict(r)["team_code"]))
            unknown = [(dict(r)["team_code"], int(dict(r)["n"])) for r in extra if not is_virtual(dict(r)["team_code"])]
            if virtual:
                notes.append(f"{info['title']}: {virtual} tournament (virtual team) copy rows were left out - they duplicate the real squads.")
            for code, n in unknown:
                notes.append(f"{info['title']}: {n} player row(s) use the unknown team code '{code}' and are not in this file.")

    if "global" in req["kinds"]:
        rows = await fetch(f"SELECT {_COLS} FROM players ORDER BY player_id;")
        sections.append({"kind": "global", "engine": "GLOBAL", "code": "", "heading": "[GLOBAL] All global players",
                         "players": [_player(dict(r)) for r in rows]})

    if "special" in req["kinds"]:
        rows = await fetch(
            "SELECT special_player_id AS player_id, name, edition, country, role, bat_level, bowl_level, batting_hand, bowling_hand "
            "FROM special_edition_players ORDER BY edition, special_player_id;"
        )
        groups: dict[str, list[dict]] = {}
        for row in rows:
            row = dict(row)
            groups.setdefault(str(row.get("edition") or "(no edition)"), []).append(_player(row, edition=str(row.get("edition") or "")))
        if not groups:
            sections.append({"kind": "special", "engine": "SPECIAL", "code": "", "heading": "[SPECIAL] (none)", "players": []})
        for edition, players in groups.items():
            sections.append({"kind": "special", "engine": "SPECIAL", "code": edition, "heading": f"[SPECIAL] {edition}", "players": players})

    if "showcase" in req["kinds"]:
        rows = await fetch(
            "SELECT c.showcase_card_id AS player_id, s.set_name, c.name, c.country, c.role, c.bat_level, c.bowl_level, "
            "c.batting_hand, c.bowling_hand FROM showcase_cards c JOIN showcase_sets s ON s.showcase_set_id = c.showcase_set_id "
            "ORDER BY s.set_name, c.showcase_card_id;"
        )
        groups = {}
        for row in rows:
            row = dict(row)
            groups.setdefault(str(row.get("set_name") or "(no set)"), []).append(_player(row))
        if not groups:
            sections.append({"kind": "showcase", "engine": "SHOWCASE", "code": "", "heading": "[SHOWCASE] (none)", "players": []})
        for set_name, players in groups.items():
            sections.append({"kind": "showcase", "engine": "SHOWCASE", "code": set_name, "heading": f"[SHOWCASE] {set_name}", "players": players})
    return sections, notes


# ---------------------------------------------------------------------------------------------- analysis
def _sorted(players: list[dict], mods: set[str]) -> list[dict]:
    if "name" in mods:
        return sorted(players, key=lambda p: p["name"].lower())
    if "bat" in mods:
        return sorted(players, key=lambda p: (-p["bat"], p["name"].lower()))
    if "bowl" in mods:
        return sorted(players, key=lambda p: (-p["bowl"], p["name"].lower()))
    if "role" in mods:
        return sorted(players, key=lambda p: (ROLE_ORDER.get(p["role"], 9), -max(p["bat"], p["bowl"]), p["name"].lower()))
    return list(players)


def _avg(values: list[int]):
    return round(sum(values) / len(values), 1) if values else None


def section_stats(players: list[dict]) -> dict:
    roles = Counter(p["role"] for p in players)
    return {
        "n": len(players),
        "bat": roles.get("Batsman", 0), "wk": roles.get("Wicketkeeper", 0),
        "ar": roles.get("AllRounder", 0), "bowler": roles.get("Bowler", 0),
        "other": sum(v for k, v in roles.items() if k not in ROLE_ORDER),
        "avg_bat": _avg([p["bat"] for p in players if p["role"] in BAT_ROLES]),
        "avg_bowl": _avg([p["bowl"] for p in players if p["role"] in BOWL_ROLES]),
    }


def _who(p: dict) -> str:
    return f"{p['name'] or '(no name)'} (ID {p['id']})"


def section_hints(sec: dict, engine_names: dict[str, dict[str, list[str]]]) -> list[str]:
    """Automatic guesses about missing / wrong data. They are hints, not errors."""
    players = sec["players"]
    n = len(players)
    hints: list[str] = []
    if sec["kind"] == "team":
        if n == 0:
            return ["EMPTY team - no players found"]
        if n < 11:
            hints.append(f"only {n} player(s) - a Playing XI needs 11")
        if not any(p["role"] == "Wicketkeeper" for p in players):
            hints.append("no Wicketkeeper in this squad")
        options = sum(1 for p in players if p["role"] in BOWL_ROLES)
        if options < 5:
            hints.append(f"only {options} bowling option(s) (Bowler + AllRounder) - about 5 are needed")
    elif n == 0:
        return []

    seen: dict[str, list[dict]] = {}
    for p in players:
        seen.setdefault(p["name"].casefold(), []).append(p)
    for group in seen.values():
        if len(group) > 1:
            hints.append(f"duplicate name '{group[0]['name']}' (IDs {', '.join(str(g['id']) for g in group)})")

    mismatched = []
    for p in players:
        if not p["name"]:
            hints.append(f"player with ID {p['id']} has no name")
        if p["role"] not in ROLE_ORDER:
            hints.append(f"{_who(p)}: unknown role '{p['role']}'")
        if not (0 <= p["bat"] <= 100) or not (0 <= p["bowl"] <= 100):
            hints.append(f"{_who(p)}: level out of range (BAT {p['bat']}, BOWL {p['bowl']})")
        if p["role"] in ("Batsman", "Wicketkeeper") and p["bat"] < p["bowl"]:
            hints.append(f"{_who(p)}: {p['role']} but BAT {p['bat']} is lower than BOWL {p['bowl']} - level or role wrong?")
        if p["role"] == "Bowler" and p["bowl"] < p["bat"]:
            hints.append(f"{_who(p)}: Bowler but BOWL {p['bowl']} is lower than BAT {p['bat']} - level or role wrong?")
        if not p["country"]:
            hints.append(f"{_who(p)}: country is empty")
        if p["hand"] not in HAND_LABELS:
            hints.append(f"{_who(p)}: batting hand '{p['hand'] or '-'}' is missing/unknown")
        if p["style"] not in BOWLING_STYLE_LABELS:
            hints.append(f"{_who(p)}: bowling style '{p['style'] or '-'}' is missing/unknown")
        if sec.get("nations") and p["country"] and p["country"].casefold() != str(sec.get("team_name", "")).casefold():
            mismatched.append(p)

    if mismatched:
        if len(mismatched) >= max(3, int(n * 0.6)):
            top = Counter(p["country"] for p in mismatched).most_common(3)
            hints.append(f"{len(mismatched)} of {n} players have a country different from '{sec.get('team_name')}' "
                         f"(most common: {', '.join(f'{c} x{k}' for c, k in top)}) - spelling difference?")
        else:
            for p in mismatched:
                hints.append(f"{_who(p)}: country '{p['country']}' differs from team '{sec.get('team_name')}'")

    if sec["kind"] == "team":
        engine = sec["engine"]
        for p in players:
            others = [code for code, names in engine_names.get(engine, {}).items()
                      if code != sec["code"] and p["name"].casefold() in names]
            if others:
                where = ", ".join(engine + "-" + c for c in others)
                hints.append(f"{_who(p)}: the same name is also in {where}")
    return hints


def _engine_name_index(sections: list[dict]) -> dict[str, dict[str, list[str]]]:
    index: dict[str, dict[str, list[str]]] = {}
    for sec in sections:
        if sec["kind"] != "team":
            continue
        index.setdefault(sec["engine"], {})[sec["code"]] = [p["name"].casefold() for p in sec["players"]]
    return index


# ---------------------------------------------------------------------------------------------- rendering
def _table(headers: list[str], rows: list[list], right: set[int] | None = None) -> list[str]:
    right = right or set()
    rows = [[str(c) for c in r] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]

    def fmt(cells):
        out = []
        for i, cell in enumerate(cells):
            out.append(cell.rjust(widths[i]) if i in right else cell.ljust(widths[i]))
        return "  ".join(out).rstrip()

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(r) for r in rows)
    return lines


def _player_rows(players: list[dict], with_edition: bool = False) -> tuple[list[str], list[list]]:
    headers = ["#", "ID", "Name"] + (["Edition"] if with_edition else []) + ["Country", "Role", "Hand", "BAT", "BowlType", "BOWL", "Bowling style"]
    rows = []
    for i, p in enumerate(players, start=1):
        row = [i, p["id"], p["name"]]
        if with_edition:
            row.append(p.get("edition", ""))
        row += [p["country"] or "-", p["role"] or "-", p["hand"] or "-", p["bat"], p["style"] or "-", p["bowl"],
                BOWLING_STYLE_LABELS.get(p["style"], "-")]
        rows.append(row)
    return headers, rows


def build_report(req: dict, sections: list[dict], notes: list[str], now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    ist = now + timedelta(hours=5, minutes=30)
    mods = req["mods"]
    for sec in sections:
        sec["players"] = _sorted(sec["players"], mods)
    index = _engine_name_index(sections)
    total = sum(len(s["players"]) for s in sections)
    sort_text = {"name": "by name (A-Z)", "bat": "by BAT level (high-low)", "bowl": "by BOWL level (high-low)",
                 "role": "by role"}.get(next(iter(sorted(m for m in mods if m != "raw")), ""), "database order (same order the game shows)")

    out = ["=" * 100, " CRICKIUM - PLAYER DATA EXPORT", "=" * 100,
           f"Generated : {now:%Y-%m-%d %H:%M} UTC  |  {ist:%Y-%m-%d %H:%M} IST",
           f"Request   : {describe_request(req) or '-'}",
           f"Players   : {total} in {len(sections)} section(s)",
           f"Order     : {sort_text}"]
    for note in notes:
        out.append(f"Note      : {note}")
    out += ["", "LEGEND",
            "  ID         database id of the player (players / playint_players / special_edition_players / showcase_cards)",
            "  Hand       batting hand: RH right-hand, LH left-hand",
            "  BAT / BOWL batting and bowling level (0-100)",
            "  BowlType   RAF/LAF fast, RAM/LAM medium, RAO/LAO off break, RAL/LAL leg spin",
            "  AvgBAT     average BAT of Batsman + Wicketkeeper + AllRounder;  AvgBOWL = average BOWL of Bowler + AllRounder",
            "  HINTS      automatic guesses about missing / wrong data - they are NOT errors, check before you change anything",
            ""]

    stat_rows = []
    all_hints: list[tuple[str, list[str]]] = []
    for sec in sections:
        st = section_stats(sec["players"])
        hints = section_hints(sec, index)
        sec["_hints"] = hints
        if hints:
            all_hints.append((sec["heading"], hints))
        stat_rows.append([sec["heading"], st["n"], st["bat"], st["wk"], st["ar"], st["bowler"],
                          "-" if st["avg_bat"] is None else st["avg_bat"], "-" if st["avg_bowl"] is None else st["avg_bowl"], len(hints)])
    out += ["SUMMARY", *_table(["Section", "Players", "Bat", "WK", "AR", "Bowl", "AvgBAT", "AvgBOWL", "Hints"], stat_rows, right=set(range(1, 9))), ""]

    hint_total = sum(len(h) for _, h in all_hints)
    out.append(f"HINTS ({hint_total})")
    if not all_hints:
        out.append("  none - nothing suspicious found")
    for heading, hints in all_hints:
        out.append(f"  {heading}")
        for h in hints[:MAX_HINTS_PER_SECTION]:
            out.append(f"    - {h}")
        if len(hints) > MAX_HINTS_PER_SECTION:
            out.append(f"    ... and {len(hints) - MAX_HINTS_PER_SECTION} more hint(s) in this section")
    out.append("")

    for sec in sections:
        st = section_stats(sec["players"])
        out += ["=" * 100, f" {sec['heading']}   ({st['n']} players: {st['bat']} bat, {st['wk']} wk, {st['ar']} ar, {st['bowler']} bowl)", "=" * 100]
        if not sec["players"]:
            out += ["  (no players)", ""]
            continue
        headers, rows = _player_rows(sec["players"], with_edition=sec["kind"] == "special")
        out += _table(headers, rows, right={i for i, h in enumerate(headers) if h in ("#", "ID", "BAT", "BOWL")})
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _upload_line(p: dict, edition: bool = False) -> str:
    name = f"{p['name']} ({p['edition']})" if edition and p.get("edition") else p["name"]
    return f"[{name}][{p['country']}][{p['role']}][{p['hand'] or 'RH'}-BAT {p['bat']}][{p['style'] or 'RAM'} {p['bowl']}]"


def build_raw(req: dict, sections: list[dict]) -> tuple[str, list[str]]:
    """/upload_pl-compatible text. Returns (text, remarks)."""
    remarks: list[str] = []
    mods = req["mods"]
    only_showcase = all(s["kind"] == "showcase" for s in sections) and bool(sections)
    ordered = ([s for s in sections if s["kind"] == "global"] + [s for s in sections if s["kind"] == "special"]
               + [s for s in sections if s["kind"] == "team"] + [s for s in sections if s["kind"] == "showcase"])
    out: list[str] = []
    for sec in ordered:
        players = _sorted(sec["players"], mods)
        if not players:
            continue
        if sec["kind"] == "showcase":
            if not only_showcase:
                remarks.append("showcase cards are not in the raw file (they need their own file: /squadtxt showcase raw, then /upload_pl showcase)")
                continue
            out.append(f"SHOWCASE: {sec['code']}")
        elif sec["kind"] == "team":
            out.append(f"{sec['engine']}-{sec['code']}")
        out.extend(_upload_line(p, edition=sec["kind"] == "special") for p in players)
        out.append("")
    return "\n".join(out).rstrip() + "\n", sorted(set(remarks))


# ---------------------------------------------------------------------------------------------- command
def _safe_filename(label: str, raw: bool, now: datetime) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:60] or "export"
    return f"crickium_{slug}_{now:%Y%m%d_%H%M}{'_upload-format' if raw else ''}.txt"


@register("squadtxt")
@register("exportsquad")
@register("exportpl")
async def squadtxt_command(message):
    chat_id = (message.get("chat") or {}).get("id")
    user_id = int((message.get("from") or {}).get("id") or 0)
    if user_id != int(ADMIN_USER_ID):
        await app.send_message(chat_id, "🚫 This command is restricted to the bot owner.")
        return

    req = parse_request(message.get("text") or "")
    if req["empty"]:
        await app.send_message(chat_id, USAGE, parse_mode="HTML")
        return
    if req["errors"]:
        shown = "\n".join(f"• {_esc(e)}" for e in req["errors"][:8])
        await app.send_message(chat_id, f"⚠️ <b>Could not understand the request</b>\n{shown}\n\nSend <code>/squadtxt</code> alone to see the format.", parse_mode="HTML")
        return
    if not req["engines"] and not req["kinds"]:
        await app.send_message(chat_id, "⚠️ Tell me what to export, e.g. <code>/squadtxt int-aus</code>, <code>/squadtxt ipl-all</code> or <code>/squadtxt global</code>.", parse_mode="HTML")
        return

    try:
        sections, notes = await load_sections(req)
    except Exception as exc:
        print(f"[squadtxt] database read failed: {exc!r}")
        await app.send_message(chat_id, f"🚫 Could not read the database: <code>{_esc(str(exc)[:300])}</code>", parse_mode="HTML")
        return

    total = sum(len(s["players"]) for s in sections)
    label = describe_request(req)
    if total == 0:
        await app.send_message(chat_id, f"ℹ️ No players found for <code>{_esc(label)}</code>.", parse_mode="HTML")
        return

    now = datetime.now(timezone.utc)
    raw = "raw" in req["mods"]
    remarks: list[str] = []
    try:
        if raw:
            text, remarks = build_raw(req, sections)
        else:
            text = build_report(req, sections, notes, now)
    except Exception as exc:
        print(f"[squadtxt] build failed: {exc!r}")
        await app.send_message(chat_id, f"🚫 Could not build the file: <code>{_esc(str(exc)[:300])}</code>", parse_mode="HTML")
        return

    data = text.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        await app.send_message(chat_id, "⚠️ The file would be too large for Telegram. Export fewer teams at a time.")
        return

    hint_count = sum(len(s.get("_hints") or []) for s in sections)
    caption = [f"📄 <b>Player export</b> • <code>{_esc(label)}</code>",
               f"👥 {total} players • 📂 {len(sections)} section(s)"]
    if raw:
        caption.append("🔁 /upload_pl format (IPL/WPL upload updates levels; INT/global/special skip players that already exist)")
    else:
        caption.append(f"💡 {hint_count} hint(s) listed at the top of the file")
    caption.extend(f"ℹ️ {_esc(r)}" for r in remarks)
    caption.extend(f"ℹ️ {_esc(n)}" for n in notes[:3])
    try:
        await app.send_document(chat_id, data, filename=_safe_filename(label, raw, now), caption="\n".join(caption)[:1000], parse_mode="HTML")
    except Exception as exc:
        print(f"[squadtxt] send failed: {exc!r}")
        await app.send_message(chat_id, f"🚫 Telegram did not accept the file: <code>{_esc(str(exc)[:300])}</code>", parse_mode="HTML")
