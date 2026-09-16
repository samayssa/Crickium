from __future__ import annotations

print("pxl.py loaded")

import html

from handlers.registry import register
from app import app
from engines.lineup_engine import load_current_xi
from database.captain_repo import get_captain_id
from utils.country_flags import flag_for
from utils.debut_gate import validate_playing_xi
from utils.PremiumEmoji import ovr_emoji_html
from utils.user_identity import get_user_identity, inline_identity


async def _captain_id(players: list[dict], user_id: int) -> int | None:
    captain_id = await get_captain_id(user_id)
    if captain_id is None:
        return None

    player_ids = {
        int(p.get("player_id") or 0)
        for p in players
    }

    return captain_id if captain_id in player_ids else None


def _overall(player: dict) -> int:
    return max(
        int(player.get("bat_level") or 0),
        int(player.get("bowl_level") or 0),
    )


def _team_ovr(xi: list[dict]) -> int:
    if not xi:
        return 0

    return int(
        round(
            sum(
                _overall(player)
                for player in xi
            )
            / len(xi)
        )
    )


def _line(
    player: dict,
    number: int,
    icon: str,
    captain_id: int | None,
) -> str:
    name = html.escape(
        str(
            player.get("name")
            or "Player"
        )
    )

    level = _overall(player)
    flag = flag_for(
        player.get("country")
    )

    cap = (
        " 🧢"
        if captain_id
        and int(
            player.get("player_id") or 0
        ) == captain_id
        else ""
    )

    return (
        f"{number}. "
        f"{name} • "
        f"{level} "
        f"{flag} "
        f"{icon}"
        f"{cap}"
    )


def _team_status(
    xi: list[dict],
) -> tuple[bool, str]:
    raw_batsmen = [
        p
        for p in xi
        if p.get("role") == "Batsman"
    ]

    keepers = [
        p
        for p in xi
        if str(
            p.get("role") or ""
        ) == "Wicketkeeper"
    ]

    keepers.extend(
        p
        for p in raw_batsmen
        if p.get("is_wicketkeeper")
    )

    if not keepers and len(raw_batsmen) == 5:
        keepers = [
            raw_batsmen[-1]
        ]

    keeper_ids = {
        int(
            p.get("player_id") or 0
        )
        for p in keepers
    }

    batsmen = [
        p
        for p in raw_batsmen
        if int(
            p.get("player_id") or 0
        ) not in keeper_ids
    ]

    allrounders = [
        p
        for p in xi
        if p.get("role") == "AllRounder"
    ]

    bowlers = [
        p
        for p in xi
        if p.get("role") == "Bowler"
    ]

    reasons = []

    if len(xi) < 11:
        reasons.append(
            f"need {11 - len(xi)} more "
            f"player"
            f"{'s' if 11 - len(xi) != 1 else ''}"
        )

    elif len(xi) > 11:
        reasons.append(
            f"remove {len(xi) - 11} "
            f"player"
            f"{'s' if len(xi) - 11 != 1 else ''}"
        )

    checks = [
        (
            "batsman",
            len(batsmen),
            3,
            4,
        ),
        (
            "wicket-keeper",
            len(keepers),
            1,
            2,
        ),
        (
            "all-rounder",
            len(allrounders),
            3,
            4,
        ),
        (
            "bowler",
            len(bowlers),
            3,
            4,
        ),
    ]

    for label, count, minimum, maximum in checks:
        if count < minimum:
            missing = minimum - count

            reasons.append(
                f"need {missing} more "
                f"{label}"
                f"{'s' if missing != 1 else ''}"
            )

        elif count > maximum:
            extra = count - maximum

            reasons.append(
                f"remove {extra} "
                f"{label}"
                f"{'s' if extra != 1 else ''}"
            )

    if not reasons:
        return True, "Valid"

    return (
        False,
        "Not Valid — "
        + ", ".join(reasons),
    )


def _render_pxl(
    xi: list[dict],
    team_identity: str,
    captain_id: int | None = None,
) -> str:

    raw_batsmen = [
        p
        for p in xi
        if p.get("role") == "Batsman"
    ]

    keepers = [
        p
        for p in xi
        if str(
            p.get("role") or ""
        ) == "Wicketkeeper"
    ]

    keepers.extend(
        p
        for p in raw_batsmen
        if p.get("is_wicketkeeper")
    )

    if not keepers and len(raw_batsmen) == 5:
        keepers = [
            raw_batsmen[-1]
        ]

    keeper_ids = {
        int(
            p.get("player_id") or 0
        )
        for p in keepers
    }

    batsmen = [
        p
        for p in raw_batsmen
        if int(
            p.get("player_id") or 0
        ) not in keeper_ids
    ]

    allrounders = [
        p
        for p in xi
        if p.get("role") == "AllRounder"
    ]

    bowlers = [
        p
        for p in xi
        if p.get("role") == "Bowler"
    ]

    lines = [
        "╭━━━〔 🏏 PLAYING XI 〕━━━╮",
        "",
        f"➤ {team_identity} • 👥 {len(xi)}/11",
        f"➤ {ovr_emoji_html()} Team OVR: {_team_ovr(xi)}",
        "",
        "<blockquote>🏏 Batsmen",
    ]

    # --------------------------------------------------------
    # BATSMEN
    # --------------------------------------------------------

    for i, player in enumerate(
        batsmen,
        start=1,
    ):
        lines.append(
            f"├ {_line(player, i, '🏏', captain_id)}"
        )

    lines.append(
        "</blockquote>"
    )

    # NO BLANK LINE HERE
    lines.append(
        "<blockquote>🧤 Wicket-Keeper"
    )

    # --------------------------------------------------------
    # WICKET-KEEPER
    # --------------------------------------------------------

    for i, player in enumerate(
        keepers,
        start=len(batsmen) + 1,
    ):
        prefix = (
            "╰"
            if i
            == (
                len(batsmen)
                + len(keepers)
            )
            else "├"
        )

        lines.append(
            f"{prefix} "
            f"{_line(player, i, '🧤', captain_id)}"
        )

    lines.append(
        "</blockquote>"
    )

    # NO BLANK LINE HERE
    lines.append(
        "<blockquote>🔄 All-Rounders"
    )

    # --------------------------------------------------------
    # ALL-ROUNDERS
    # --------------------------------------------------------

    start = (
        len(batsmen)
        + len(keepers)
        + 1
    )

    for idx, player in enumerate(
        allrounders,
        start=start,
    ):
        prefix = (
            "╰"
            if idx
            == (
                len(batsmen)
                + len(keepers)
                + len(allrounders)
            )
            else "├"
        )

        lines.append(
            f"{prefix} "
            f"{_line(player, idx, '🔄', captain_id)}"
        )

    lines.append(
        "</blockquote>"
    )

    # NO BLANK LINE HERE
    lines.append(
        "<blockquote>⚡ Bowlers"
    )

    # --------------------------------------------------------
    # BOWLERS
    # --------------------------------------------------------

    start = (
        len(batsmen)
        + len(keepers)
        + len(allrounders)
        + 1
    )

    for idx, player in enumerate(
        bowlers,
        start=start,
    ):
        prefix = (
            "╰"
            if idx == 11
            else "├"
        )

        lines.append(
            f"{prefix} "
            f"{_line(player, idx, '⚡', captain_id)}"
        )

    lines += [
        "</blockquote>",
        "",
        (
            f"➤ 📊 Team Status: "
            f"{html.escape(_team_status(xi)[1])}"
        ),
        "➤ 📋 Full Squad: /squad",
        "",
        "╰━━━━━━━━━━━━━━━━━━╯",
    ]

    return "\n".join(lines)


@register("pxl")
async def pxl_command(message):
    chat_id = message["chat"]["id"]

    from_user = message.get(
        "from",
        {},
    )

    user_id = from_user.get(
        "id"
    )

    first_name = from_user.get(
        "first_name"
    )

    xi = await load_current_xi(
        user_id
    ) or []

    xi = xi[:11]

    validate_playing_xi(
        xi
    )

    identity = await get_user_identity(
        user_id,
        first_name,
    )

    captain_id = await _captain_id(
        xi,
        user_id,
    )

    report = _render_pxl(
        xi,
        inline_identity(identity),
        captain_id,
    )

    await app.send_message(
        chat_id,
        report,
        parse_mode="HTML",
    )