"""Referral tracking and reward delivery for Crickium.

A referral becomes payable only after the referred user has:
1) started the bot through a referral deep-link,
2) completed /debut,
3) retained at least one /claim reward, and
4) completed at least one qualifying match.

Super-over-only matches are not used as the qualifying game signal.
"""
from __future__ import annotations

import html
import json
from urllib.parse import quote_plus

from database.query import fetch, fetchrow, fetchval, transaction

MAX_REFERRAL_REWARDS = 50

# The user explicitly specified the early/mid tiers. For the later milestones
# where only the milestone numbers were supplied, the amounts below continue
# the established +500,000 coins / +250 rubies pattern from the 15th tier.
# Keeping the table centralized makes those later amounts easy to change later
# without touching referral logic.
REFERRAL_TIERS: dict[int, dict] = {
    1: {"coins": 50_000, "rubies": 50, "players": []},
    2: {"coins": 50_000, "rubies": 50, "players": []},
    3: {"coins": 100_000, "rubies": 100, "players": []},
    4: {"coins": 100_000, "rubies": 100, "players": []},
    5: {"coins": 200_000, "rubies": 250, "players": [{"kind": "global", "min": 80, "max": 89}]},
    6: {"coins": 200_000, "rubies": 250, "players": [{"kind": "global", "min": 80, "max": 89}]},
    7: {"coins": 200_000, "rubies": 250, "players": [{"kind": "global", "min": 80, "max": 89}]},
    8: {"coins": 400_000, "rubies": 400, "players": [{"kind": "special", "min": 90, "max": 98}]},
    9: {"coins": 400_000, "rubies": 400, "players": [{"kind": "special", "min": 90, "max": 98}]},
    10: {"coins": 700_000, "rubies": 650, "players": [{"kind": "special", "min": 0, "max": 96}]},
    15: {
        "coins": 1_200_000,
        "rubies": 900,
        "players": [
            {"kind": "global", "min": 90, "max": 94},
            {"kind": "special", "min": 90, "max": 98},
        ],
    },
    20: {
        "coins": 1_700_000,
        "rubies": 1_150,
        "players": [
            {"kind": "global", "min": 90, "max": 94},
            {"kind": "special", "min": 90, "max": 98},
        ],
    },
    25: {
        "coins": 2_200_000,
        "rubies": 1_400,
        "players": [
            {"kind": "global", "min": 90, "max": 94},
            {"kind": "special", "min": 90, "max": 98},
        ],
    },
    30: {
        "coins": 2_700_000,
        "rubies": 1_650,
        "players": [
            {"kind": "global", "min": 90, "max": 94},
            {"kind": "special", "min": 90, "max": 98},
        ],
    },
    40: {
        "coins": 3_700_000,
        "rubies": 2_150,
        "players": [
            {"kind": "global", "min": 90, "max": 94},
            {"kind": "special", "min": 90, "max": 98},
        ],
    },
    50: {
        "coins": 4_700_000,
        "rubies": 2_650,
        "players": [
            {"kind": "global", "min": 90, "max": 94},
            {"kind": "special", "min": 90, "max": 98},
        ],
    },
}

MILESTONE_LEVELS = tuple(sorted(REFERRAL_TIERS))


def tier_for_referral(number: int) -> dict:
    number = int(number)
    if number <= 0:
        raise ValueError("Referral number must be positive")
    if number in REFERRAL_TIERS:
        return dict(REFERRAL_TIERS[number])
    # Between milestones, the amount repeats but no new player is granted.
    earlier = max((n for n in MILESTONE_LEVELS if n < number), default=1)
    tier = dict(REFERRAL_TIERS[earlier])
    tier["players"] = []
    return tier


def reward_label(number: int) -> str:
    tier = tier_for_referral(number)
    return f"🪙 {int(tier['coins']):,} Coins • 💎 {int(tier['rubies']):,} Rubies"


def build_referral_prize_structure() -> str:
    lines: list[str] = []

    def _milestone_line(idx: int) -> str:
        tier = REFERRAL_TIERS[idx]
        player_bits = []
        for spec in tier["players"]:
            kind = str(spec["kind"])
            if kind == "special" and idx == 10:
                player_bits.append("+ 1 random Special Edition player (level ≤96)")
            elif kind == "special":
                player_bits.append(f"+ 1 random Special Edition player (level {spec['min']}-{spec['max']})")
            else:
                player_bits.append(f"+ 1 random non-special player (level {spec['min']}-{spec['max']})")
        extra = " " + " ".join(player_bits) if player_bits else ""
        return f"{idx}. {int(tier['coins']):,} Coins + {int(tier['rubies']):,} Rubies{extra}"

    for idx in range(1, 11):
        lines.append(_milestone_line(idx))
    lines.append("11-14. Same 700,000 Coins + 650 Rubies, no additional player")
    lines.append(_milestone_line(15))
    lines.append("16-19. Same 1,200,000 Coins + 900 Rubies, no additional player")
    lines.append(_milestone_line(20))
    lines.append("21-24. Same 1,700,000 Coins + 1,150 Rubies, no additional player")
    lines.append(_milestone_line(25))
    lines.append("26-29. Same 2,200,000 Coins + 1,400 Rubies, no additional player")
    lines.append(_milestone_line(30))
    lines.append("31-39. Same 2,700,000 Coins + 1,650 Rubies, no additional player")
    lines.append(_milestone_line(40))
    lines.append("41-49. Same 3,700,000 Coins + 2,150 Rubies, no additional player")
    lines.append(_milestone_line(50))
    return "\n".join(lines)


def build_referral_message(referral_url: str) -> str:
    return (
        "<b>╭━━〔 🎁 REFERRAL REWARDS 〕━━╮</b>\n\n"
        "<b>Invite friends and earn rewards when they finish the full Crickium onboarding path.</b>\n\n"
        "<blockquote expandable>"
        "<b>✅ HOW TO EARN A REFERRAL REWARD</b>\n\n"
        "1. Your friend must open your referral link and press /start.\n"
        "2. They must complete /debut.\n"
        "3. They must complete at least one /claim player assignment.\n"
        "4. They must complete at least one regular game. Super Over-only play does not count.\n\n"
        "⚠️ The referred user must be new: an existing database user or an existing debut squad makes the referral ineligible."
        "</blockquote>\n\n"
        "<blockquote expandable>"
        f"<b>🎯 PRIZE STRUCTURE</b>\n{html.escape(build_referral_prize_structure())}"
        "</blockquote>\n\n"
        f"<blockquote><b>🔗 YOUR REFERRAL LINK</b>\n<code>{html.escape(referral_url)}</code></blockquote>\n\n"
        "<i>The reward is credited automatically to your account after the referred user completes every required step.</i>\n\n"
        "<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>"
    )


async def create_referral(referrer_id: int, referred_id: int) -> bool:
    referrer_id = int(referrer_id)
    referred_id = int(referred_id)
    if referrer_id <= 0 or referred_id <= 0 or referrer_id == referred_id:
        return False

    async def _tx(conn):
        referrer = await conn.fetchval("SELECT 1 FROM users WHERE user_id=$1;", referrer_id)
        if not referrer:
            return False
        return bool(await conn.fetchval(
            """
            INSERT INTO referrals (referrer_id, referred_id, status)
            VALUES ($1,$2,'pending')
            ON CONFLICT (referred_id) DO NOTHING
            RETURNING referred_id;
            """,
            referrer_id, referred_id,
        ))

    return bool(await transaction(_tx))


async def _qualification_flags(conn, user_id: int) -> tuple[bool, bool, bool]:
    debut = await conn.fetchval("SELECT 1 FROM team_squads WHERE user_id=$1 LIMIT 1;", user_id) is not None
    claim = int(await conn.fetchval(
        "SELECT COUNT(*) FROM player_claims WHERE user_id=$1 AND status IN ('retained','released');", user_id,
    ) or 0) > 0
    game = int(await conn.fetchval(
        "SELECT COALESCE(matches,0) FROM player_stats WHERE user_id=$1 LIMIT 1;", user_id,
    ) or 0) > 0
    return debut, claim, game


async def refresh_referral_progress(user_id: int) -> dict | None:
    """Refresh a referred user's progress and pay the referral once eligible."""
    user_id = int(user_id)

    async def _tx(conn):
        row = await conn.fetchrow(
            "SELECT * FROM referrals WHERE referred_id=$1 FOR UPDATE;", user_id,
        )
        if not row:
            return None
        if row["status"] == "completed":
            return dict(row)

        debut_done, claim_done, game_done = await _qualification_flags(conn, user_id)
        await conn.execute(
            """
            UPDATE referrals
               SET debut_completed=$2,
                   claim_completed=$3,
                   game_completed=$4,
                   updated_at=NOW()
             WHERE referred_id=$1;
            """,
            user_id, debut_done, claim_done, game_done,
        )

        if not (debut_done and claim_done and game_done):
            fresh = await conn.fetchrow("SELECT * FROM referrals WHERE referred_id=$1;", user_id)
            return dict(fresh) if fresh else None

        referrer_id = int(row["referrer_id"])
        completed_count = int(await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id=$1 AND status='completed';", referrer_id,
        ) or 0)
        reward_number = completed_count + 1
            # Lock the referrer wallet row before calculating the next reward number.
        # This serializes concurrent referral completions for the same inviter.
        user_row = await conn.fetchrow("SELECT balance, rubies FROM users WHERE user_id=$1 FOR UPDATE;", referrer_id)
        if not user_row:
            return dict(row)

        if reward_number > MAX_REFERRAL_REWARDS:
            await conn.execute(
                "UPDATE referrals SET status='completed', completed_at=NOW(), updated_at=NOW() WHERE referred_id=$1;",
                user_id,
            )
            return dict(await conn.fetchrow("SELECT * FROM referrals WHERE referred_id=$1;", user_id))

        tier = tier_for_referral(reward_number)

        # Lock the recipient wallet and squad so a retry cannot double-credit
        # the same referral or race another squad update.
        user_row = await conn.fetchrow("SELECT balance, rubies FROM users WHERE user_id=$1 FOR UPDATE;", referrer_id)
        if not user_row:
            return dict(row)
        squad_row = await conn.fetchrow("SELECT squad FROM team_squads WHERE user_id=$1 FOR UPDATE;", referrer_id)
        if not squad_row:
            return dict(row)
        squad = squad_row["squad"]
        if isinstance(squad, str):
            squad = json.loads(squad)
        else:
            squad = list(squad or [])
        if len(squad) + len(tier["players"]) > 25:
            # Preserve the referral as fully qualified but leave payout pending
            # until the inviter has room to receive the player cards.
            return dict(row)

        reward_players = []
        used_ids = {int(p.get("player_id") or 0) for p in squad if isinstance(p, dict)}
        for spec in tier["players"]:
            kind = spec["kind"]
            if kind == "special":
                p = await conn.fetchrow(
                    """
                    SELECT * FROM special_edition_players
                     WHERE GREATEST(COALESCE(bat_level,0), COALESCE(bowl_level,0)) BETWEEN $1 AND $2
                     ORDER BY random() LIMIT 1;
                    """,
                    int(spec["min"]), int(spec["max"]),
                )
                if p:
                    d = dict(p)
                    d.update({
                        "is_special": True,
                        "special_edition_id": int(d["special_player_id"]),
                        "player_id": -int(d["special_player_id"]),
                    })
                else:
                    d = None
            else:
                p = await conn.fetchrow(
                    """
                    SELECT * FROM players
                     WHERE GREATEST(COALESCE(bat_level,0), COALESCE(bowl_level,0)) BETWEEN $1 AND $2
                     ORDER BY random() LIMIT 1;
                    """,
                    int(spec["min"]), int(spec["max"]),
                )
                d = dict(p) if p else None
                if d:
                    d["is_special"] = False
                    d["edition"] = None
                    d["special_edition_id"] = None
            if not d:
                # No matching player in the current database means the reward
                # stays pending rather than crediting a partial tier.
                return dict(row)
            pid = int(d["player_id"])
            if pid in used_ids:
                return dict(row)
            used_ids.add(pid)
            reward_players.append(d)

        squad.extend(reward_players)
        await conn.execute(
            "UPDATE team_squads SET squad=$1::jsonb, updated_at=NOW() WHERE user_id=$2;",
            json.dumps(squad, default=str), referrer_id,
        )
        for player in reward_players:
            await conn.execute(
                "DELETE FROM player_user_match_stats WHERE user_id=$1 AND player_id=$2;",
                referrer_id, int(player["player_id"]),
            )

        await conn.execute(
            """
            UPDATE users
               SET balance = COALESCE(balance,0) + $1,
                   rubies = COALESCE(rubies,0) + $2,
                   last_seen_at = NOW()
             WHERE user_id=$3;
            """,
            int(tier["coins"]), int(tier["rubies"]), referrer_id,
        )
        await conn.execute(
            """
            UPDATE referrals
               SET status='completed',
                   debut_completed=TRUE,
                   claim_completed=TRUE,
                   game_completed=TRUE,
                   completed_at=NOW(),
                   reward_number=$2,
                   reward_coins=$3,
                   reward_rubies=$4,
                   reward_players=$5::jsonb,
                   updated_at=NOW()
             WHERE referred_id=$1;
            """,
            user_id,
            reward_number,
            int(tier["coins"]),
            int(tier["rubies"]),
            json.dumps([{
                "player_id": int(p["player_id"]),
                "name": p.get("name"),
                "is_special": bool(p.get("is_special")),
                "edition": p.get("edition"),
                "level": max(int(p.get("bat_level") or 0), int(p.get("bowl_level") or 0)),
            } for p in reward_players], default=str),
        )
        return dict(await conn.fetchrow("SELECT * FROM referrals WHERE referred_id=$1;", user_id))

    return await transaction(_tx)


async def get_my_referrals(referrer_id: int) -> list[dict]:
    rows = await fetch(
        """
        SELECT r.referred_id, u.username, u.first_name, r.status, r.debut_completed, r.claim_completed, r.game_completed,
               r.reward_number, r.reward_coins, r.reward_rubies, r.reward_players,
               r.created_at, r.completed_at
          FROM referrals r
          JOIN users u ON u.user_id = r.referred_id
         WHERE r.referrer_id=$1
         ORDER BY r.created_at DESC, r.referred_id DESC;
        """,
        int(referrer_id),
    )
    return [dict(r) for r in rows]


def build_my_referrals_message(rows: list[dict]) -> str:
    completed = [r for r in rows if r.get("status") == "completed"]
    pending = [r for r in rows if r.get("status") != "completed"]
    chunks = [
        "<b>╭━━〔 👥 MY REFERRALS 〕━━╮</b>",
        "",
        f"✅ <b>Completed:</b> {len(completed)}",
        f"⏳ <b>Incomplete:</b> {len(pending)}",
        "",
    ]
    if completed:
        chunks.append("<blockquote expandable><b>✅ COMPLETED</b>")
        for r in completed:
            num = r.get("reward_number") or "?"
            chunks.append(f"• {html.escape(str(r.get("first_name") or r.get("username") or r["referred_id"]))} <code>{int(r["referred_id"])}</code> → Referral #{num} → {reward_label(int(num))}")
        chunks.append("</blockquote>")
    if pending:
        chunks.append("<blockquote expandable><b>⏳ INCOMPLETE</b>")
        for r in pending:
            steps = [
                "✅ Debut" if r.get("debut_completed") else "⬜ Debut",
                "✅ Claim" if r.get("claim_completed") else "⬜ Claim",
                "✅ Game" if r.get("game_completed") else "⬜ Game",
            ]
            chunks.append(f"• {html.escape(str(r.get("first_name") or r.get("username") or r["referred_id"]))} <code>{int(r["referred_id"])}</code> → {' • '.join(steps)}")
        chunks.append("</blockquote>")
    if not rows:
        chunks.append("<blockquote>You have no referral records yet. Send /refer to invite your friends.</blockquote>")
    chunks.append("\n<b>╰━━━━━━━━━━━━━━━━━━━━╯</b>")
    return "\n".join(chunks)


def share_url(referral_url: str, first_name: str) -> str:
    text = f"Join me on Crickium and unlock the referral rewards! 🎁"
    return "https://t.me/share/url?url=" + quote_plus(referral_url) + "&text=" + quote_plus(text)
